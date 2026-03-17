import math
from typing import Dict, Iterable, List, Sequence

import numpy as np
from scipy.optimize import least_squares

R_GAS = 8.314462618

MODEL_FAMILY_EDO_ORDER_N_V1 = "edo_order_n_v1"
MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1 = "pinheiro_sigmoidal_v1"
MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1 = "pinheiro_sigmoidal_teq_v1"

SUPPORTED_MODEL_FAMILIES = (
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
)

DEFAULT_MODEL_FAMILY = MODEL_FAMILY_EDO_ORDER_N_V1
DEFAULT_MODEL_VERSION = "v1"
DEFAULT_FIT_METHOD = "least_squares"
DEFAULT_REFERENCE_TEMPERATURE_K = 433.15

CURE_COMPLETION_RULE_ALPHA_099 = "alpha_practical_0_99"
CURE_COMPLETION_RULE_ALPHA_098 = "alpha_practical_0_98"
CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL = "pinheiro_linear_tail_correction"
SUPPORTED_CURE_COMPLETION_RULES = (
    CURE_COMPLETION_RULE_ALPHA_099,
    CURE_COMPLETION_RULE_ALPHA_098,
    CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL,
)
DEFAULT_CURE_COMPLETION_RULE = CURE_COMPLETION_RULE_ALPHA_099

ALPHA_METRIC_TARGETS_DEFAULT = (0.30, 0.50, 0.90, 0.95, 0.99)


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def normalize_model_family(model_family):
    token = str(model_family or "").strip().lower()
    if token in SUPPORTED_MODEL_FAMILIES:
        return token
    return DEFAULT_MODEL_FAMILY


def normalize_fit_method(fit_method):
    token = str(fit_method or "").strip().lower()
    if token == DEFAULT_FIT_METHOD:
        return token
    return DEFAULT_FIT_METHOD


def normalize_cure_completion_rule(rule):
    token = str(rule or "").strip().lower()
    if token in SUPPORTED_CURE_COMPLETION_RULES:
        return token
    return DEFAULT_CURE_COMPLETION_RULE


def cure_completion_target_alpha(rule):
    rule_norm = normalize_cure_completion_rule(rule)
    if rule_norm == CURE_COMPLETION_RULE_ALPHA_098:
        return 0.98
    if rule_norm == CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL:
        # Linear-tail extrapolation is computed separately.
        return None
    return 0.99


def alpha_from_torque(torque, m_min, m_max):
    torque_arr = np.asarray(torque, dtype=float)
    delta = float(m_max) - float(m_min)
    if abs(delta) < 1e-12:
        alpha = np.zeros_like(torque_arr)
    else:
        alpha = (torque_arr - float(m_min)) / delta
    return np.clip(alpha, 0.0, 1.0)


def torque_from_alpha(alpha, m_min, m_max):
    alpha_arr = np.clip(np.asarray(alpha, dtype=float), 0.0, 1.0)
    return float(m_min) + (alpha_arr * (float(m_max) - float(m_min)))


def first_crossing_time(domain_vec: np.ndarray, alpha_vec: np.ndarray, alpha_target: float):
    domain = np.asarray(domain_vec, dtype=float)
    alpha = np.asarray(alpha_vec, dtype=float)
    size = min(domain.size, alpha.size)
    if size < 2:
        return None

    for i in range(1, size):
        a0 = float(alpha[i - 1])
        a1 = float(alpha[i])
        if a0 <= alpha_target <= a1:
            t0 = float(domain[i - 1])
            t1 = float(domain[i])
            if a1 == a0:
                return t1
            frac = (alpha_target - a0) / (a1 - a0)
            return t0 + frac * (t1 - t0)
    return None


def nearest_alpha_time(domain_vec: np.ndarray, alpha_vec: np.ndarray, alpha_target: float):
    domain = np.asarray(domain_vec, dtype=float)
    alpha = np.asarray(alpha_vec, dtype=float)
    size = min(domain.size, alpha.size)
    if size == 0:
        return None

    idx = int(np.argmin(np.abs(alpha[:size] - float(alpha_target))))
    return float(domain[idx])


