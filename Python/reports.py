"""
reports.py

Extracts key thermal/flow metrics from a Fluent solver session using the
report-definitions Settings API, with a best-effort TUI fallback.

NOTE: report-definition attribute names (report_type, field, surface_names,
zone_names, .compute()) match recent PyFluent releases at the time this was
written. If your PyFluent version differs, you may need to adjust the
attribute names below — but the important thing is that failures are now
surfaced (printed per-metric) instead of silently producing fake "success".
"""

import csv

import config


def _safe_dir(obj):
    try:
        return [n for n in dir(obj) if not n.startswith("_")]
    except Exception:
        return []


_diagnostics_dumped = set()  # module-level, so each distinct failure mode dumps only once per run


def _create_and_compute_report(solver_session, name, category, report_type, field,
                                target_names):
    """
    Creates a report definition and computes it. Tries the confirmed-correct
    creation method first: container.create(name) — confirmed present on
    report_definitions.volume via a live attribute dump, and the same
    failure family as the velocity-inlet BC bug (container[name] = {} looks
    like it should work, silently doesn't actually create the object, and
    a later access throws a bare KeyError(name)).

    Tries, per container (typed sub-container first, flat as fallback) and
    per zone-attribute-name candidate:
      1. container.create(name) — the confirmed-correct method.
      2. container[name] = {} — kept as a fallback for other PyFluent
         versions where this form does work.

    Cleans up afterward via del container[name] or container.delete(name),
    whichever is available.

    Returns the computed float, or None on total failure — after which it
    dumps the attribute list of whichever container it reached, ONCE per
    distinct failure signature, so any remaining mismatch can be identified
    from this same run's log instead of another guess-and-rerun cycle.
    """
    rd_root = solver_session.solution.report_definitions

    containers = []
    typed_container = getattr(rd_root, category, None)
    if typed_container is not None:
        containers.append(typed_container)
    containers.append(rd_root)

    zone_attr_candidates = ["zone_names", "cell_zones"] if category == "volume" else ["surface_names"]

    def _cleanup(container):
        try:
            del container[name]
            return
        except Exception:
            pass
        try:
            container.delete(name)
        except Exception:
            pass

    def _extract_value(computed):
        """compute() returns a list of dicts; the key isn't guaranteed to
        exactly match `name` on every version, so try the expected key
        first and fall back to whatever single value is present."""
        last = computed[-1]
        if name in last:
            return last[name]
        if len(last) == 1:
            return next(iter(last.values()))
        raise KeyError(f"Unexpected compute() result shape: {last!r}")

    last_error = None
    for container in containers:
        creation_strategies = []
        if hasattr(container, "create"):
            creation_strategies.append("create_method")
        creation_strategies.append("dict_assign")

        for strategy in creation_strategies:
            for zone_attr in zone_attr_candidates:
                try:
                    if strategy == "create_method":
                        obj = container.create(name)
                    else:
                        container[name] = {}
                        obj = container[name]

                    obj.report_type = report_type
                    if field is not None:
                        obj.field = field
                    setattr(obj, zone_attr, target_names)

                    value = _extract_value(obj.compute())
                    _cleanup(container)
                    return float(value)

                except Exception as e:
                    last_error = e
                    _cleanup(container)
                    continue

    # Every combination failed — dump diagnostics once per distinct
    # (category, report_type) pair so repeated calls (e.g. per-case Tin/
    # Tout) don't spam the log, but each genuinely new failure gets one.
    sig = (category, report_type)
    if sig not in _diagnostics_dumped:
        _diagnostics_dumped.add(sig)
        dump_target = getattr(rd_root, category, rd_root)
        print(f"⚠️ Could not create/compute report '{name}' ({report_type}) via any known path. "
              f"Last error: {last_error}")
        print(f"   Attributes available on report_definitions.{category if hasattr(rd_root, category) else '(flat)'}:")
        print(f"   {sorted(_safe_dir(dump_target))}")
    else:
        print(f"⚠️ Could not create/compute report '{name}' ({report_type}): {last_error}")

    return None


def get_fluid_zone_names(solver_session):
    """Returns the list of fluid cell zone names, or [] if unavailable."""
    try:
        return list(solver_session.setup.cell_zone_conditions.fluid.keys())
    except Exception as e:
        print(f"⚠️ Could not read fluid cell zone names: {e}")
        return []


