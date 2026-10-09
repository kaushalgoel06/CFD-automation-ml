"""
runner.py

Main driver for the CFD parametric study.
Manages dynamic sweeps, case state recoveries, and execution tracking loops.
Uses task manager termination to handle license seats cleanly.
Dynamically routes case and data files into isolated geometry subfolders.
"""

import os
import time
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
import config

from geometry_manager import GeometryManager
from mesher import FluentMesher
from solver_setup import (
    configure_solver_physics,
    execute_tui,
    get_fluid_zone_names,
)
import reports

# Bounding Fluent to local interface
os.environ["REMOTING_SERVER_ADDRESS"] = "127.0.0.1"


class StallDetected(RuntimeError):
    """Raised when an iteration chunk takes far longer than the recent
    average, instead of letting the pipeline hang until someone manually
    interrupts it."""
    pass


class SolutionDivergedError(RuntimeError):
    """Raised when the fluid-zone max temperature stays pinned at (or above)
    the solver's clamp limit for several consecutive checks, indicating the
    solution has diverged rather than converged."""
    pass


class FlowFrozenError(RuntimeError):
    """
    Raised when a short post-initialization probe shows the volume-averaged
    velocity magnitude isn't moving at all across several iterations — the
    same symptom as flat 0.0000e+00 continuity/velocity residuals while
    energy/k/omega keep evolving. This is checked empirically (rather than
    by hunting for a specific hidden setting) because the underlying cause
    could be a frozen cell zone, a disabled Solve > Equations > Flow toggle,
    or something else — and this Fluent build doesn't expose a reliable
    Settings-API attribute to check for any of them directly.
    """
    pass


class UnverifiedBoundaryConditionError(RuntimeError):
    """Raised when a velocity-inlet or wall-heat-flux write can't be
    confirmed via read-back after both the Settings-API and TUI attempts —
    running a case on an unconfirmed BC would produce misleading results
    that look like a valid, converged case."""
    pass


def wait_for_fluent_processes_to_clear():
    """ Forcibly terminates lingering ANSYS processes and holds for socket clearing. """
    print("\n💥 [BRUTE FORCE CLEANUP] Executing Task Manager 'End Task' on all ANSYS processes...")
    targets = ['fluent.exe', 'cortex.exe', 'fl_mpi.exe']

    for target in targets:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    print("⏳ Targets terminated. Waiting 60 seconds for port & socket cleanup...")
    time.sleep(60)
    print("🚀 Cooldown complete! Proceeding.\n")


def load_completed_cases(case_dir):
    """
    Determines which (velocity, heat_flux) cases are already done by
    checking for the actual .cas.h5 output file in THIS geometry's own
    case_dir — not by reading results.csv.

    This fixes a real bug: results.csv was a single file SHARED across
    every geometry in the sweep, storing only (velocity, heat_flux) pairs
    with no column identifying which geometry produced them. As soon as
    geometry #1 logged its 20 rows, every subsequent geometry's
    load_completed_cases() saw those same 20 (V, HF) combinations already
    present and skipped all 20 of ITS OWN cases without ever running a
    single one — exactly what happened to 'colloq v5 dw': meshing
    succeeded (1,031,803 real cells), but every case was skipped, zero
    case files were written, and the run still printed [SUCCESS] and
    marked the geometry complete in project_state.json.

    Checking actual files in case_dir is inherently correct per-geometry,
    since case_dir itself is already unique per geometry (mesher.py
    creates 'cfd.<geometry_stem>/'). It's also more honest than trusting
    CSV rows in general: if report-definition extraction fails (as it has
    been) or the process crashes between iterating and logging a results
    row, the case file on disk still tells the truth about whether that
    specific case actually ran to completion.
    """
    completed = set()
    if not case_dir.exists():
        return completed

    for velocity in config.VELOCITIES:
        for heat_flux in config.HEAT_FLUXES:
            filename = config.CASE_NAME_FORMAT.format(
                prefix=config.CASE_PREFIX,
                velocity=velocity,
                heat_flux=heat_flux,
                dynamic_deg=15
            )
            case_file = case_dir / f"{filename}.cas.h5"
            if case_file.exists():
                completed.add((float(velocity), float(heat_flux)))

    return completed


