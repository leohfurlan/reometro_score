import math
from typing import Dict, Iterable, List

import numpy as np

R_GAS = 8.314462618
DEFAULT_TIME_FLOOR_S = 5.0
DEFAULT_MIN_POINTS_ARRHENIUS = 3
DEFAULT_MIN_TEMP_SPAN_K = 8.0
DEFAULT_MIN_R2 = 0.60


def _as_float_array(values):
    return np.asarray(values if values is not None else [], dtype=float)


def _temperature_range_payload(temp_k_min, temp_k_max):
    temp_k_min = float(temp_k_min)
    temp_k_max = float(temp_k_max)
    span = float(temp_k_max - temp_k_min)
    return {
        "temperature_range_k": {
            "min": temp_k_min,
            "max": temp_k_max,
            "span": span,
        },
        "temperature_range_c": {
            "min": temp_k_min - 273.15,
            "max": temp_k_max - 273.15,
            "span": span,
        },
        "temperature_validity_range": {
            "min_k": temp_k_min,
            "max_k": temp_k_max,
            "min_c": temp_k_min - 273.15,
            "max_c": temp_k_max - 273.15,
            "span_k": span,
            "span_c": span,
            "source": "scorch_points_regression_window",
        },
    }


def extract_curve_scorch(time_vec, torque_vec, time_floor=DEFAULT_TIME_FLOOR_S):
    """
    Extract experimental scorch point from one rheometer curve.

    Rules:
    - Search minimum torque only in subset where t >= time_floor.
    - If there are no points in this subset, mark curve as invalid.

    Returns:
    {
      "valid": bool,
      "t_scorch": float | None,    # s
      "torque_min": float | None,  # torque units from source curve
      "index": int | None,         # original index in full vectors
      "used_time_floor": float,    # s
      "reason": str | None,
    }
    """
    time_arr = _as_float_array(time_vec)
    torque_arr = _as_float_array(torque_vec)
    size = min(time_arr.size, torque_arr.size)
    time_floor = float(time_floor)

    if size <= 0:
        return {
            "valid": False,
            "t_scorch": None,
            "torque_min": None,
            "index": None,
            "used_time_floor": time_floor,
            "t_scorch_s": None,
            "search_start_s": time_floor,
            "search_first_time_s": None,
            "points_after_search": 0,
            "points_before_search": 0,
            "total_valid_points": 0,
            "global_min_time_s": None,
            "global_min_torque": None,
            "global_min_discarded": None,
            "valid_for_regression": False,
            "reason": "empty_curve",
        }

    t = time_arr[:size]
    q = torque_arr[:size]
    finite_mask = np.isfinite(t) & np.isfinite(q)
    finite_idx = np.where(finite_mask)[0]
    global_min_idx = None
    if finite_idx.size > 0:
        global_min_idx = int(finite_idx[int(np.argmin(q[finite_mask]))])

    mask = finite_mask & (t >= time_floor)
    candidates = np.where(mask)[0]

    if candidates.size == 0:
        points_before_search = int(np.sum(finite_mask & (t < time_floor)))
        points_after_search = int(np.sum(finite_mask & (t >= time_floor)))
        global_min_discarded = bool(global_min_idx is not None and global_min_idx not in candidates)
        return {
            "valid": False,
            "t_scorch": None,
            "torque_min": None,
            "index": None,
            "used_time_floor": time_floor,
            "t_scorch_s": None,
            "search_start_s": time_floor,
            "search_first_time_s": None,
            "points_after_search": points_after_search,
            "points_before_search": points_before_search,
            "total_valid_points": int(np.sum(finite_mask)),
            "global_min_time_s": float(t[global_min_idx]) if global_min_idx is not None else None,
            "global_min_torque": float(q[global_min_idx]) if global_min_idx is not None else None,
            "global_min_discarded": global_min_discarded,
            "valid_for_regression": False,
            "reason": "insufficient_points_after_time_floor",
        }

    idx_local = int(np.argmin(q[mask]))
    idx = int(candidates[idx_local])
    points_before_search = int(np.sum(finite_mask & (t < time_floor)))
    points_after_search = int(np.sum(finite_mask & (t >= time_floor)))
    global_min_discarded = bool(global_min_idx is not None and global_min_idx != idx)
    return {
        "valid": True,
        "t_scorch": float(t[idx]),
        "torque_min": float(q[idx]),
        "index": idx,
        "used_time_floor": time_floor,
        "t_scorch_s": float(t[idx]),
        "search_start_s": time_floor,
        "search_first_time_s": float(t[candidates[0]]) if candidates.size > 0 else None,
        "points_after_search": points_after_search,
        "points_before_search": points_before_search,
        "total_valid_points": int(np.sum(finite_mask)),
        "global_min_time_s": float(t[global_min_idx]) if global_min_idx is not None else None,
        "global_min_torque": float(q[global_min_idx]) if global_min_idx is not None else None,
        "global_min_discarded": global_min_discarded,
        "valid_for_regression": True,
        "reason": None,
    }