def crossing_or_nearest_time(domain_vec: np.ndarray, alpha_vec: np.ndarray, alpha_target: float):
    cross = first_crossing_time(domain_vec, alpha_vec, alpha_target)
    if cross is not None:
        return float(cross)
    return nearest_alpha_time(domain_vec, alpha_vec, alpha_target)


def _prepare_time_temperature(time_s, temperature_k):
    time = np.asarray(time_s, dtype=float)
    temp = np.asarray(temperature_k, dtype=float)

    if time.ndim != 1:
        time = np.ravel(time)
    if temp.ndim != 1:
        temp = np.ravel(temp)

    if time.size == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    if temp.size == 1 and time.size > 1:
        temp = np.full_like(time, fill_value=float(temp[0]), dtype=float)

    size = min(time.size, temp.size)
    if size == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    time = time[:size]
    temp = temp[:size]

    time = np.maximum.accumulate(np.maximum(time, 0.0))
    temp = np.maximum(temp, 1.0)
    return time, temp


def _safe_ref_temp(reference_temperature_k):
    ref_k = _safe_float(reference_temperature_k, default=DEFAULT_REFERENCE_TEMPERATURE_K)
    if ref_k is None:
        return float(DEFAULT_REFERENCE_TEMPERATURE_K)
    return float(max(ref_k, 1.0))


def arrhenius_reference_factor(temperature_k, ea, reference_temperature_k):
    temp_safe = np.maximum(np.asarray(temperature_k, dtype=float), 1.0)
    ea_val = float(max(_safe_float(ea, default=0.0), 0.0))
    t_ref = _safe_ref_temp(reference_temperature_k)
    # Physical sign convention: T > T_ref must accelerate cure (factor > 1).
    exponent_raw = -(ea_val / R_GAS) * ((1.0 / temp_safe) - (1.0 / t_ref))
    return np.exp(np.clip(exponent_raw, -700.0, 700.0))


def compute_equivalent_time(time_s, temperature_k, ea, reference_temperature_k):
    time, temp = _prepare_time_temperature(time_s, temperature_k)
    if time.size == 0:
        return np.asarray([], dtype=float)

    dt = np.diff(time, prepend=time[0])
    dt[0] = 0.0
    dt = np.maximum(dt, 0.0)

    factor = arrhenius_reference_factor(temp, ea=ea, reference_temperature_k=reference_temperature_k)

    t_eq = np.cumsum(factor * dt)
    return np.maximum(t_eq, 0.0)


def _arrhenius_k_legacy_k0(temp_k, k0, ea):
    temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
    exponent = np.clip(-float(ea) / (R_GAS * temp_safe), -700.0, 700.0)
    k_t = float(max(k0, 1e-300)) * np.exp(exponent)
    return np.maximum(k_t, 0.0)


def _arrhenius_k_reference(temp_k, k_ref, ea, reference_temperature_k):
    temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
    factor = arrhenius_reference_factor(
        temp_safe,
        ea=ea,
        reference_temperature_k=reference_temperature_k,
    )
    k_t = float(max(k_ref, 1e-300)) * factor
    return np.maximum(k_t, 0.0)


def _edo_closed_form_isothermal(time_s, k_t, n):
    time = np.maximum(np.asarray(time_s, dtype=float), 0.0)
    k = np.maximum(np.asarray(k_t, dtype=float), 0.0)
    n_val = float(np.clip(float(n), 0.2, 12.0))

    if abs(n_val - 1.0) <= 1e-9:
        alpha = 1.0 - np.exp(-k * time)
        return np.clip(alpha, 0.0, 1.0)

    base = 1.0 + ((n_val - 1.0) * k * time)
    base = np.maximum(base, 1e-300)
    alpha = 1.0 - np.power(base, -1.0 / (n_val - 1.0))
    return np.clip(alpha, 0.0, 1.0)