def resolve_zone_name(solver_session, target_name):
    """
    Dynamically matches target boundary zone names in Fluent to handle
    underscore vs hyphen renaming variations (e.g., 'heat_wall' vs 'heat-wall').
    """
    try:
        wall_zones = list(solver_session.setup.boundary_conditions.wall.keys())
        inlet_zones = list(solver_session.setup.boundary_conditions.velocity_inlet.keys())
        all_zones = wall_zones + inlet_zones

        if target_name in all_zones:
            return target_name

        alt_name = target_name.replace("_", "-") if "_" in target_name else target_name.replace("-", "_")
        if alt_name in all_zones:
            print(f"ℹ️ Zone auto-corrected: '{target_name}' -> '{alt_name}'")
            return alt_name

        for zone in all_zones:
            if target_name.lower() in zone.lower() or alt_name.lower() in zone.lower():
                print(f"ℹ️ Zone matched via search: '{target_name}' -> '{zone}'")
                return zone
    except Exception:
        pass

    return target_name


def _read_back(obj, attr_path):
    """Reads a (possibly dotted) attribute path back off an object, WITHOUT
    unwrapping it — that's now _extract_number's job (see below). Returns
    the raw leaf object (or None if the path itself can't be walked), so
    callers get a single, more robust place to turn it into a number."""
    try:
        target = obj
        for part in attr_path.split("."):
            target = getattr(target, part)
        return target
    except Exception:
        return None


def _safe_dir(obj):
    try:
        return [n for n in dir(obj) if not n.startswith("_")]
    except Exception:
        return []


def _extract_number(obj, depth=0, max_depth=3):
    """
    Coerces a PyFluent Settings-API leaf object (or a plain value) into a
    float, trying several strategies in order since different leaf types
    expose their underlying numeric value differently:

      1. Already numeric — return directly.
      2. .get_state() — the standard Settings-API way to pull the raw
         Python value out of a settings object.
      3. .value — may itself be another wrapper object (hence the
         depth-limited recursion). This is exactly what happened on this
         build: a single hasattr(leaf, 'value') check found the attribute
         fine at write-time (triggering Fluent's own "newer syntax:
         .value = 1" deprecation notice, which confirmed
         .momentum.velocity_magnitude is the correct path) but a plain
         single-level unwrap at read-time wasn't enough.
      4. Callable — some proxies return the value when called.
      5. float(obj) directly, as a last resort.

    Returns None if nothing works, rather than raising — so a wrong-shape
    candidate (e.g. a 3-component velocity vector) is treated as "no match"
    instead of crashing a numeric comparison.
    """
    if obj is None or depth > max_depth:
        return None

    if isinstance(obj, (int, float)):
        return float(obj)

    get_state = getattr(obj, "get_state", None)
    if callable(get_state):
        try:
            state = get_state()
            if isinstance(state, (int, float)):
                return float(state)
            if isinstance(state, dict) and len(state) == 1:
                only_val = next(iter(state.values()))
                if isinstance(only_val, (int, float)):
                    return float(only_val)
        except Exception:
            pass

    if hasattr(obj, "value"):
        try:
            inner = obj.value
            if inner is not obj:
                result = _extract_number(inner, depth + 1, max_depth)
                if result is not None:
                    return result
        except Exception:
            pass

    if callable(obj):
        try:
            called = obj()
            if called is not obj:
                result = _extract_number(called, depth + 1, max_depth)
                if result is not None:
                    return result
        except Exception:
            pass

    try:
        return float(obj)
    except Exception:
        return None


def _to_float_or_none(val):
    """Safely coerces a read-back value to float via _extract_number."""
    return _extract_number(val)


# Candidate Settings-API attribute paths for velocity-inlet magnitude, tried
# in order. '.momentum.velocity_magnitude' is CONFIRMED correct — Fluent's
# own deprecation notice on the write path echoed back exactly this
# attribute path — and is listed first. The rest stay as fallbacks only in
# case a future build changes the confirmed path.
_VELOCITY_ATTR_CANDIDATES = [
    "momentum.velocity_magnitude",
    "velocity_magnitude",
    "momentum.velocity",
    "velocity",
    "v",
]


def _try_set_and_readback(obj, attr_path, value):
    """Attempts to set a (possibly dotted) attribute path to `value`,
    handling both raw and .value-wrapped Parameter styles, then reads it
    back (raw — the caller runs it through _to_float_or_none). Returns the
    raw read-back object, or None on any failure during the set itself."""
    try:
        target = obj
        parts = attr_path.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        leaf_name = parts[-1]
        leaf = getattr(target, leaf_name)
        if hasattr(leaf, "value"):
            leaf.value = value
        else:
            setattr(target, leaf_name, value)
        return _read_back(obj, attr_path)
    except Exception:
        return None