def extract_scorch_points(curves: Iterable[Dict], time_floor=DEFAULT_TIME_FLOOR_S):
    """
    Batch scorch extraction for multiple curves.

    Each curve should provide:
    - "tempo" (time in s)
    - "torque"
    Optional fields are preserved in output (e.g. COD_ENSAIO, T_C, T_K).
    """
    rows = []
    for curve in curves:
        row = dict(curve or {})
        extracted = extract_curve_scorch(
            time_vec=row.get("tempo"),
            torque_vec=row.get("torque"),
            time_floor=time_floor,
        )
        row.update(extracted)
        rows.append(row)
    return rows


def _coerce_temperature_k(item):
    t_k = item.get("T_K")
    if t_k is None:
        t_c = item.get("T_C")
        if t_c is not None:
            t_k = float(t_c) + 273.15
    try:
        t_k = float(t_k)
    except (TypeError, ValueError):
        return math.nan
    return t_k


def _build_arrhenius_table(points: Iterable[Dict]):
    table = []
    used_rows = []
    discarded_rows = []

    for idx, item in enumerate(points):
        row = {
            "point_index": int(idx),
            "COD_ENSAIO": item.get("COD_ENSAIO"),
            "T_C": float(item.get("T_C")) if item.get("T_C") is not None else None,
            "T_K": None,
            "t_scorch": None,
            "torque_min": None,
            "ln_t_scorch": None,
            "inv_T": None,
            "status": "discarded",
            "discard_reason": None,
        }

        is_valid_curve = bool(item.get("valid"))
        t_scorch = item.get("t_scorch")
        torque_min = item.get("torque_min")
        temp_k = _coerce_temperature_k(item)

        if np.isfinite(temp_k):
            row["T_K"] = float(temp_k)
            if row["T_C"] is None:
                row["T_C"] = float(temp_k - 273.15)

        if t_scorch is not None and np.isfinite(float(t_scorch)):
            row["t_scorch"] = float(t_scorch)
        if torque_min is not None and np.isfinite(float(torque_min)):
            row["torque_min"] = float(torque_min)

        if not is_valid_curve:
            row["discard_reason"] = item.get("reason") or "invalid_curve"
        elif row["t_scorch"] is None:
            row["discard_reason"] = "missing_or_nonfinite_t_scorch"
        elif row["T_K"] is None:
            row["discard_reason"] = "missing_or_nonfinite_temperature"
        elif row["t_scorch"] <= 0.0:
            row["discard_reason"] = "non_positive_t_scorch"
        elif row["T_K"] <= 0.0:
            row["discard_reason"] = "non_positive_temperature_k"
        else:
            row["ln_t_scorch"] = float(math.log(row["t_scorch"]))
            row["inv_T"] = float(1.0 / row["T_K"])
            row["status"] = "used"
            used_rows.append(row)

        if row["status"] != "used":
            discarded_rows.append(row)
        table.append(row)

    return table, used_rows, discarded_rows


