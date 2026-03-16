import csv
import json
from pathlib import Path

import numpy as np

from services import vulcanization_service_v2 as vs2


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
        "criteria": {
            "solver_no_nan": finite_solver,
            "cooling_started": cooling_started,
            "temperature_drops_after_cooling": bool(cooling_started and (temp_drop_after_cooling > 1e-4)),
            "mean_alpha_reaches_0_9_before_cooling": bool(cooling_started and (alpha_at_cooling_start >= 0.9)),
            "delta_alpha_near_optimal": False,
        },
    }


def apply_delta_alpha_criterion(case_rows, tolerance=1e-9):
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
        "solver_no_nan": criteria["solver_no_nan"],
        "cooling_started": criteria["cooling_started"],
        "temperature_drops_after_cooling": criteria["temperature_drops_after_cooling"],
        "mean_alpha_reaches_0_9_before_cooling": criteria["mean_alpha_reaches_0_9_before_cooling"],
        "delta_alpha_near_optimal": criteria["delta_alpha_near_optimal"],
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
):
    case_defs = list(cases) if cases is not None else build_validation_cases(quick=quick)
    rows = []

    old_out_dir = vs2.OUT_DIR
    if sim_out_dir is not None:
        vs2.OUT_DIR = Path(sim_out_dir)
        vs2.OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        for case in case_defs:
            sim_id, _ = vs2.run_simulation_v2(**case["sim_kwargs"])
            sim = vs2.load_simulation_v2(sim_id)
            metrics = _compute_metrics(sim)
            row = {
                "case_id": str(case["case_id"]),
                "size_class": str(case["size_class"]),
                "profile": str(case["profile"]),
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
    for row in rows:
        row["pass_required"] = bool(all(bool(row["criteria"][flag]) for flag in required_flags))

    report = {
        "suite": "v2_physical_validation",
        "quick_mode": bool(quick),
        "total_cases": len(rows),
        "required_flags": required_flags,
        "all_required_passed": bool(all(bool(r["pass_required"]) for r in rows)),
        "cases": rows,
    }

    json_path, csv_path = _write_report_files(report, output_dir=output_dir, report_stem=report_stem)
    report["json_report_path"] = json_path
    report["csv_report_path"] = csv_path
    return report