def _edo_integrated_nonisothermal(time_s, temp_k, k0, ea, n):
    time, temp = _prepare_time_temperature(time_s, temp_k)
    size = time.size
    if size == 0:
        return np.asarray([], dtype=float)

    k_t = _arrhenius_k_legacy_k0(temp, k0=k0, ea=ea)
    n_val = float(np.clip(float(n), 0.2, 12.0))

    alpha = np.zeros((size,), dtype=float)
    for i in range(1, size):
        dt = max(float(time[i] - time[i - 1]), 0.0)
        if dt <= 0.0:
            alpha[i] = alpha[i - 1]
            continue

        a_prev = float(np.clip(alpha[i - 1], 0.0, 1.0 - 1e-12))
        k_i = float(max(k_t[i], 0.0))
        if k_i <= 0.0:
            alpha[i] = a_prev
            continue

        if abs(n_val - 1.0) <= 1e-9:
            one_minus_next = (1.0 - a_prev) * math.exp(-k_i * dt)
            alpha[i] = 1.0 - one_minus_next
            continue

        one_minus_prev = max(1.0 - a_prev, 1e-12)
        rhs = (one_minus_prev ** (1.0 - n_val)) + ((n_val - 1.0) * k_i * dt)
        rhs = max(rhs, 1e-300)
        one_minus_next = rhs ** (1.0 / (1.0 - n_val))
        alpha[i] = 1.0 - one_minus_next

    return np.clip(alpha, 0.0, 1.0)


def _sigmoid_from_drive(drive):
    d = np.maximum(np.asarray(drive, dtype=float), 0.0)
    return d / (1.0 + d)


def _pinheiro_drive_isothermal(time_s, temp_k, k_ref, ea, n, reference_temperature_k):
    time, temp = _prepare_time_temperature(time_s, temp_k)
    k_t = _arrhenius_k_reference(temp, k_ref=k_ref, ea=ea, reference_temperature_k=reference_temperature_k)
    n_val = float(np.clip(float(n), 0.2, 12.0))

    t_safe = np.maximum(time, 0.0)
    ln_x = np.log(np.maximum(k_t, 1e-300)) + (n_val * np.log(np.maximum(t_safe, 1e-300)))
    x = np.exp(np.clip(ln_x, -700.0, 700.0))
    x = np.where((t_safe <= 0.0) | (k_t <= 0.0), 0.0, x)
    return np.maximum(x, 0.0)


def _pinheiro_drive_nonisothermal(time_s, temp_k, k_ref, ea, n, reference_temperature_k):
    time, temp = _prepare_time_temperature(time_s, temp_k)
    k_t = _arrhenius_k_reference(temp, k_ref=k_ref, ea=ea, reference_temperature_k=reference_temperature_k)
    n_val = float(np.clip(float(n), 0.2, 12.0))

    t_pow_n = np.power(np.maximum(time, 0.0), n_val)
    dt_pow_n = np.diff(t_pow_n, prepend=t_pow_n[0])
    dt_pow_n[0] = 0.0
    dt_pow_n = np.maximum(dt_pow_n, 0.0)

    drive = np.cumsum(k_t * dt_pow_n)
    return np.maximum(drive, 0.0)


def _pinheiro_teq_drive(time_s, temp_k, k_ref, ea, n, reference_temperature_k):
    t_eq = compute_equivalent_time(
        time_s=time_s,
        temperature_k=temp_k,
        ea=ea,
        reference_temperature_k=reference_temperature_k,
    )
    n_val = float(np.clip(float(n), 0.2, 12.0))
    t_eq_safe = np.maximum(t_eq, 0.0)
    ln_x = np.log(max(float(k_ref), 1e-300)) + (n_val * np.log(np.maximum(t_eq_safe, 1e-300)))
    x = np.exp(np.clip(ln_x, -700.0, 700.0))
    x = np.where(t_eq_safe <= 0.0, 0.0, x)
    return np.maximum(x, 0.0)