def fit_arrhenius_from_scorch(
    points: Iterable[Dict],
    *,
    min_points=DEFAULT_MIN_POINTS_ARRHENIUS,
    min_temp_span_k=DEFAULT_MIN_TEMP_SPAN_K,
    min_r2=DEFAULT_MIN_R2,
):
    """
    Fit Arrhenius-like induction relation from experimental scorch data:

        ln(t_scorch) = intercept + slope * (1 / T)

    with T in Kelvin and t_scorch in seconds.

    Returned physical parameters:
    - E_ind = slope * R_GAS             [J/mol]
    - A_ind = exp(-intercept)           [1/s]

    Returns dict with:
    - success, message
    - A_ind, E_ind, R2
    - intercept, slope
    - points_used (table used in regression)
    """
    min_points = int(max(min_points, 2))
    min_temp_span_k = float(max(min_temp_span_k, 0.0))
    min_r2 = float(np.clip(min_r2, -1.0, 1.0))

    table, points_used, points_discarded = _build_arrhenius_table(points)
    used_count = len(points_used)

    if used_count < min_points:
        return {
            "success": False,
            "message": (
                "Pontos validos insuficientes para ajuste Arrhenius de scorch "
                f"({used_count}/{min_points})."
            ),
            "A_ind": None,
            "E_ind": None,
            "R2": None,
            "intercept": None,
            "slope": None,
            "points_used": points_used,
            "points_discarded": points_discarded,
            "points_table": table,
            "total_points": len(table),
            "valid_points": used_count,
            "discarded_points": len(points_discarded),
            "fit_quality": "insufficient_data",
            "temperature_range_k": None,
            "temperature_range_c": None,
            "temperature_validity_range": None,
            "induction_model_regime": "unavailable",
            "criteria": {
                "min_points": min_points,
                "min_temp_span_k": min_temp_span_k,
                "min_r2": min_r2,
            },
        }

    x = np.asarray([p["inv_T"] for p in points_used], dtype=float)
    y = np.asarray([p["ln_t_scorch"] for p in points_used], dtype=float)
    temps_k = np.asarray([p["T_K"] for p in points_used], dtype=float)
    temp_k_min = float(np.min(temps_k))
    temp_k_max = float(np.max(temps_k))
    temp_k_span = float(temp_k_max - temp_k_min)
    range_payload = _temperature_range_payload(temp_k_min, temp_k_max)

    if temp_k_span < min_temp_span_k:
        return {
            "success": False,
            "message": (
                "Faixa de temperatura insuficiente para ajuste Arrhenius robusto "
                f"({temp_k_span:.3f} K < {min_temp_span_k:.3f} K)."
            ),
            "A_ind": None,
            "E_ind": None,
            "R2": None,
            "intercept": None,
            "slope": None,
            "points_used": points_used,
            "points_discarded": points_discarded,
            "points_table": table,
            "total_points": len(table),
            "valid_points": used_count,
            "discarded_points": len(points_discarded),
            "fit_quality": "insufficient_temperature_range",
            **range_payload,
            "induction_model_regime": "single_arrhenius",
            "criteria": {
                "min_points": min_points,
                "min_temp_span_k": min_temp_span_k,
                "min_r2": min_r2,
            },
        }

    slope, intercept = np.polyfit(x, y, deg=1)
    y_pred = intercept + (slope * x)

    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot <= 1e-15:
        r2 = 1.0 if ss_res <= 1e-15 else 0.0
    else:
        r2 = 1.0 - (ss_res / ss_tot)

    if slope <= 0.0:
        return {
            "success": False,
            "message": (
                "Ajuste Arrhenius fisicamente inconsistente: slope <= 0 "
                "(scorch nao aumenta com 1/T)."
            ),
            "A_ind": None,
            "E_ind": None,
            "R2": float(r2),
            "intercept": float(intercept),
            "slope": float(slope),
            "points_used": points_used,
            "points_discarded": points_discarded,
            "points_table": table,
            "total_points": len(table),
            "valid_points": used_count,
            "discarded_points": len(points_discarded),
            "fit_quality": "inconsistent_slope",
            **range_payload,
            "induction_model_regime": "single_arrhenius",
            "criteria": {
                "min_points": min_points,
                "min_temp_span_k": min_temp_span_k,
                "min_r2": min_r2,
            },
        }

    if r2 < min_r2:
        return {
            "success": False,
            "message": (
                "Ajuste Arrhenius rejeitado por baixa qualidade "
                f"(R2={r2:.4f} < {min_r2:.4f})."
            ),
            "A_ind": None,
            "E_ind": None,
            "R2": float(r2),
            "intercept": float(intercept),
            "slope": float(slope),
            "points_used": points_used,
            "points_discarded": points_discarded,
            "points_table": table,
            "total_points": len(table),
            "valid_points": used_count,
            "discarded_points": len(points_discarded),
            "fit_quality": "low_r2",
            **range_payload,
            "induction_model_regime": "single_arrhenius",
            "criteria": {
                "min_points": min_points,
                "min_temp_span_k": min_temp_span_k,
                "min_r2": min_r2,
            },
        }

    e_ind = float(slope * R_GAS)
    a_ind = float(math.exp(-intercept))

    if r2 >= 0.98 and used_count >= 4:
        fit_quality = "high"
    elif r2 >= 0.90:
        fit_quality = "medium"
    else:
        fit_quality = "low"

    return {
        "success": True,
        "message": "Ajuste Arrhenius de scorch concluido.",
        "A_ind": a_ind,
        "E_ind": e_ind,
        "R2": float(r2),
        "intercept": float(intercept),
        "slope": float(slope),
        "points_used": points_used,
        "points_discarded": points_discarded,
        "points_table": table,
        "total_points": len(table),
        "valid_points": used_count,
        "discarded_points": len(points_discarded),
        "fit_quality": fit_quality,
        **range_payload,
        "induction_model_regime": "single_arrhenius",
        "criteria": {
            "min_points": min_points,
            "min_temp_span_k": min_temp_span_k,
            "min_r2": min_r2,
        },
    }
