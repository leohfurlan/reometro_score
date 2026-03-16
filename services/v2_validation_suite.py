import csv
import json
from pathlib import Path

import numpy as np

from services.engine_registry import ENGINE_THERMO_KINETIC_V2, get_engine_status
from services.simulation_schema import normalize_simulation_output
from services import vulcanization_service_v2 as vs2


QUALITY_STATUS_ORDER = {
    "healthy": 0,
    "info": 1,
    "warning": 2,
    "critical": 3,
}


def build_validation_cases(*, quick=False):
    """
    Build validation scenarios for small / medium / large equivalent geometries.

    Each size has:
    - baseline
    - near_optimal (higher thermal conductivity + longer hold)
    """
    if quick:
        size_table = [
            ("small", "7,7,7", 80.0),
            ("medium", "9,9,9", 100.0),
            ("large", "11,11,11", 120.0),
        ]
    else:
        size_table = [
            ("small", "9,9,9", 180.0),
            ("medium", "13,13,13", 220.0),
            ("large", "17,17,17", 260.0),
        ]

    base_fit = {
        "fit_id": "validation_reference_v2",
        "k0": 1.2e8,
        "Ea": 65000.0,
        "n": 1.2,
    }
    base_kinetics = {
        "Ac": 1.2e8,
        "Eac": 65000.0,
        "Ar": 0.0,
        "Ear": 80000.0,
        "Kn": 1.0,
        "Nn": 1.2,
        "Kx": 1.0,
        "Nx": 1.0,
        "alpha_c0": 0.84,
        "alpha_r0": 0.0,
    }

    cases = []
    for size_name, shape_raw, t_end_base in size_table:
        common = {
            "fit_payload": dict(base_fit),
            "mode": "autoclave",
            "dim": 3,
            "shape_raw": shape_raw,
            "dx": 0.001,
            "dt": 1.0,
            "mold_temp_c": 180.0,
            "init_temp_c": 25.0,
            "ramp_rate": 0.0,
            "snapshot_every": 4 if quick else 8,
            "kinetics_params": dict(base_kinetics),
            "exotherm_enabled": True,
            "qv": 13000.0,
            "A_ind": vs2.DEFAULT_A_IND,
            "E_ind": vs2.DEFAULT_E_IND,
        }

        baseline = dict(common)
        baseline["t_end"] = float(t_end_base)
        baseline["lambda_rubber"] = 0.16
        baseline["fit_payload"]["fit_id"] = f"validation_{size_name}_baseline"

        near_optimal = dict(common)
        near_optimal["t_end"] = float(t_end_base + (20.0 if quick else 40.0))
        near_optimal["lambda_rubber"] = 0.24
        near_optimal["fit_payload"]["fit_id"] = f"validation_{size_name}_near_optimal"

        cases.append(
            {
                "case_id": f"{size_name}_baseline",
                "size_class": size_name,
                "profile": "baseline",
                "sim_kwargs": baseline,
            }
        )
        cases.append(
            {
                "case_id": f"{size_name}_near_optimal",
                "size_class": size_name,
                "profile": "near_optimal",
                "sim_kwargs": near_optimal,
            }
        )

    return cases


def _time_series_mean(field_snaps):
    arr = np.asarray(field_snaps, dtype=float)
    if arr.ndim <= 1:
        return arr.astype(float, copy=False)
    axes = tuple(range(1, arr.ndim))
    return np.mean(arr, axis=axes)


def _time_series_min(field_snaps):
    arr = np.asarray(field_snaps, dtype=float)
    if arr.ndim <= 1:
        return arr.astype(float, copy=False)
    axes = tuple(range(1, arr.ndim))
    return np.min(arr, axis=axes)


def _time_series_max(field_snaps):
    arr = np.asarray(field_snaps, dtype=float)
    if arr.ndim <= 1:
        return arr.astype(float, copy=False)
    axes = tuple(range(1, arr.ndim))
    return np.max(arr, axis=axes)