def predict_alpha_isothermal(
    time_s,
    temperature_k,
    params,
    model_family,
    reference_temperature_k=DEFAULT_REFERENCE_TEMPERATURE_K,
):
    family = normalize_model_family(model_family)
    p = dict(params or {})
    n = float(np.clip(_safe_float(p.get("n"), default=1.2), 0.2, 12.0))
    ea = float(max(_safe_float(p.get("Ea"), default=80000.0), 0.0))

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        k0 = float(max(_safe_float(p.get("k0", p.get("k_ref")), default=1e-6), 1e-300))
        time, temp = _prepare_time_temperature(time_s, temperature_k)
        k_t = _arrhenius_k_legacy_k0(temp, k0=k0, ea=ea)
        return _edo_closed_form_isothermal(time, k_t=k_t, n=n)

    if family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1:
        k_ref = float(max(_safe_float(p.get("k_ref", p.get("k")), default=1e-6), 1e-300))
        drive = _pinheiro_drive_isothermal(
            time_s=time_s,
            temp_k=temperature_k,
            k_ref=k_ref,
            ea=ea,
            n=n,
            reference_temperature_k=reference_temperature_k,
        )
        return _sigmoid_from_drive(drive)

    k_ref = float(max(_safe_float(p.get("k_ref", p.get("k")), default=1e-6), 1e-300))
    drive = _pinheiro_teq_drive(
        time_s=time_s,
        temp_k=temperature_k,
        k_ref=k_ref,
        ea=ea,
        n=n,
        reference_temperature_k=reference_temperature_k,
    )
    return _sigmoid_from_drive(drive)


def predict_alpha_nonisothermal(
    time_s,
    temperature_k,
    params,
    model_family,
    reference_temperature_k=DEFAULT_REFERENCE_TEMPERATURE_K,
):
    family = normalize_model_family(model_family)
    p = dict(params or {})
    n = float(np.clip(_safe_float(p.get("n"), default=1.2), 0.2, 12.0))
    ea = float(max(_safe_float(p.get("Ea"), default=80000.0), 0.0))

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        k0 = float(max(_safe_float(p.get("k0", p.get("k_ref")), default=1e-6), 1e-300))
        return _edo_integrated_nonisothermal(time_s, temperature_k, k0=k0, ea=ea, n=n)

    if family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1:
        k_ref = float(max(_safe_float(p.get("k_ref", p.get("k")), default=1e-6), 1e-300))
        drive = _pinheiro_drive_nonisothermal(
            time_s=time_s,
            temp_k=temperature_k,
            k_ref=k_ref,
            ea=ea,
            n=n,
            reference_temperature_k=reference_temperature_k,
        )
        return _sigmoid_from_drive(drive)

    k_ref = float(max(_safe_float(p.get("k_ref", p.get("k")), default=1e-6), 1e-300))
    drive = _pinheiro_teq_drive(
        time_s=time_s,
        temp_k=temperature_k,
        k_ref=k_ref,
        ea=ea,
        n=n,
        reference_temperature_k=reference_temperature_k,
    )
    return _sigmoid_from_drive(drive)


def _is_effectively_isothermal(temperature_k):
    temp = np.asarray(temperature_k, dtype=float)
    if temp.size <= 1:
        return True
    return bool(np.nanmax(temp) - np.nanmin(temp) <= 1e-6)


def predict_alpha(
    time_s,
    temperature_k,
    params,
    model_family,
    reference_temperature_k=DEFAULT_REFERENCE_TEMPERATURE_K,
):
    if _is_effectively_isothermal(temperature_k):
        return predict_alpha_isothermal(
            time_s=time_s,
            temperature_k=temperature_k,
            params=params,
            model_family=model_family,
            reference_temperature_k=reference_temperature_k,
        )
    return predict_alpha_nonisothermal(
        time_s=time_s,
        temperature_k=temperature_k,
        params=params,
        model_family=model_family,
        reference_temperature_k=reference_temperature_k,
    )