def apply_boundary_conditions(solver_session, inlet_name, velocity, wall_name, heat_flux):
    """
    Sets inlet velocity and wall heat flux, reading each back afterward to
    confirm it actually stuck.

    Velocity: tries several candidate Settings-API attribute paths (the
    correct one for this Fluent build/config is unknown — '.v' has never
    actually worked, in any run to date, always silently falling through
    to a TUI fallback that was itself malformed for a case with Energy +
    SST k-omega enabled, since it didn't answer the extra turbulence
    prompts Fluent asks for in that config). If no candidate works, this
    deliberately does NOT attempt the old free-text TUI fallback — a
    malformed multi-prompt interactive command can leave Fluent's TUI
    parser mid-dialog, corrupting how it interprets the next command too.
    Instead it dumps the inlet's real attribute tree so the correct path
    can be identified directly from this run's log.

    Wall heat flux: '.thermal.heat_flux' is confirmed working via the
    Settings API from an earlier successful run, so it stays the primary
    path, with a TUI fallback retained only for that BC (fewer, simpler
    prompts than velocity-inlet).

    Raises UnverifiedBoundaryConditionError if a value can't be confirmed,
    since running a case on an unverified BC produces misleading results
    that look like a valid, converged case.
    """
    resolved_inlet = resolve_zone_name(solver_session, inlet_name)
    resolved_wall = resolve_zone_name(solver_session, wall_name)

    # 1. Velocity Inlet
    inlet_bc = solver_session.setup.boundary_conditions.velocity_inlet[resolved_inlet]

    # Best-effort: explicitly select "Magnitude, Normal to Boundary" so the
    # velocity_magnitude value we're about to write is actually interpreted
    # as a scalar magnitude rather than left on whatever specification
    # method the case defaulted to. Non-fatal — if this fails, the
    # candidate loop below will still catch it via the read-back mismatch.
    try:
        inlet_bc.momentum.velocity_specification_method = "Magnitude, Normal to Boundary"
    except Exception as e:
        print(f"   (Could not set velocity_specification_method — proceeding anyway: {e})")

    confirmed_v = None
    working_attr = None
    for attr_path in _VELOCITY_ATTR_CANDIDATES:
        result = _try_set_and_readback(inlet_bc, attr_path, float(velocity))
        result_f = _to_float_or_none(result)
        if result_f is not None and abs(result_f - float(velocity)) < 1e-6:
            confirmed_v = result_f
            working_attr = attr_path
            break
        elif result is not None:
            print(f"   Candidate '.{attr_path}' accepted the write but read back as "
                  f"non-numeric or mismatched ({result!r}) — trying next candidate.")

    # Best-effort temperature set — not gating on this, since heat flux at
    # the wall is what actually drives the thermal problem, and we don't
    # want a temperature-attribute mismatch to mask a velocity success.
    _try_set_and_readback(inlet_bc, "t", 300.0)

    if confirmed_v is None:
        print(f"   Could not confirm inlet velocity via any of {_VELOCITY_ATTR_CANDIDATES}.")
        print(f"   Top-level attributes on velocity_inlet['{resolved_inlet}']:")
        print(f"   {sorted(_safe_dir(inlet_bc))}")
        for sub_name in ("momentum", "thermal", "turbulence"):
            sub_obj = getattr(inlet_bc, sub_name, None)
            if sub_obj is not None:
                print(f"   .{sub_name} attributes: {sorted(_safe_dir(sub_obj))}")
        raise UnverifiedBoundaryConditionError(
            f"Could not confirm velocity inlet '{resolved_inlet}' is set to {velocity} m/s "
            f"via any known Settings-API path. Skipped the TUI fallback deliberately (it's "
            f"malformed for a case with Energy + SST k-omega enabled and risks corrupting "
            f"the TUI dialog state for subsequent commands). See the attribute dump above "
            f"to identify the correct path for this build."
        )

    print(f"-> Confirmed velocity inlet '{resolved_inlet}' = {confirmed_v} m/s via '.{working_attr}' (300 K)")

    # 2. Wall Heat Flux
    try:
        wall_bc = solver_session.setup.boundary_conditions.wall[resolved_wall]
        wall_bc.thermal.thermal_condition = "Heat Flux"
        wall_bc.thermal.heat_flux.value = float(heat_flux)
        confirmed_q = _to_float_or_none(_read_back(wall_bc, "thermal.heat_flux"))
    except Exception:
        confirmed_q = None

    if confirmed_q is None or abs(confirmed_q - float(heat_flux)) > 1e-6:
        execute_tui(solver_session, f'/define/boundary-conditions/zone-type {resolved_wall} wall')
        execute_tui(solver_session, f'/define/boundary-conditions/wall {resolved_wall} yes heat-flux {heat_flux} no 0 no 1')
        try:
            wall_bc = solver_session.setup.boundary_conditions.wall[resolved_wall]
            confirmed_q = _to_float_or_none(_read_back(wall_bc, "thermal.heat_flux"))
        except Exception:
            confirmed_q = None

    if confirmed_q is None or abs(confirmed_q - float(heat_flux)) > 1e-6:
        wall_bc = solver_session.setup.boundary_conditions.wall[resolved_wall]
        print(f"   Top-level attributes on wall['{resolved_wall}']: {sorted(_safe_dir(wall_bc))}")
        thermal_obj = getattr(wall_bc, "thermal", None)
        if thermal_obj is not None:
            print(f"   .thermal attributes: {sorted(_safe_dir(thermal_obj))}")
        raise UnverifiedBoundaryConditionError(
            f"Could not confirm wall '{resolved_wall}' heat flux is set to {heat_flux} W/m² "
            f"after both Settings-API and TUI attempts (read back: {confirmed_q}). See the "
            f"attribute dump above. Refusing to run this case on an unverified boundary "
            f"condition."
        )
    print(f"-> Confirmed wall '{resolved_wall}' heat flux = {confirmed_q} W/m²")

    return resolved_inlet, resolved_wall