def _compute_metrics(sim):
    t_snaps = np.asarray(sim["t_snaps"], dtype=float)
    alpha_snaps = np.asarray(sim["alpha_snaps"], dtype=float)
    states = np.asarray(sim["process_state_over_time"], dtype=str)
    times = np.asarray(sim["times"], dtype=float)

    temp_mean_ts_c = _time_series_mean(t_snaps) - 273.15
    temp_max_ts_c = _time_series_max(t_snaps) - 273.15
    alpha_mean_ts = _time_series_mean(alpha_snaps)
    alpha_min_ts = _time_series_min(alpha_snaps)
    alpha_max_ts = _time_series_max(alpha_snaps)

    final_temp = np.asarray(t_snaps[-1], dtype=float)
    final_alpha = np.asarray(alpha_snaps[-1], dtype=float)
    delta_alpha_final = float(np.max(final_alpha) - np.min(final_alpha))

    cooling_indices = np.where(states == "COOLING")[0]
    cooling_started = len(cooling_indices) > 0
    if cooling_started:
        i0 = int(cooling_indices[0])
        temp_after = temp_mean_ts_c[i0 + 1 :] if (i0 + 1) < len(temp_mean_ts_c) else np.asarray([], dtype=float)
        alpha_after = alpha_mean_ts[i0 + 1 :] if (i0 + 1) < len(alpha_mean_ts) else np.asarray([], dtype=float)
        temp_drop_after_cooling = (
            float(temp_mean_ts_c[i0] - np.min(temp_after)) if temp_after.size > 0 else 0.0
        )
        alpha_growth_after_cooling = (
            float(np.max(alpha_after) - alpha_mean_ts[i0]) if alpha_after.size > 0 else 0.0
        )
        alpha_at_cooling_start = float(alpha_mean_ts[i0])
        cooling_time = float(times[i0])
    else:
        temp_drop_after_cooling = 0.0
        alpha_growth_after_cooling = 0.0
        alpha_at_cooling_start = float(alpha_mean_ts[-1])
        cooling_time = None

    finite_solver = bool(
        np.isfinite(np.asarray(sim["t_snaps"], dtype=float)).all()
        and np.isfinite(np.asarray(sim["alpha_c_snaps"], dtype=float)).all()
        and np.isfinite(np.asarray(sim["alpha_r_snaps"], dtype=float)).all()
        and np.isfinite(np.asarray(sim["alpha_snaps"], dtype=float)).all()
    )
    solver_metrics = dict(sim.get("metrics") or {})
    clip_events_count = int(solver_metrics.get("clip_events_count", sim.get("clip_events_count", 0)) or 0)
    nan_recovery_events = int(solver_metrics.get("nan_recovery_events", sim.get("nan_recovery_events", 0)) or 0)
    numerical_warning_count = int(
        solver_metrics.get(
            "numerical_warning_count",
            len(sim.get("numerical_warnings") or solver_metrics.get("numerical_warnings") or []),
        )
        or 0
    )
    stiffness_alerts = list(solver_metrics.get("stiffness_alerts") or [])
    quality_flags = list(solver_metrics.get("quality_flags") or [])
    quality_status = str(solver_metrics.get("quality_status") or "healthy").lower()
    if quality_status not in QUALITY_STATUS_ORDER:
        quality_status = "warning"
    quality_severity_breakdown = dict(solver_metrics.get("quality_severity_breakdown") or {})
    induction_extrapolation_warning = bool(
        solver_metrics.get("induction_extrapolation_warning", sim.get("induction_extrapolation_warning", False))
    )
    induction_extrapolation_level = str(
        solver_metrics.get("induction_extrapolation_level")
        or ("mild" if induction_extrapolation_warning else "none")
    )
    temperature_validity_range = dict(
        solver_metrics.get("temperature_validity_range") or sim.get("temperature_validity_range") or {}
    )

    alerts = []
    if int(solver_metrics.get("substeps_max_per_step", 0) or 0) >= 8:
        alerts.append("high_substepping")
    if float(solver_metrics.get("max_source_to_diffusion_dT_ratio", 0.0) or 0.0) >= 10.0:
        alerts.append("source_dominance")
    if nan_recovery_events > 0:
        alerts.append("nan_recovery")
    if clip_events_count > 0:
        alerts.append("clipping_events")
    if numerical_warning_count > 0:
        alerts.append("numerical_warnings")
    if stiffness_alerts:
        alerts.append("stiffness_alerts")
    if induction_extrapolation_warning:
        alerts.append("induction_extrapolation")
    if quality_status in {"warning", "critical"}:
        alerts.append(f"quality_{quality_status}")

    return {
        "temperature_max_c": float(np.max(final_temp) - 273.15),
        "temperature_mean_c": float(np.mean(final_temp) - 273.15),
        "alpha_min": float(np.min(final_alpha)),
        "alpha_mean": float(np.mean(final_alpha)),
        "alpha_max": float(np.max(final_alpha)),
        "delta_alpha": delta_alpha_final,
        "demold_time_s": None if sim["demold_time"] is None else float(sim["demold_time"]),
        "cooling_time_s": cooling_time,
        "post_cooling_temp_drop_c": temp_drop_after_cooling,
        "post_cooling_alpha_growth": alpha_growth_after_cooling,
        "alpha_mean_at_cooling_start": alpha_at_cooling_start,
        "temp_mean_ts_c": [float(x) for x in np.asarray(temp_mean_ts_c, dtype=float)],
        "temp_max_ts_c": [float(x) for x in np.asarray(temp_max_ts_c, dtype=float)],
        "alpha_mean_ts": [float(x) for x in np.asarray(alpha_mean_ts, dtype=float)],
        "alpha_min_ts": [float(x) for x in np.asarray(alpha_min_ts, dtype=float)],
        "alpha_max_ts": [float(x) for x in np.asarray(alpha_max_ts, dtype=float)],
        "substeps_total": int(solver_metrics.get("substeps_total", 0) or 0),
        "substeps_max_per_step": int(solver_metrics.get("substeps_max_per_step", 0) or 0),
        "substeps_mean_per_step": float(solver_metrics.get("substeps_mean_per_step", 0.0) or 0.0),
        "max_cure_rate_s_inv": float(solver_metrics.get("max_cure_rate_s_inv", 0.0) or 0.0),
        "max_reversion_rate_s_inv": float(solver_metrics.get("max_reversion_rate_s_inv", 0.0) or 0.0),
        "max_heat_source_w_m3": float(solver_metrics.get("max_heat_source_w_m3", 0.0) or 0.0),
        "max_source_to_diffusion_dT_ratio": float(
            solver_metrics.get("max_source_to_diffusion_dT_ratio", 0.0) or 0.0
        ),
        "clip_events_count": clip_events_count,
        "nan_recovery_events": nan_recovery_events,
        "numerical_warning_count": numerical_warning_count,
        "critical_step_index": int(solver_metrics.get("critical_step_index", 0) or 0),
        "critical_step_time_s": float(solver_metrics.get("critical_step_time_s", 0.0) or 0.0),
        "critical_step_reason": str(solver_metrics.get("critical_step_reason") or "unknown"),
        "critical_step_context": dict(solver_metrics.get("critical_step_context") or {}),
        "critical_step_metrics": dict(solver_metrics.get("critical_step_metrics") or {}),
        "stiffness_alerts": stiffness_alerts,
        "alerts": alerts,
        "induction_confidence": str(solver_metrics.get("induction_confidence") or sim.get("induction_confidence") or ""),
        "induction_source": str(solver_metrics.get("induction_source") or sim.get("induction_source") or ""),
        "induction_fit_quality": str(
            solver_metrics.get("induction_fit_quality") or sim.get("induction_fit_quality") or ""
        ),
        "induction_model_regime": str(
            solver_metrics.get("induction_model_regime") or sim.get("induction_model_regime") or "single_arrhenius"
        ),
        "temperature_validity_range": temperature_validity_range,
        "induction_extrapolation_warning": induction_extrapolation_warning,
        "induction_extrapolation_level": induction_extrapolation_level,
        "quality_flags": quality_flags,
        "quality_status": quality_status,
        "quality_severity_breakdown": quality_severity_breakdown,
        "criteria": {
            "solver_no_nan": finite_solver,
            "cooling_started": cooling_started,
            "temperature_drops_after_cooling": bool(cooling_started and (temp_drop_after_cooling > 1e-4)),
            "mean_alpha_reaches_0_9_before_cooling": bool(cooling_started and (alpha_at_cooling_start >= 0.9)),
            "delta_alpha_near_optimal": False,
            "no_nan_recovery_events": bool(nan_recovery_events == 0),
            "no_clip_events": bool(clip_events_count == 0),
            "has_stiffness_alerts": bool(len(alerts) > 0),
            "quality_not_critical": bool(quality_status != "critical"),
            "induction_within_validity_range": bool(not induction_extrapolation_warning),
        },
    }