def _extract_curve_training_data(curve):
    t_raw = curve.get("t_rel")
    if t_raw is None:
        t_raw = []
    alpha_raw = curve.get("alpha")
    if alpha_raw is None:
        alpha_raw = []

    t = np.asarray(t_raw, dtype=float)
    alpha = np.asarray(alpha_raw, dtype=float)
    size = min(t.size, alpha.size)
    if size < 2:
        return None

    t = np.maximum.accumulate(np.maximum(t[:size], 0.0))
    alpha = np.clip(alpha[:size], 0.0, 1.0)

    temp_profile = curve.get("temp_k_profile")
    if temp_profile is None:
        temp_k = np.full((size,), fill_value=float(curve.get("T_K") or 298.15), dtype=float)
    else:
        temp_raw = np.asarray(temp_profile, dtype=float)
        if temp_raw.size == 1:
            temp_k = np.full((size,), fill_value=float(temp_raw[0]), dtype=float)
        else:
            temp_k = np.full((size,), fill_value=float(curve.get("T_K") or 298.15), dtype=float)
            lim = min(size, temp_raw.size)
            temp_k[:lim] = temp_raw[:lim]

    temp_k = np.maximum(temp_k, 1.0)
    return {
        "COD_ENSAIO": curve.get("COD_ENSAIO"),
        "time_s": t,
        "alpha_real": alpha,
        "temperature_k": temp_k,
    }


def _default_reference_temperature_k(curves_data):
    temps = []
    for curve in curves_data:
        temp = np.asarray(curve["temperature_k"], dtype=float)
        if temp.size:
            temps.append(float(np.median(temp)))
    if not temps:
        return float(DEFAULT_REFERENCE_TEMPERATURE_K)
    return float(max(np.median(np.asarray(temps, dtype=float)), 1.0))


def _estimate_t50_from_mean_curve(curves_data, model_family, reference_temperature_k, ea_guess):
    domains = []
    alphas = []
    max_domain = 0.0

    for curve in curves_data:
        alpha = np.asarray(curve["alpha_real"], dtype=float)
        time = np.asarray(curve["time_s"], dtype=float)
        temp = np.asarray(curve["temperature_k"], dtype=float)

        if model_family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1:
            domain = compute_equivalent_time(time, temp, ea_guess, reference_temperature_k)
        else:
            domain = time

        size = min(domain.size, alpha.size)
        if size < 2:
            continue

        domain = np.maximum.accumulate(np.maximum(domain[:size], 0.0))
        alpha = np.clip(alpha[:size], 0.0, 1.0)

        keep = np.concatenate(([True], np.diff(domain) > 1e-12))
        domain = domain[keep]
        alpha = alpha[keep]
        if domain.size < 2:
            continue

        max_domain = max(max_domain, float(domain[-1]))
        domains.append(domain)
        alphas.append(alpha)

    if not domains or max_domain <= 0.0:
        return None

    grid_size = int(np.clip(max_domain / 1.0, 240, 1500))
    grid = np.linspace(0.0, max_domain, grid_size, dtype=float)

    aligned = []
    for domain, alpha in zip(domains, alphas):
        aligned.append(np.interp(grid, domain, alpha, left=alpha[0], right=alpha[-1]))

    if not aligned:
        return None

    alpha_mean = np.mean(np.asarray(aligned, dtype=float), axis=0)
    t50 = first_crossing_time(grid, alpha_mean, 0.50)
    if t50 is None:
        t50 = nearest_alpha_time(grid, alpha_mean, 0.50)
    if t50 is None or t50 <= 0.0:
        return None
    return float(t50)


