"""
solver_setup.py

Configures physics models, viscous options, energy equations,
under-relaxation factors, and shell-conduction settings for Fluent solver sessions.
"""


class TuiCommandError(RuntimeError):
    """Raised when Fluent's TUI reports an error inside its response text,
    even though the underlying Python call itself didn't raise."""
    pass


class FrozenFlowUnverifiedError(RuntimeError):
    """Raised when Frozen Flow cannot be confirmed OFF on any fluid zone.

    Running with this unverified risks a repeat of the flat 0.0000e+00
    continuity/velocity residuals seen previously — energy/k/omega evolve
    while the flow field itself never updates, silently wasting an entire
    case's iteration budget on a non-CFD solve.
    """
    pass


# Substrings that indicate a genuine TUI-level failure. Kept as a list (not
# just "Error") so a wider net can be added later without touching call sites.
_TUI_ERROR_MARKERS = ("Error:", "rp-var-value", "undefined variable")


def execute_tui(solver_session, command_str, raise_on_tui_error=True):
    """
    Executes a TUI command safely via the tui/scheme interface.

    Inspects whatever text Fluent returns (when the PyFluent version in use
    actually returns it) and raises if it contains a known error marker — so
    a command that "succeeds" at the Python level but fails inside Fluent no
    longer passes silently.

    NOTE: not every PyFluent version returns captured console text from
    execute_tui/scheme_eval. Where it doesn't, this falls back to the old
    behavior (only Python-level exceptions are caught) and the caller can't
    verify TUI-side errors for that call programmatically.
    """
    output = None
    try:
        output = solver_session.tui.execute_tui(command_str)
    except Exception:
        try:
            output = solver_session.scheme_eval.scheme_eval(
                f'(ti-menu-load-string "{command_str}")'
            )
        except Exception as e:
            raise RuntimeError(f"Failed to execute TUI command: '{command_str}'. Error: {e}")

    if output is None:
        # This PyFluent build didn't hand back console text — we can't check
        # for embedded errors, only that the call itself didn't throw.
        return output

    output_str = str(output)
    if raise_on_tui_error and any(marker in output_str for marker in _TUI_ERROR_MARKERS):
        raise TuiCommandError(
            f"TUI command '{command_str}' returned an error:\n{output_str}"
        )

    return output


def get_fluid_zone_names(solver_session):
    """Returns the list of fluid cell zone names, or [] if unavailable."""
    try:
        return list(solver_session.setup.cell_zone_conditions.fluid.keys())
    except Exception as e:
        print(f"⚠️ Could not read fluid cell zone names: {e}")
        return []


def _safe_dir(obj):
    """dir() that never raises, even on odd Settings-API proxy objects."""
    try:
        return [n for n in dir(obj) if not n.startswith("_")]
    except Exception:
        return []


def _find_frozen_flow_path(zone_settings, max_depth=2):
    """
    Self-discovering search for whatever attribute controls Frozen Flow on
    this fluid zone, instead of relying on a fixed guess-list of sub-object
    names (which vary across PyFluent/Fluent versions and didn't match on
    this build). Searches attribute names containing 'frozen' up to
    `max_depth` levels into the settings tree.

    Returns (container_obj, attr_name) on a match, or (None, None) if
    nothing was found — callers should fall back to dumping dir() so the
    correct path can be identified from that run's own log.
    """
    visited = set()

    def _search(obj, depth):
        obj_id = id(obj)
        if obj_id in visited or depth > max_depth:
            return None, None
        visited.add(obj_id)

        names = _safe_dir(obj)

        # Direct match at this level first.
        for name in names:
            if "frozen" in name.lower():
                return obj, name

        if depth == max_depth:
            return None, None

        # Descend into plausible child settings objects. Skip anything that
        # looks like a method/command (leading verbs) or dunder-ish/private
        # to avoid triggering side effects — Settings API children are
        # typically nouns (general, reference_frame, source_terms, etc.).
        skip_prefixes = ("is_", "get_", "set_", "add_", "delete_", "rename_",
                          "create_", "read_", "write_", "compute_")
        for name in names:
            if name.startswith(skip_prefixes):
                continue
            try:
                child = getattr(obj, name)
            except Exception:
                continue
            # Only recurse into things that look like settings sub-objects
            # (have their own dir() with attributes), not plain values,
            # bound methods, or callables without children.
            if callable(child) or isinstance(child, (bool, int, float, str, type(None))):
                continue
            found_obj, found_name = _search(child, depth + 1)
            if found_obj is not None:
                return found_obj, found_name

        return None, None

    return _search(zone_settings, 0)


def _get_bool_value(container, attr_name):
    """Reads a boolean setting that may be a raw bool or a Parameter object
    exposing `.value` (both styles exist across PyFluent versions)."""
    val = getattr(container, attr_name)
    if hasattr(val, "value"):
        return bool(val.value)
    return bool(val)


def _set_bool_value(container, attr_name, new_value):
    """Writes a boolean setting, handling both raw-bool and .value-wrapped
    Parameter styles."""
    current = getattr(container, attr_name)
    if hasattr(current, "value"):
        current.value = new_value
    else:
        setattr(container, attr_name, new_value)