def apply_delta_alpha_criterion(case_rows, tolerance=2e-6):
    grouped = {}
    for row in case_rows:
        grouped.setdefault(row["size_class"], {})[row["profile"]] = row

    for _, profile_map in grouped.items():
        base = profile_map.get("baseline")
        near = profile_map.get("near_optimal")
        if base is None or near is None:
            continue

        base_delta = float(base["metrics"]["delta_alpha"])
        near_delta = float(near["metrics"]["delta_alpha"])
        passed = bool(near_delta <= (base_delta + float(tolerance)))

        base["criteria"]["delta_alpha_near_optimal"] = passed
        near["criteria"]["delta_alpha_near_optimal"] = passed

    return case_rows


def _serialize_case_for_csv(case_row):
    metrics = case_row["metrics"]
    criteria = case_row["criteria"]
    return {
        "engine": case_row.get("engine", ENGINE_THERMO_KINETIC_V2),
        "engine_status": case_row.get("engine_status", get_engine_status(ENGINE_THERMO_KINETIC_V2)),
        "case_id": case_row["case_id"],
        "size_class": case_row["size_class"],
        "profile": case_row["profile"],
        "temperature_max_c": metrics["temperature_max_c"],
        "temperature_mean_c": metrics["temperature_mean_c"],
        "alpha_min": metrics["alpha_min"],
        "alpha_mean": metrics["alpha_mean"],
        "alpha_max": metrics["alpha_max"],
        "delta_alpha": metrics["delta_alpha"],
        "demold_time_s": metrics["demold_time_s"],
        "post_cooling_temp_drop_c": metrics["post_cooling_temp_drop_c"],
        "post_cooling_alpha_growth": metrics["post_cooling_alpha_growth"],
        "substeps_total": metrics["substeps_total"],
        "substeps_max_per_step": metrics["substeps_max_per_step"],
        "substeps_mean_per_step": metrics["substeps_mean_per_step"],
        "max_cure_rate_s_inv": metrics["max_cure_rate_s_inv"],
        "max_reversion_rate_s_inv": metrics["max_reversion_rate_s_inv"],
        "max_heat_source_w_m3": metrics["max_heat_source_w_m3"],
        "max_source_to_diffusion_dT_ratio": metrics["max_source_to_diffusion_dT_ratio"],
        "clip_events_count": metrics["clip_events_count"],
        "nan_recovery_events": metrics["nan_recovery_events"],
        "numerical_warning_count": metrics["numerical_warning_count"],
        "critical_step_index": metrics["critical_step_index"],
        "critical_step_time_s": metrics["critical_step_time_s"],
        "critical_step_reason": metrics["critical_step_reason"],
        "quality_status": metrics["quality_status"],
        "quality_flags": ";".join(
            str(x.get("code") if isinstance(x, dict) else x) for x in (metrics.get("quality_flags") or [])
        ),
        "induction_extrapolation_warning": metrics["induction_extrapolation_warning"],
        "induction_extrapolation_level": metrics["induction_extrapolation_level"],
        "induction_confidence": metrics["induction_confidence"],
        "induction_source": metrics["induction_source"],
        "induction_fit_quality": metrics["induction_fit_quality"],
        "induction_model_regime": metrics["induction_model_regime"],
        "alerts": ";".join(str(x) for x in (metrics.get("alerts") or [])),
        "solver_no_nan": criteria["solver_no_nan"],
        "cooling_started": criteria["cooling_started"],
        "temperature_drops_after_cooling": criteria["temperature_drops_after_cooling"],
        "mean_alpha_reaches_0_9_before_cooling": criteria["mean_alpha_reaches_0_9_before_cooling"],
        "delta_alpha_near_optimal": criteria["delta_alpha_near_optimal"],
        "no_nan_recovery_events": criteria["no_nan_recovery_events"],
        "no_clip_events": criteria["no_clip_events"],
        "has_stiffness_alerts": criteria["has_stiffness_alerts"],
        "quality_not_critical": criteria["quality_not_critical"],
        "induction_within_validity_range": criteria["induction_within_validity_range"],
    }