def _k_guess_from_t50(curves_data, model_family, reference_temperature_k, ea_guess, n_guess):
    n_safe = float(np.clip(float(n_guess), 0.5, 4.0))
    t50_mean = _estimate_t50_from_mean_curve(
        curves_data,
        model_family=model_family,
        reference_temperature_k=reference_temperature_k,
        ea_guess=ea_guess,
    )
    if t50_mean is not None and t50_mean > 0.0:
        return float(np.clip(1.0 / max(float(t50_mean) ** n_safe, 1e-12), 1e-12, 1e8))

    # Fallback for sparse/noisy data: median of per-curve t50.
    k_guesses = []
    for curve in curves_data:
        alpha = np.asarray(curve["alpha_real"], dtype=float)
        time = np.asarray(curve["time_s"], dtype=float)
        temp = np.asarray(curve["temperature_k"], dtype=float)

        if model_family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1:
            domain = compute_equivalent_time(time, temp, ea_guess, reference_temperature_k)
        else:
            domain = time

        t50 = first_crossing_time(domain, alpha, 0.50)
        if t50 is None or t50 <= 0:
            continue
        k_guesses.append(1.0 / max(float(t50) ** n_safe, 1e-12))

    if not k_guesses:
        return 1e-4
    return float(np.clip(np.median(np.asarray(k_guesses, dtype=float)), 1e-12, 1e8))


def fit_model_parameters(
    curves,
    model_family=DEFAULT_MODEL_FAMILY,
    reference_temperature_k=None,
    fit_method=DEFAULT_FIT_METHOD,
):
    family = normalize_model_family(model_family)
    fit_method_norm = normalize_fit_method(fit_method)

    curves_data = []
    for curve in (curves or []):
        parsed = _extract_curve_training_data(curve)
        if parsed is not None:
            curves_data.append(parsed)

    total_points = int(sum(len(c["time_s"]) for c in curves_data))
    if total_points < 12:
        return {
            "success": False,
            "message": "Pontos insuficientes para ajuste. Selecione mais ensaios.",
            "fit_method": fit_method_norm,
            "model_family": family,
            "model_version": DEFAULT_MODEL_VERSION,
            "reference_temperature_K": float(_safe_ref_temp(reference_temperature_k)),
        }

    if reference_temperature_k is None:
        reference_temperature_k = _default_reference_temperature_k(curves_data)
    reference_temperature_k = _safe_ref_temp(reference_temperature_k)

    ea0 = 70000.0
    n0 = 1.5

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        k_name = "k0"
        k0_guess = 1e-3
    else:
        k_name = "k_ref"
        k0_guess = _k_guess_from_t50(
            curves_data,
            model_family=family,
            reference_temperature_k=reference_temperature_k,
            ea_guess=ea0,
            n_guess=n0,
        )

    p0 = np.asarray([math.log(max(k0_guess, 1e-12)), ea0, n0], dtype=float)
    lb = np.asarray([math.log(1e-12), 30000.0, 0.5], dtype=float)
    ub = np.asarray([math.log(1e8), 120000.0, 4.0], dtype=float)

    def residuals(p):
        k_val = float(math.exp(p[0]))
        ea = float(p[1])
        n = float(p[2])

        params = {k_name: k_val, "Ea": ea, "n": n}
        if family == MODEL_FAMILY_EDO_ORDER_N_V1:
            params["k_ref"] = k_val

        out = []
        for curve in curves_data:
            pred = predict_alpha(
                time_s=curve["time_s"],
                temperature_k=curve["temperature_k"],
                params=params,
                model_family=family,
                reference_temperature_k=reference_temperature_k,
            )
            out.append(pred - curve["alpha_real"])

        if not out:
            return np.asarray([], dtype=float)
        return np.concatenate(out)

    result = least_squares(
        residuals,
        p0,
        bounds=(lb, ub),
        method="trf",
        max_nfev=4000,
    )

    k_opt = float(math.exp(result.x[0]))
    ea_opt = float(result.x[1])
    n_opt = float(result.x[2])

    params_opt = {k_name: k_opt, "Ea": ea_opt, "n": n_opt}
    if family != MODEL_FAMILY_EDO_ORDER_N_V1:
        params_opt["k"] = k_opt
    else:
        params_opt["k_ref"] = k_opt

    residual_vec = np.asarray(result.fun, dtype=float)
    rmse_alpha = float(np.sqrt(np.mean(np.square(residual_vec)))) if residual_vec.size else float("nan")

    return {
        "success": bool(result.success),
        "message": str(result.message),
        "fit_method": fit_method_norm,
        "model_family": family,
        "model_version": DEFAULT_MODEL_VERSION,
        "reference_temperature_K": float(reference_temperature_k),
        "model_parameters": params_opt,
        k_name: float(k_opt),
        "Ea": ea_opt,
        "n": n_opt,
        "rmse_alpha": rmse_alpha,
        "nfev": int(result.nfev),
        "cost": float(result.cost),
    }


