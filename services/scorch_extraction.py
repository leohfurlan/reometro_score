import math
from typing import Dict, Iterable, List

import numpy as np

R_GAS = 8.314462618
DEFAULT_TIME_FLOOR_S = 5.0


def _as_float_array(values):
    return np.asarray(values if values is not None else [], dtype=float)


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
            "reason": "empty_curve",
        }

    t = time_arr[:size]
    q = torque_arr[:size]
    mask = np.isfinite(t) & np.isfinite(q) & (t >= time_floor)
    candidates = np.where(mask)[0]

    if candidates.size == 0:
        return {
            "valid": False,
            "t_scorch": None,
            "torque_min": None,
            "index": None,
            "used_time_floor": time_floor,
            "reason": "insufficient_points_after_time_floor",
        }

    idx_local = int(np.argmin(q[mask]))
    idx = int(candidates[idx_local])
    return {
        "valid": True,
        "t_scorch": float(t[idx]),
        "torque_min": float(q[idx]),
        "index": idx,
        "used_time_floor": time_floor,
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


def fit_arrhenius_from_scorch(points: Iterable[Dict]):
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
    points_used: List[Dict] = []
    for item in points:
        valid = bool(item.get("valid"))
        t_scorch = float(item.get("t_scorch")) if item.get("t_scorch") is not None else math.nan
        temp_k = float(item.get("T_K")) if item.get("T_K") is not None else math.nan

        if (not valid) or (not np.isfinite(t_scorch)) or (not np.isfinite(temp_k)):
            continue
        if t_scorch <= 0.0 or temp_k <= 0.0:
            continue

        inv_t = 1.0 / temp_k
        ln_t = math.log(t_scorch)
        points_used.append(
            {
                "COD_ENSAIO": item.get("COD_ENSAIO"),
                "T_K": float(temp_k),
                "t_scorch": float(t_scorch),
                "inv_T": float(inv_t),
                "ln_t_scorch": float(ln_t),
            }
        )

    if len(points_used) < 2:
        return {
            "success": False,
            "message": "Pontos insuficientes para ajuste Arrhenius de scorch.",
            "A_ind": None,
            "E_ind": None,
            "R2": None,
            "intercept": None,
            "slope": None,
            "points_used": points_used,
        }

    x = np.asarray([p["inv_T"] for p in points_used], dtype=float)
    y = np.asarray([p["ln_t_scorch"] for p in points_used], dtype=float)
    slope, intercept = np.polyfit(x, y, deg=1)
    y_pred = intercept + (slope * x)

    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot <= 1e-15:
        r2 = 1.0 if ss_res <= 1e-15 else 0.0
    else:
        r2 = 1.0 - (ss_res / ss_tot)

    e_ind = float(slope * R_GAS)
    a_ind = float(math.exp(-intercept))

    return {
        "success": True,
        "message": "Ajuste Arrhenius de scorch concluido.",
        "A_ind": a_ind,
        "E_ind": e_ind,
        "R2": float(r2),
        "intercept": float(intercept),
        "slope": float(slope),
        "points_used": points_used,
    }