def get_velocity_magnitude_report(solver_session, zone_names=None):
    """
    Volume-averaged velocity magnitude across the given cell zones (defaults
    to all fluid zones). Used as an empirical sanity probe: if this stays at
    exactly 0.0 across several iterations, the flow field isn't being
    solved. Returns None if the metric can't be computed.
    """
    if zone_names is None:
        zone_names = get_fluid_zone_names(solver_session)
    if not zone_names:
        return None

    return _create_and_compute_report(
        solver_session, "__vmag_check", "volume", "volume-average",
        "velocity-magnitude", zone_names
    )


def get_max_temperature(solver_session, zone_names=None):
    """
    Returns the maximum temperature (K) over the given cell zones (defaults to
    all fluid zones). Used by runner.py as a divergence check. Returns None
    if the metric could not be computed, rather than raising, so a callers'
    watchdog loop degrades gracefully instead of crashing the whole pipeline.
    """
    if zone_names is None:
        zone_names = get_fluid_zone_names(solver_session)
    if not zone_names:
        return None

    return _create_and_compute_report(
        solver_session, "__tmax_check", "volume", "volume-max",
        "temperature", zone_names
    )


def _compute_report(solver_session, name, report_type, field, surface_names):
    """Single surface report-definition compute, returning None on any failure."""
    return _create_and_compute_report(
        solver_session, name, "surface", report_type, field, surface_names
    )


def export_case_results(solver_session, velocity, heat_flux,
                         inlet_name=None, outlet_name=None, wall_name=None):
    """
    Computes Tin, Tout, Tmax, and mass flow rate for the current solution.
    Returns a dict; any metric that fails to compute is set to None rather
    than silently defaulting to a misleading value like 0.0 or 300.0.
    """
    inlet_name = inlet_name or getattr(config, "REAL_INLET_NAME", None)
    outlet_name = outlet_name or getattr(config, "REAL_OUTLET_NAME", None)
    wall_name = wall_name or getattr(config, "REAL_WALL_NAME", None)

    results = {"velocity": velocity, "heat_flux": heat_flux}

    if inlet_name:
        results["tin"] = _compute_report(
            solver_session, "__tin", "surface-massavg", "temperature", [inlet_name]
        )
        results["massflow"] = _compute_report(
            solver_session, "__mdot", "surface-massflowrate", None, [inlet_name]
        )
        if results["massflow"] is not None:
            results["massflow"] = abs(results["massflow"])
    else:
        print("⚠️ No inlet name available — skipping Tin / mass flow.")
        results["tin"] = None
        results["massflow"] = None

    if outlet_name:
        results["tout"] = _compute_report(
            solver_session, "__tout", "surface-massavg", "temperature", [outlet_name]
        )
    else:
        print("⚠️ No outlet name configured (config.REAL_OUTLET_NAME) — skipping Tout.")
        results["tout"] = None

    fluid_zones = get_fluid_zone_names(solver_session)
    if fluid_zones:
        results["tmax"] = get_max_temperature(solver_session, fluid_zones)
    elif wall_name:
        results["tmax"] = _compute_report(
            solver_session, "__tmax", "surface-facetmax", "temperature", [wall_name]
        )
    else:
        print("⚠️ No fluid zones or wall name available — skipping Tmax.")
        results["tmax"] = None

    return results


def write_case_result_to_csv(geometry_name, velocity, heat_flux, results):
    """
    Appends one row to config.RESULTS_CSV, creating the file with a header
    row if it doesn't exist yet.

    NOTE: this file is now purely a human-readable record — it is NOT used
    for skip/resume logic anymore. runner.load_completed_cases() checks
    actual .cas.h5 files in each geometry's own case_dir instead, since
    this shared, single CSV (with no geometry column at all) previously
    caused every geometry after the first to see the first geometry's
    (velocity, heat_flux) rows already present and silently skip all of
    its own cases without ever running them.

    The Geometry column here is for your own reference when reviewing
    results across multiple designs, nothing in the pipeline reads it
    back. If a pre-existing results.csv from before this column was added
    is still being appended to, its original header won't have a
    'Geometry' column — the file will still work, just note that when
    opening it, older rows won't have that column populated to align with.
    """
    csv_path = config.RESULTS_CSV
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "Geometry",
        "Velocity (m/s)",
        "Heat Flux (W/m2)",
        "Tin (K)",
        "Tout (K)",
        "Tmax (K)",
        "Mass Flow (kg/s)",
    ]

    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, mode="a", newline="", encoding="cp1252", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "Geometry": geometry_name,
            "Velocity (m/s)": velocity,
            "Heat Flux (W/m2)": heat_flux,
            "Tin (K)": results.get("tin"),
            "Tout (K)": results.get("tout"),
            "Tmax (K)": results.get("tmax"),
            "Mass Flow (kg/s)": results.get("massflow"),
        })