def _linear_tail_completion_time(domain_vec, alpha_vec, n_value=None):
    domain = np.asarray(domain_vec, dtype=float)
    alpha = np.asarray(alpha_vec, dtype=float)
    size = min(domain.size, alpha.size)
    if size < 3:
        return None

    domain = domain[:size]
    alpha = alpha[:size]

    n_eff = float(np.clip(_safe_float(n_value, default=1.0), 0.2, 12.0))
    x = np.power(np.maximum(domain, 0.0), n_eff)
    mask = (alpha >= 0.85) & (alpha < 0.995) & np.isfinite(x) & np.isfinite(alpha)
    if int(np.count_nonzero(mask)) < 2:
        return None

    x_tail = x[mask]
    alpha_tail = alpha[mask]
    y_tail = 1.0 / np.maximum(1.0 - alpha_tail, 1e-9)

    a, b = np.polyfit(x_tail, y_tail, deg=1)
    if not np.isfinite(a) or a <= 0.0:
        return None

    y_target = 1.0 / (1.0 - 0.999)
    x_target = (y_target - b) / a
    if (not np.isfinite(x_target)) or (x_target <= 0.0):
        return None

    t_target = x_target ** (1.0 / n_eff)
    if not np.isfinite(t_target):
        return None
    return float(max(t_target, 0.0))


def estimate_cure_completion_time(domain_vec, alpha_vec, cure_completion_rule, n_value=None):
    rule = normalize_cure_completion_rule(cure_completion_rule)
    if rule == CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL:
        corrected = _linear_tail_completion_time(domain_vec, alpha_vec, n_value=n_value)
        if corrected is not None:
            return corrected
        # Fall back to practical alpha if tail correction cannot be estimated.
        return crossing_or_nearest_time(domain_vec, alpha_vec, 0.99)

    alpha_target = cure_completion_target_alpha(rule)
    return crossing_or_nearest_time(domain_vec, alpha_vec, alpha_target)


def compute_txx_errors(time_vec, alpha_real, alpha_model, targets=None):
    time = np.asarray(time_vec, dtype=float)
    real = np.asarray(alpha_real, dtype=float)
    model = np.asarray(alpha_model, dtype=float)
    size = min(time.size, real.size, model.size)
    if size == 0:
        return []

    time = time[:size]
    real = real[:size]
    model = model[:size]

    out = []
    for target in (targets or ALPHA_METRIC_TARGETS_DEFAULT):
        t_real = first_crossing_time(time, real, float(target))
        t_model = first_crossing_time(time, model, float(target))
        out.append(
            {
                "alpha_target": float(target),
                "time_real_s": float(t_real) if t_real is not None else None,
                "time_model_s": float(t_model) if t_model is not None else None,
                "error_s": float(t_model - t_real) if (t_real is not None and t_model is not None) else None,
            }
        )
    return out


