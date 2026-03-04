import math
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import least_squares

R_GAS = 8.314462618
ALPHAS_REF_DEFAULT = [0.10, 0.25, 0.30, 0.45, 0.50, 0.60, 0.90]


def first_crossing_time(time_vec: np.ndarray, alpha_vec: np.ndarray, alpha_target: float):
    if len(time_vec) < 2:
        return None
    for i in range(1, len(time_vec)):
        a0 = float(alpha_vec[i - 1])
        a1 = float(alpha_vec[i])
        if a0 <= alpha_target <= a1:
            t0 = float(time_vec[i - 1])
            t1 = float(time_vec[i])
            if a1 == a0:
                return t1
            frac = (alpha_target - a0) / (a1 - a0)
            return t0 + frac * (t1 - t0)
    return None


def parametrize_curve(tempo: np.ndarray, torque: np.ndarray, ml: float, mh: float, t_min_tempo: float):
    t_rel = np.maximum(tempo - float(t_min_tempo), 0.0)
    delta = float(mh) - float(ml)
    if abs(delta) < 1e-12:
        alpha = np.zeros_like(t_rel)
    else:
        alpha = (torque - float(ml)) / delta
    alpha = np.clip(alpha, 0.0, 1.0)
    return t_rel, alpha


def build_fit_dataset(curves: List[Dict], alphas_ref=None):
    alphas_ref = alphas_ref or ALPHAS_REF_DEFAULT
    t_obs = []
    t_kelvin = []
    alpha_obs = []
    by_curve = []

    for curve in curves:
        crossings = []
        for a_ref in alphas_ref:
            t_cross = first_crossing_time(curve["t_rel"], curve["alpha"], a_ref)
            if t_cross is not None and t_cross > 0:
                t_obs.append(t_cross)
                t_kelvin.append(curve["T_K"])
                alpha_obs.append(a_ref)
                crossings.append({"alpha_ref": a_ref, "t_cross": t_cross})
        by_curve.append({"COD_ENSAIO": curve["COD_ENSAIO"], "crossings": crossings})

    return np.array(t_obs, dtype=float), np.array(t_kelvin, dtype=float), np.array(alpha_obs, dtype=float), by_curve


def alpha_model(t: np.ndarray, t_kelvin: np.ndarray, k0: float, ea: float, n: float):
    temp_safe = np.maximum(t_kelvin, 1.0)
    n_safe = float(np.clip(n, 0.5, 12.0))
    k_t = k0 * np.exp(-ea / (R_GAS * temp_safe))

    # x = (k*t)^n = exp(n * ln(k*t)); using logs avoids power overflow in wide ranges.
    ln_kt = np.log(np.maximum(k_t, 1e-300)) + np.log(np.maximum(t, 1e-12))
    exp_arg = np.clip(n_safe * ln_kt, -700.0, 700.0)
    x = np.exp(exp_arg)
    return x / (1.0 + x)


def fit_kinetics(curves: List[Dict]):
    t_obs, t_kelvin, alpha_target, crossing_debug = build_fit_dataset(curves)
    if len(t_obs) < 6:
        return {
            "success": False,
            "message": "Pontos insuficientes para ajuste. Selecione mais ensaios.",
            "crossing_debug": crossing_debug,
        }

    def residuals(p):
        k0 = math.exp(p[0])
        ea = p[1]
        n = p[2]
        pred = alpha_model(t_obs, t_kelvin, k0, ea, n)
        return pred - alpha_target

    p0 = np.array([math.log(1e6), 80000.0, 1.2], dtype=float)
    lb = np.array([math.log(1e-8), 50000.0, 0.5], dtype=float)
    ub = np.array([math.log(1e15), 250000.0, 4.0], dtype=float)

    result = least_squares(residuals, p0, bounds=(lb, ub), method="trf", max_nfev=3000)
    k0 = math.exp(result.x[0])
    ea = float(result.x[1])
    n = float(result.x[2])

    rmse = float(np.sqrt(np.mean(np.square(result.fun))))

    return {
        "success": bool(result.success),
        "message": result.message,
        "k0": float(k0),
        "Ea": ea,
        "n": n,
        "rmse_alpha": rmse,
        "crossing_debug": crossing_debug,
    }


def alpha_from_timeline(time: np.ndarray, temp_k: np.ndarray, k0: float, ea: float, n: float):
    time = np.asarray(time, dtype=float)
    temp_k = np.asarray(temp_k, dtype=float)
    dt = np.diff(time, prepend=time[0])
    dt[0] = 0.0
    k_t = np.maximum(k0 * np.exp(-ea / (R_GAS * np.maximum(temp_k, 1.0))), 0.0)
    n_safe = float(np.clip(n, 0.5, 12.0))
    z = np.cumsum(np.power(k_t, 1.0 / n_safe) * dt)
    z_n = np.power(np.maximum(z, 0.0), n_safe)
    return z_n / (1.0 + z_n)