def run_initialization(solver_session, inlet_name="velocity_inlet"):
    """ Initializes field values and patches temperature to 300 K to prevent AMG solver divergence. """
    strategy_used = ""
    try:
        execute_tui(solver_session, f'/solve/initialize/compute-defaults/velocity-inlet {inlet_name}')
        execute_tui(solver_session, '/solve/initialize/initialize-flow')
        strategy_used = "Standard Initialization (Inlet Defaults)"
    except Exception:
        try:
            solver_session.solution.initialization.hybrid_initialize()
            strategy_used = "Hybrid Initialization API"
        except Exception:
            execute_tui(solver_session, "/solve/initialize/hyb-initialization")
            strategy_used = "Hybrid Initialization TUI"

    # Patch temperature field across all cell zones to 300 K to avoid 0 K AMG solver crash.
    # Previously this used '/solve/patch () () temperature 300', where the bare '()' zone/
    # register lists triggered 'rp-var-value: undefined variable' in this Fluent version.
    # Fixed to use explicit zone names, via the Settings API first and an explicit-name
    # TUI fallback second.
    try:
        try:
            solver_session.solution.initialization.patch(
                cell_zones=get_fluid_zone_names(solver_session) or None,
                variable="temperature",
                value=300,
            )
        except Exception:
            zone_names = get_fluid_zone_names(solver_session)
            if not zone_names:
                raise RuntimeError("No fluid zone names available for patch fallback.")
            zone_arg = " ".join(zone_names)
            execute_tui(solver_session, f"/solve/patch {zone_arg} () temperature 300")
        print("-> Temperature field patched to 300 K across all cell zones.")
    except Exception as e:
        print(f"⚠️ Patch notice: {e}")

    return strategy_used