def _write_report_files(report, output_dir, report_stem):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{report_stem}.json"
    csv_path = out_dir / f"{report_stem}.csv"

    with json_path.open("w", encoding="utf-8") as f_json:
        json.dump(report, f_json, indent=2, ensure_ascii=False)

    csv_rows = [_serialize_case_for_csv(row) for row in report["cases"]]
    fieldnames = list(csv_rows[0].keys()) if csv_rows else ["case_id"]
    with csv_path.open("w", encoding="utf-8", newline="") as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=fieldnames)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)

    return str(json_path), str(csv_path)


def run_v2_validation_suite(
    *,
    output_dir="data/out",
    report_stem="v2_validation_report",
    quick=False,
    cases=None,
    sim_out_dir=None,
    gate_mode="informative",
):
    gate_mode_norm = str(gate_mode or "informative").strip().lower()
    if gate_mode_norm not in {"informative", "strict"}:
        gate_mode_norm = "informative"

    case_defs = list(cases) if cases is not None else build_validation_cases(quick=quick)
    rows = []

    old_out_dir = vs2.OUT_DIR
    if sim_out_dir is not None:
        vs2.OUT_DIR = Path(sim_out_dir)
        vs2.OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        for case in case_defs:
            sim_id, _ = vs2.run_simulation_v2(**case["sim_kwargs"])
            sim_raw = vs2.load_simulation_v2(sim_id)
            sim = normalize_simulation_output(sim_raw, engine=ENGINE_THERMO_KINETIC_V2)
            metrics = _compute_metrics(sim)
            row = {
                "case_id": str(case["case_id"]),
                "size_class": str(case["size_class"]),
                "profile": str(case["profile"]),
                "engine": ENGINE_THERMO_KINETIC_V2,
                "engine_status": get_engine_status(ENGINE_THERMO_KINETIC_V2),
                "metrics": metrics,
                "criteria": dict(metrics["criteria"]),
            }
            rows.append(row)
    finally:
        vs2.OUT_DIR = old_out_dir

    rows = apply_delta_alpha_criterion(rows)

    required_flags = [
        "solver_no_nan",
        "temperature_drops_after_cooling",
        "mean_alpha_reaches_0_9_before_cooling",
        "delta_alpha_near_optimal",
    ]
    alert_flags = [
        "no_nan_recovery_events",
        "no_clip_events",
        "has_stiffness_alerts",
        "quality_not_critical",
        "induction_within_validity_range",
    ]
    for row in rows:
        row["pass_required"] = bool(all(bool(row["criteria"][flag]) for flag in required_flags))
        row["pass_strict"] = bool(row["pass_required"] and row["criteria"].get("quality_not_critical", True))

    quality_status_counts = {"healthy": 0, "info": 0, "warning": 0, "critical": 0}
    for row in rows:
        status = str(row.get("metrics", {}).get("quality_status") or "healthy").lower()
        if status not in quality_status_counts:
            status = "warning"
        quality_status_counts[status] += 1

    all_required_passed = bool(all(bool(r["pass_required"]) for r in rows))
    has_critical_quality = bool(quality_status_counts["critical"] > 0)
    if gate_mode_norm == "strict":
        ci_gate_passed = bool(all_required_passed and (not has_critical_quality))
    else:
        ci_gate_passed = True

    report = {
        "suite": "v2_physical_validation",
        "engine": ENGINE_THERMO_KINETIC_V2,
        "engine_status": get_engine_status(ENGINE_THERMO_KINETIC_V2),
        "quick_mode": bool(quick),
        "quality_gate_mode": gate_mode_norm,
        "total_cases": len(rows),
        "required_flags": required_flags,
        "alert_flags_non_blocking": alert_flags,
        "all_required_passed": all_required_passed,
        "has_critical_quality": has_critical_quality,
        "quality_status_counts": dict(quality_status_counts),
        "ci_gate_passed": bool(ci_gate_passed),
        "cases": rows,
    }

    json_path, csv_path = _write_report_files(report, output_dir=output_dir, report_stem=report_stem)
    report["json_report_path"] = json_path
    report["csv_report_path"] = csv_path
    return report