def extract_model_parameters(payload, model_family=None):
    family = normalize_model_family(model_family or (payload or {}).get("model_family"))
    raw = dict((payload or {}).get("model_parameters") or {})

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        k0 = _safe_float(raw.get("k0"), default=None)
        if k0 is None:
            k0 = _safe_float((payload or {}).get("k0"), default=None)
        if k0 is None:
            k0 = _safe_float(raw.get("k_ref"), default=1e-3)
        ea = _safe_float(raw.get("Ea"), default=None)
        if ea is None:
            ea = _safe_float((payload or {}).get("Ea"), default=80000.0)
        n = _safe_float(raw.get("n"), default=None)
        if n is None:
            n = _safe_float((payload or {}).get("n"), default=1.2)
        return {
            "k0": float(max(k0, 1e-300)),
            "k_ref": float(max(k0, 1e-300)),
            "Ea": float(max(ea, 0.0)),
            "n": float(np.clip(n, 0.2, 12.0)),
        }

    k_ref = _safe_float(raw.get("k_ref"), default=None)
    if k_ref is None:
        k_ref = _safe_float(raw.get("k"), default=None)
    if k_ref is None:
        k_ref = _safe_float((payload or {}).get("k_ref"), default=None)
    if k_ref is None:
        # Legacy compatibility when only k0 was persisted.
        k_ref = _safe_float((payload or {}).get("k0"), default=1e-3)

    ea = _safe_float(raw.get("Ea"), default=None)
    if ea is None:
        ea = _safe_float((payload or {}).get("Ea"), default=80000.0)

    n = _safe_float(raw.get("n"), default=None)
    if n is None:
        n = _safe_float((payload or {}).get("n"), default=1.2)

    return {
        "k_ref": float(max(k_ref, 1e-300)),
        "k": float(max(k_ref, 1e-300)),
        "Ea": float(max(ea, 0.0)),
        "n": float(np.clip(n, 0.2, 12.0)),
    }


def update_model_parameters(payload, *, k_value, ea, n):
    family = normalize_model_family((payload or {}).get("model_family"))
    params = extract_model_parameters(payload, model_family=family)

    n_safe = float(np.clip(float(n), 0.2, 12.0))
    ea_safe = float(max(float(ea), 0.0))
    k_safe = float(max(float(k_value), 1e-300))

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        params["k0"] = k_safe
        params["k_ref"] = k_safe
        params.pop("k", None)
    else:
        params["k_ref"] = k_safe
        params["k"] = k_safe
        params.pop("k0", None)

    params["Ea"] = ea_safe
    params["n"] = n_safe

    out = dict(payload or {})
    out["model_parameters"] = params
    out["Ea"] = ea_safe
    out["n"] = n_safe

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        out["k0"] = k_safe
        out.pop("k_ref", None)
    else:
        out["k_ref"] = k_safe
        out["k0"] = k_safe  # Compatibility alias for legacy consumers.

    return out


__all__ = [
    "R_GAS",
    "MODEL_FAMILY_EDO_ORDER_N_V1",
    "MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1",
    "MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1",
    "SUPPORTED_MODEL_FAMILIES",
    "DEFAULT_MODEL_FAMILY",
    "DEFAULT_MODEL_VERSION",
    "DEFAULT_FIT_METHOD",
    "DEFAULT_REFERENCE_TEMPERATURE_K",
    "CURE_COMPLETION_RULE_ALPHA_099",
    "CURE_COMPLETION_RULE_ALPHA_098",
    "CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL",
    "SUPPORTED_CURE_COMPLETION_RULES",
    "DEFAULT_CURE_COMPLETION_RULE",
    "ALPHA_METRIC_TARGETS_DEFAULT",
    "normalize_model_family",
    "normalize_fit_method",
    "normalize_cure_completion_rule",
    "cure_completion_target_alpha",
    "alpha_from_torque",
    "torque_from_alpha",
    "first_crossing_time",
    "nearest_alpha_time",
    "crossing_or_nearest_time",
    "arrhenius_reference_factor",
    "compute_equivalent_time",
    "predict_alpha_isothermal",
    "predict_alpha_nonisothermal",
    "predict_alpha",
    "fit_model_parameters",
    "estimate_cure_completion_time",
    "compute_txx_errors",
    "extract_model_parameters",
    "update_model_parameters",
]