def _verify_flow_equation_active(solver_session, probe_iterations=None):
    """
    Runs a short burst of iterations right after initialization and checks
    whether volume-averaged velocity magnitude actually changes. This is
    the empirical replacement for the old (unreliable on this Fluent build)
    Settings-API Frozen Flow check: it catches the SAME symptom — flow
    field not being solved — regardless of whether the cause is a frozen
    cell zone, a disabled Solve > Equations > Flow toggle, or anything else,
    and regardless of what this Fluent build exposes via the Settings API.

    Cheap by design: runs only `probe_iterations` (a handful) before
    checking, so a truly frozen case is caught in ~5 iterations instead of
    burning the full ITERATIONS_PER_CASE budget on a non-CFD solve.

    Raises FlowFrozenError if velocity stays at exactly 0.0 across the
    probe. If the velocity report itself is unavailable (e.g. this PyFluent
    version doesn't support the report-definition path used), prints a
    warning and skips the check rather than blocking the run on a metric
    that can't be computed.
    """
    probe_iterations = probe_iterations or getattr(config, "FLOW_PROBE_ITERATIONS", 5)

    v_before = reports.get_velocity_magnitude_report(solver_session)

    execute_tui(solver_session, f"/solve/iterate {probe_iterations}", raise_on_tui_error=False)

    v_after = reports.get_velocity_magnitude_report(solver_session)

    if v_before is None or v_after is None:
        print("⚠️ Flow-activity probe skipped (velocity-magnitude report unavailable this run) — "
              "proceeding without this safety check.")
        return probe_iterations

    delta = abs(v_after - v_before)
    print(f"-> Flow-activity probe: volume-avg |velocity| {v_before:.6g} -> {v_after:.6g} "
          f"(Δ={delta:.3g}) over {probe_iterations} iterations")

    if v_before == 0.0 and v_after == 0.0:
        raise FlowFrozenError(
            f"Volume-averaged velocity magnitude stayed at exactly 0.0 across "
            f"{probe_iterations} probe iterations after initialization. This matches "
            f"the flat continuity/velocity-residual failure mode seen previously — the "
            f"flow field is not being solved. Likely causes to check in the Fluent GUI: "
            f"Solve > Equations (confirm 'Flow' is checked/enabled), and Setup > Cell "
            f"Zone Conditions > (fluid zone) > Frozen Flow."
        )

    return probe_iterations


def _run_iterations_with_watchdog(solver_session, total_iterations, chunk_size=None,
                                   min_stall_timeout=None, stall_multiplier=None):
    """
    Runs `total_iterations` in small chunks instead of one giant blocking
    call, so a stalled/hung Fluent session raises StallDetected instead of
    requiring a manual Ctrl+C. Also checks solution health between chunks
    (see _check_not_diverged) and raises SolutionDivergedError early rather
    than burning the rest of the case's iterations on a clamped solution.

    IMPORTANT: on a timeout, the worker thread is still stuck inside a
    blocking Fluent call and may never return on its own. We deliberately
    do NOT block waiting for it to finish (shutdown(wait=False)) — the
    caller's wait_for_fluent_processes_to_clear() taskkill is what actually
    reclaims it. Using `with ThreadPoolExecutor(...)` here would silently
    defeat the whole point of this watchdog, since __exit__ calls
    shutdown(wait=True) and blocks forever on the same stuck call.
    """
    chunk_size = chunk_size or getattr(config, "ITER_CHUNK_SIZE", 50)
    min_stall_timeout = min_stall_timeout or getattr(config, "MIN_STALL_TIMEOUT_SECONDS", 900)
    stall_multiplier = stall_multiplier or getattr(config, "STALL_TIMEOUT_MULTIPLIER", 6)

    remaining = total_iterations
    chunk_durations = []

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        while remaining > 0:
            this_chunk = min(chunk_size, remaining)

            if chunk_durations:
                timeout = max(min_stall_timeout, stall_multiplier * statistics.median(chunk_durations))
            else:
                timeout = min_stall_timeout

            start = time.time()
            future = executor.submit(solver_session.tui.solve.iterate, this_chunk)
            try:
                future.result(timeout=timeout)
            except FutureTimeoutError:
                # Don't join the stuck worker thread — hand off to the
                # caller's process-kill cleanup instead.
                executor.shutdown(wait=False, cancel_futures=True)
                raise StallDetected(
                    f"Iteration chunk of {this_chunk} did not complete within "
                    f"{timeout:.0f}s (recent chunks averaged "
                    f"{statistics.median(chunk_durations) if chunk_durations else 'n/a'}s). "
                    f"Fluent likely stalled or is stuck on a license/socket issue."
                )

            elapsed = time.time() - start
            chunk_durations.append(elapsed)
            chunk_durations = chunk_durations[-5:]  # keep a short rolling window

            remaining -= this_chunk

            _check_not_diverged(solver_session)
    finally:
        # Non-blocking on the normal-completion path too — the pool has no
        # more work queued at this point, so this just releases resources
        # without risking a hang.
        executor.shutdown(wait=False)