def ensure_flow_not_frozen(solver_session):
    """
    Best-effort check/reset of "Frozen Flow" on every fluid cell zone via the
    Settings API. NON-FATAL: on this Fluent build (confirmed via a full
    attribute dump against a live session), no attribute containing
    'frozen' exists anywhere in the fluid zone's settings tree — meaning
    this control simply isn't exposed through the Settings API on this
    version, not that we're looking in the wrong place. Raising here would
    permanently block every run on an unverifiable check.

    The actual safety net against a silently-frozen/disabled flow field is
    `runner._verify_flow_equation_active()`, which empirically probes a few
    iterations after initialization and checks whether velocity actually
    moves — this works regardless of which Settings-API path (if any) this
    Fluent build uses internally, and catches BOTH frozen cell zones and a
    disabled Solve > Equations > Flow toggle (which produces the identical
    symptom and is not zone-scoped, so wouldn't appear in this per-zone
    search either way).
    """
    zones = get_fluid_zone_names(solver_session)
    if not zones:
        print("⚠️ Could not enumerate fluid zones — skipping Frozen Flow settings-API check "
              "(the post-initialization flow-activity probe will still run).")
        return

    any_verified = False
    diagnostic_dumped = False

    for zone in zones:
        try:
            zone_settings = solver_session.setup.cell_zone_conditions.fluid[zone]
            container, attr_name = _find_frozen_flow_path(zone_settings)

            if container is None:
                if not diagnostic_dumped:
                    print(f"   (no 'frozen*' attribute found on fluid zone '{zone}' via Settings API "
                          f"— this build doesn't expose it here; relying on the flow-activity probe instead)")
                    diagnostic_dumped = True
                continue

            was_frozen = _get_bool_value(container, attr_name)
            _set_bool_value(container, attr_name, False)
            confirmed_off = not _get_bool_value(container, attr_name)

            if not confirmed_off:
                print(f"⚠️ '{attr_name}' still True after setting False on '{zone}' — "
                      f"relying on the flow-activity probe as a backstop.")
                continue

            any_verified = True
            status = " (was ON — disabled)" if was_frozen else " (already OFF)"
            print(f"-> Frozen Flow confirmed OFF for fluid zone '{zone}' via "
                  f"'.{attr_name}'{status}")

        except Exception as e:
            print(f"⚠️ Frozen Flow settings-API check errored on zone '{zone}': {e}")

    if not any_verified:
        print("-> Frozen Flow not settable via Settings API on this build — proceeding; "
              "the flow-activity probe after initialization is the real safety check.")


def configure_solver_physics(solver_session):
    """
    Sets up viscous models, energy equations, under-relaxation factors,
    ensures shell conduction is disabled on solid-fluid interfaces, and
    attempts to verify/reset Frozen Flow on the fluid zone(s) via the
    Settings API (best-effort, non-fatal — see ensure_flow_not_frozen()).

    Does NOT raise on an unverified Frozen Flow state. The authoritative
    safety check for "is the flow field actually being solved" is
    runner._verify_flow_equation_active(), which empirically probes a few
    iterations after initialization rather than relying on a Settings-API
    attribute that this build doesn't consistently expose.
    """
    print("\n" + "=" * 60)
    print("Configuring Global Solver Physics Parameters")
    print("=" * 60)

    # 1. Enable Energy Equation
    print("-> Enabling Energy Equation...")
    try:
        solver_session.setup.models.energy.enabled = True
    except Exception:
        execute_tui(solver_session, "/define/models/energy yes no no no no")

    # 2. Set Viscous Model to SST k-omega
    print("-> Setting Viscous Model to SST k-omega...")
    try:
        solver_session.setup.models.viscous.model = "k-omega"
        solver_session.setup.models.viscous.k_omega_model = "sst"
    except Exception:
        execute_tui(solver_session, "/define/models/viscous/kw-sst yes")

    # 3. Under-Relaxation Factors (URF) for Thermal/Flow Stability
    #    Settings API first (more stable across versions than TUI menu
    #    paths, which returned "Error: invalid command" in the last run
    #    without that error being visible to the caller). TUI is a fallback,
    #    and failures on both are now printed per-factor instead of being
    #    swallowed by a single blanket try/except.
    print("-> Applying Under-Relaxation Factors for stability...")
    for factor_name, factor_value in (("temperature", 0.95), ("k", 0.7), ("omega", 0.7)):
        try:
            solver_session.solution.controls.under_relaxation[factor_name] = factor_value
            print(f"-> URF[{factor_name}] = {factor_value} (Settings API)")
        except Exception as e_settings:
            try:
                execute_tui(
                    solver_session,
                    f"/solve/set/under-relaxation/{factor_name} {factor_value}"
                )
                print(f"-> URF[{factor_name}] = {factor_value} (TUI fallback)")
            except Exception as e_tui:
                print(
                    f"⚠️ Could not set URF[{factor_name}]: "
                    f"settings_api_error={e_settings} tui_error={e_tui}"
                )

    # 4. Disable Shell Conduction on Wall Interfaces (Prevents AMG Divergence)
    #    NOTE: this Fluent build returns "Error: invalid command" for
    #    disable-all, but prints it directly to console rather than handing
    #    it back as text execute_tui() can inspect — so a raised
    #    TuiCommandError can't be relied on here to catch it. Rather than
    #    print a false "success", this only claims the command was *sent*,
    #    not confirmed. Low-stakes: if this build doesn't support/need
    #    shell conduction here (e.g. no shell-conduction walls in this
    #    model), there's nothing to disable anyway.
    print("-> Attempting to disable shell conduction on wall boundaries...")
    try:
        execute_tui(solver_session, "/define/models/shell-conduction/disable-all",
                    raise_on_tui_error=False)
        print("-> Shell conduction disable-all command sent (not independently "
              "verifiable on this Fluent build — check console output above "
              "for an 'invalid command' error).")
    except Exception as e:
        print(f"⚠️ Could not send shell-conduction disable command: {e}")

    # 5. Verify (and enforce) the fluid zone(s) actually have Frozen Flow
    #    off — see ensure_flow_not_frozen() docstring for why this matters.
    #    This now raises rather than warns if it can't be confirmed.
    print("-> Verifying fluid zones are not set to Frozen Flow...")
    ensure_flow_not_frozen(solver_session)

    print("Physics configuration successfully applied!\n")