def _check_not_diverged(solver_session, temp_limit_k=None, streak_limit=None):
    """
    Between iteration chunks, checks whether max fluid-zone temperature is
    pinned at (or above) the solver's clamp limit. If it stays pinned for
    several consecutive checks, raises SolutionDivergedError so the case is
    aborted before its (bad) case/data files get written — which is also
    what keeps it correctly un-skipped on a resumed run, since completion
    is now determined by the .cas.h5 file's presence in case_dir.
    """
    temp_limit_k = temp_limit_k or getattr(config, "TEMP_LIMIT_K", 5000.0)
    streak_limit = streak_limit or getattr(config, "DIVERGENCE_STREAK_LIMIT", 3)

    tmax = reports.get_max_temperature(solver_session)
    if tmax is None:
        return  # health check unsupported this run — degrade gracefully

    if tmax >= temp_limit_k - 1.0:
        _check_not_diverged._streak = getattr(_check_not_diverged, "_streak", 0) + 1
        if _check_not_diverged._streak >= streak_limit:
            raise SolutionDivergedError(
                f"Max fluid temperature has stayed at/above the {temp_limit_k:.0f} K "
                f"solver limiter for {_check_not_diverged._streak} consecutive checks. "
                f"Treating this case as diverged."
            )
    else:
        _check_not_diverged._streak = 0


def run_cases_for_geometry(solver_session, geometry_name, case_dir):
    """ Runs the parametric CFD combinations using the active solver instance. """
    completed_cases = load_completed_cases(case_dir)

    print()
    print("=" * 70)
    print(f"Processing Operating Cases for: {geometry_name}")
    print(f"Found {len(completed_cases)} completed case(s) in {case_dir.name}/ (checked via .cas.h5 files present).")
    print("=" * 70)

    total_cases = len(config.VELOCITIES) * len(config.HEAT_FLUXES)
    case_number = 1
    probe_already_run = False  # tracks whether the flow-activity probe has
                                # run yet THIS geometry — deliberately not
                                # tied to case_number == 1, since on a
                                # resumed run case 1 may already be done
                                # and skipped, in which case the probe
                                # still needs to run on whichever case is
                                # actually the first one executed.

    for velocity in config.VELOCITIES:
        for heat_flux in config.HEAT_FLUXES:

            if (float(velocity), float(heat_flux)) in completed_cases:
                print(f"Skipping Case {case_number}/{total_cases} (V={velocity:.1f} m/s, HF={heat_flux} W/m²)")
                case_number += 1
                continue

            print()
            print("-" * 60)
            print(f"Running Case {case_number}/{total_cases}")
            print(f"Velocity : {velocity:.1f} m/s")
            print(f"Heat Flux: {heat_flux} W/m²")
            print("-" * 60)

            # Reset the divergence-streak counter for each new case.
            _check_not_diverged._streak = 0

            try:
                # Safely update boundary condition values
                resolved_inlet, resolved_wall = apply_boundary_conditions(
                    solver_session,
                    config.REAL_INLET_NAME,
                    velocity,
                    config.REAL_WALL_NAME,
                    heat_flux
                )

                # Initialize on first run or when switching heat flux states
                if case_number == 1 or heat_flux == config.HEAT_FLUXES[0]:
                    print("Performing clean system initialization step...")
                    used_strategy = run_initialization(solver_session, resolved_inlet)
                    print(f"-> Initialization succeeded via: {used_strategy}")

                    # One-time, solver-wide sanity check per geometry (not
                    # per-case): confirm the flow field actually moves
                    # before committing to the full iteration budget. Cheap
                    # — only 'probe_iterations' iterations — and catches a
                    # frozen/disabled flow field in ~5 iterations instead
                    # of 100.
                    if not probe_already_run:
                        probe_consumed = _verify_flow_equation_active(solver_session)
                        probe_already_run = True
                        remaining_iterations = max(0, config.ITERATIONS_PER_CASE - probe_consumed)
                    else:
                        remaining_iterations = config.ITERATIONS_PER_CASE
                else:
                    remaining_iterations = config.ITERATIONS_PER_CASE

                # Iteration execution — chunked, with stall + divergence watchdogs
                print("Running calculation iterations...")
                _run_iterations_with_watchdog(solver_session, remaining_iterations)

                # Save output case/data files (only reached if not stalled/diverged)
                filename = config.CASE_NAME_FORMAT.format(
                    prefix=config.CASE_PREFIX,
                    velocity=velocity,
                    heat_flux=heat_flux,
                    dynamic_deg=15
                )
                case_data_path = case_dir / filename

                solver_session.file.write(
                    file_type="case-data",
                    file_name=str(case_data_path),
                )

                # Extract telemetry metrics and actually persist them
                results = reports.export_case_results(
                    solver_session, velocity, heat_flux,
                    inlet_name=resolved_inlet, wall_name=resolved_wall,
                )
                print(f"\nResults Update [{geometry_name}]:")
                print(f"  Tin       : {results.get('tin')} K")
                print(f"  Tout      : {results.get('tout')} K")
                print(f"  Tmax      : {results.get('tmax')} K")
                print(f"  Mass Flow : {results.get('massflow')} kg/s")

                reports.write_case_result_to_csv(geometry_name, velocity, heat_flux, results)
                print("-> Result row appended to results.csv")

            except (StallDetected, SolutionDivergedError, FlowFrozenError,
                    UnverifiedBoundaryConditionError) as health_err:
                print("\n" + "=" * 70)
                print("CASE ABORTED — SOLVER HEALTH CHECK FAILED")
                print(f"Geometry  : {geometry_name}")
                print(f"Case Index: {case_number}")
                print(f"Condition : V={velocity} m/s, HF={heat_flux} W/m²")
                print(f"Reason    : {health_err}")
                print("This case was NOT written to disk and was NOT marked complete,")
                print("so it will be retried on the next run.")
                print("=" * 70)
                raise health_err

            except Exception as e:
                print("\n" + "=" * 70)
                print("SIMULATION EXCEPTION ENCOUNTERED - PIPELINE HALTED FOR ANALYSIS")
                print(f"Geometry  : {geometry_name}")
                print(f"Case Index: {case_number}")
                print(f"Condition : V={velocity} m/s, HF={heat_flux} W/m²")
                print(f"Error Log : {e}")
                print("=" * 70)
                raise e

            case_number += 1


def main():
    config.RESULT_FOLDER.mkdir(parents=True, exist_ok=True)
    geo_manager = GeometryManager()
    pending_geometries = geo_manager.get_remaining_geometries()

    if not pending_geometries:
        print("\nAll target geometries marked complete in project_state.json. Task done!")
        return

    for geo_path in pending_geometries:
        geo_name = geo_path.name
        live_solver_session = None

        try:
            wait_for_fluent_processes_to_clear()

            mesher_pipeline = FluentMesher()
            live_solver_session = mesher_pipeline.mesh_geometry(geo_path)
            case_dir = mesher_pipeline.current_case_dir

            print("Optimizing cell junctions on global mesh structure via solver registers...")
            execute_tui(live_solver_session, '/mesh/repair-improve/improve-quality')

            # Best-effort Frozen Flow check/reset (non-fatal on this build —
            # see solver_setup.ensure_flow_not_frozen docstring). The real
            # gate against a frozen/disabled flow field is the empirical
            # probe inside run_cases_for_geometry (FlowFrozenError below).
            configure_solver_physics(live_solver_session)

            run_cases_for_geometry(live_solver_session, geo_name, case_dir)

            geo_manager.mark_geometry_complete(geo_name)
            print(f"\n[SUCCESS] Completed entire execution block for {geo_name}.\n")

            try:
                live_solver_session.exit()
            except Exception:
                pass

            wait_for_fluent_processes_to_clear()

        except FlowFrozenError as ff_err:
            print("\n" + "=" * 70)
            print("GEOMETRY ABORTED — FLOW FIELD NOT MOVING (FROZEN/DISABLED)")
            print(f"Geometry  : {geo_name}")
            print(f"Reason    : {ff_err}")
            print("This geometry was NOT marked complete, so it will be retried")
            print("(after manual investigation in the Fluent GUI) on the next run.")
            print("=" * 70)
            if live_solver_session is not None:
                try:
                    live_solver_session.exit()
                except Exception:
                    pass
            wait_for_fluent_processes_to_clear()
            return

        except Exception as master_err:
            print(f"\n❌ [PIPELINE CRASH] Aborting design run on configuration {geo_name}.")
            print(f"Reason: {master_err}")
            if live_solver_session is not None:
                try:
                    live_solver_session.exit()
                except Exception:
                    pass
            wait_for_fluent_processes_to_clear()
            return

    print("\n" + "=" * 70)
    print("ALL GEOMETRIES AND PARAMETRIC PATTERNS EXECUTED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()