import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, List, Sequence

import numpy as np
from scipy.optimize import least_squares

R_GAS = 8.314462618

MODEL_FAMILY_EDO_ORDER_N_V1 = "edo_order_n_v1"
MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1 = "pinheiro_sigmoidal_v1"
MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1 = "pinheiro_sigmoidal_teq_v1"
MODEL_FAMILY_LEROY2013_CONTINUOUS_V1 = "leroy2013_continuous_v1"
MODEL_FAMILY_AUTO = "auto"

AUTO_COMPARE_MODEL_FAMILIES = (
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
)

SUPPORTED_MODEL_FAMILIES = (
    MODEL_FAMILY_AUTO,
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
)

DEFAULT_MODEL_FAMILY = MODEL_FAMILY_EDO_ORDER_N_V1
DEFAULT_MODEL_VERSION = "v1"
DEFAULT_FIT_METHOD = "least_squares"
DEFAULT_REFERENCE_TEMPERATURE_K = 433.15

FIT_BOUNDS_LOGK = (math.log(1e-12), math.log(1e8))
FIT_BOUNDS_EA = (30000.0, 120000.0)
FIT_BOUNDS_N = (0.5, 4.0)

LEROY2013_EV_FIXED_DEFAULT = 90000.0
LEROY2013_ER_FIXED_DEFAULT = 180000.0
LEROY2013_FIT_BOUNDS = {
    "Av1": (1e-12, 1e4),
    "Av2": (1e-12, 4e4),
    "Ev": (4.0e4, 1.6e5),
    "X": (1e-4, 1.0 - 1e-4),
    "Ar": (0.0, 1e8),
    "Er": (8.0e4, 3.2e5),
}
LEROY2013_STAGE2_MIN_TEMPS = 3
LEROY2013_STAGE2_MIN_SPAN_K = 12.0
LEROY2013_REGULARIZATION_SCALE = 1e-4

CURE_COMPLETION_RULE_ALPHA_099 = "alpha_practical_0_99"
CURE_COMPLETION_RULE_ALPHA_098 = "alpha_practical_0_98"
CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL = "pinheiro_linear_tail_correction"
SUPPORTED_CURE_COMPLETION_RULES = (
    CURE_COMPLETION_RULE_ALPHA_099,
    CURE_COMPLETION_RULE_ALPHA_098,
    CURE_COMPLETION_RULE_PINHEIRO_LINEAR_TAIL,
)
DEFAULT_CURE_COMPLETION_RULE = CURE_COMPLETION_RULE_ALPHA_099

ALPHA_METRIC_TARGETS_DEFAULT = (0.10, 0.30, 0.50, 0.90, 0.95, 0.99)
PARAM_DECIMALS = 2


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def _round_half_up(value, decimals=PARAM_DECIMALS):
    numeric = _safe_float(value, default=None)
    if numeric is None:
        return None
    exp = "1." + ("0" * max(int(decimals), 0))
    try:
        q = Decimal(exp).scaleb(-int(decimals))
        rounded = Decimal(str(numeric)).quantize(q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return float(numeric)
    return float(rounded)


def _round_leroy_params(params):
    rounded = {}
    for key, value in dict(params or {}).items():
        numeric = _safe_float(value, default=None)
        if numeric is None:
            continue
        r = _round_half_up(numeric, decimals=PARAM_DECIMALS)
        rounded[key] = float(numeric if r is None else r)
    return rounded


def normalize_model_family(model_family):
    token = str(model_family or "").strip().lower()
    if token in SUPPORTED_MODEL_FAMILIES:
        return token
    return DEFAULT_MODEL_FAMILY


def _predict_model_family(model_family):
    family = normalize_model_family(model_family)
    if family == MODEL_FAMILY_AUTO:
        return DEFAULT_MODEL_FAMILY
    return family


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


def _leroy2013_param_defaults():
    return {
        "Av1": 1e-4,
        "Av2": 1e-2,
        "Ev": float(LEROY2013_EV_FIXED_DEFAULT),
        "X": 0.7,
        "Ar": 1e-3,
        "Er": float(LEROY2013_ER_FIXED_DEFAULT),
    }


def _leroy2013_param_bounds_for(keys):
    lower = []
    upper = []
    for key in keys:
        lo, hi = LEROY2013_FIT_BOUNDS[key]
        lower.append(float(lo))
        upper.append(float(hi))
    return np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)


def _leroy2013_sanitize_params(params):
    raw = dict(params or {})
    defaults = _leroy2013_param_defaults()
    out = {}
    for key, default in defaults.items():
        lo, hi = LEROY2013_FIT_BOUNDS[key]
        numeric = _safe_float(raw.get(key), default=default)
        if numeric is None:
            numeric = default
        out[key] = float(np.clip(float(numeric), float(lo), float(hi)))
    return out


def _leroy2013_arrhenius(temp_k, activation_energy):
    temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
    ea_val = float(max(_safe_float(activation_energy, default=0.0), 0.0))
    exponent = np.clip(-(ea_val / R_GAS) / temp_safe, -700.0, 700.0)
    return np.exp(exponent)


def integrate_leroy2013_states(
    time_s,
    temperature_k,
    params,
    *,
    alpha_v0=0.0,
    alpha_unstable0=0.0,
    max_internal_substeps=64,
):
    time, temp = _prepare_time_temperature(time_s, temperature_k)
    size = int(time.size)
    if size == 0:
        empty = np.asarray([], dtype=float)
        return {
            "time_s": empty,
            "temperature_k": empty,
            "alpha_v": empty,
            "alpha_stable": empty,
            "alpha_unstable": empty,
            "alpha_total": empty,
            "dalpha_v_dt": empty,
            "dalpha_unstable_dt": empty,
            "kr": empty,
        }

    p = _leroy2013_sanitize_params(params)
    av1 = float(p["Av1"])
    av2 = float(p["Av2"])
    ev = float(p["Ev"])
    x_stable = float(p["X"])
    ar = float(p["Ar"])
    er = float(p["Er"])
    max_substeps = int(max(1, int(max_internal_substeps)))

    alpha_v = np.zeros((size,), dtype=float)
    alpha_unstable = np.zeros((size,), dtype=float)

    alpha_v[0] = float(np.clip(_safe_float(alpha_v0, default=0.0), 0.0, 1.0))
    unstable_upper0 = (1.0 - x_stable) * alpha_v[0]
    alpha_unstable[0] = float(np.clip(_safe_float(alpha_unstable0, default=0.0), 0.0, unstable_upper0))

    for i in range(1, size):
        dt = max(float(time[i] - time[i - 1]), 0.0)
        if dt <= 0.0:
            alpha_v[i] = alpha_v[i - 1]
            alpha_unstable[i] = alpha_unstable[i - 1]
            continue

        av = float(alpha_v[i - 1])
        au = float(alpha_unstable[i - 1])
        temp_i = float(max(temp[i], 1.0))
        max_rate_guess = (av1 + av2 * max(av, 0.0)) * math.exp(np.clip(-ev / (R_GAS * temp_i), -700.0, 700.0))
        max_rate_guess = max(float(max_rate_guess), 1e-12)
        substeps = int(np.clip(math.ceil(dt * max_rate_guess * 8.0), 1, max_substeps))
        dt_inner = dt / float(substeps)

        for _ in range(substeps):
            kv = (av1 + av2 * av) * math.exp(np.clip(-ev / (R_GAS * temp_i), -700.0, 700.0))
            kv = max(float(kv), 0.0)
            dalpha_v_dt = kv * ((1.0 - av) ** 2)
            av_next = min(1.0, av + (dalpha_v_dt * dt_inner))
            if av_next < av:
                av_next = av

            kr_t = 0.0
            if ar > 0.0:
                kr_t = ar * math.exp(np.clip(-er / (R_GAS * temp_i), -700.0, 700.0))
                kr_t = max(float(kr_t), 0.0)

            dalpha_unstable_dt = ((1.0 - x_stable) * dalpha_v_dt) - (kr_t * au)
            au_next = au + (dalpha_unstable_dt * dt_inner)
            au_upper = max((1.0 - x_stable) * av_next, 0.0)
            au = float(np.clip(au_next, 0.0, au_upper))
            av = float(np.clip(av_next, 0.0, 1.0))

        alpha_v[i] = av
        alpha_unstable[i] = au

    alpha_v = np.maximum.accumulate(np.clip(alpha_v, 0.0, 1.0))
    alpha_unstable = np.maximum(alpha_unstable, 0.0)
    alpha_unstable = np.minimum(alpha_unstable, (1.0 - x_stable) * alpha_v)

    alpha_stable = np.clip(x_stable * alpha_v, 0.0, 1.0)
    alpha_total = np.clip(alpha_stable + alpha_unstable, 0.0, 1.0)

    kv_arr = np.maximum(av1 + (av2 * alpha_v), 0.0) * _leroy2013_arrhenius(temp, ev)
    dalpha_v_dt = kv_arr * np.square(np.maximum(1.0 - alpha_v, 0.0))
    kr = np.maximum(float(ar), 0.0) * _leroy2013_arrhenius(temp, er)
    dalpha_unstable_dt = ((1.0 - x_stable) * dalpha_v_dt) - (kr * alpha_unstable)

    return {
        "time_s": time,
        "temperature_k": temp,
        "alpha_v": alpha_v,
        "alpha_stable": alpha_stable,
        "alpha_unstable": alpha_unstable,
        "alpha_total": alpha_total,
        "dalpha_v_dt": np.maximum(dalpha_v_dt, 0.0),
        "dalpha_unstable_dt": dalpha_unstable_dt,
        "kr": np.maximum(kr, 0.0),
    }


def predict_alpha_leroy2013(
    time_s,
    temperature_k,
    params,
    *,
    return_states=False,
):
    states = integrate_leroy2013_states(
        time_s=time_s,
        temperature_k=temperature_k,
        params=params,
    )
    if return_states:
        return states
    return np.asarray(states["alpha_total"], dtype=float)


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
    family = _predict_model_family(model_family)
    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        return predict_alpha_leroy2013(
            time_s=time_s,
            temperature_k=temperature_k,
            params=params,
        )

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
    family = _predict_model_family(model_family)
    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        return predict_alpha_leroy2013(
            time_s=time_s,
            temperature_k=temperature_k,
            params=params,
        )

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
        "markers": dict(curve.get("markers") or {}),
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


def _prepare_training_curves(curves):
    curves_data = []
    for curve in (curves or []):
        parsed = _extract_curve_training_data(curve)
        if parsed is not None:
            curves_data.append(parsed)

    total_points = int(sum(len(c["time_s"]) for c in curves_data))
    return curves_data, total_points


def _leroy_isothermal_initial_guess(curve_data, ev_fixed, er_fixed):
    time = np.asarray(curve_data["time_s"], dtype=float)
    alpha = np.asarray(curve_data["alpha_real"], dtype=float)
    markers = dict(curve_data.get("markers") or {})
    t50 = _safe_float(markers.get("T50_s"), default=None)
    if t50 is None:
        t50 = first_crossing_time(time, alpha, 0.50)
    if t50 is None:
        t50 = nearest_alpha_time(time, alpha, 0.50)
    t50 = float(max(_safe_float(t50, default=60.0), 1.0))
    rate_guess = float(np.clip(1.0 / t50, 1e-8, 10.0))

    tail_size = int(np.clip(round(len(alpha) * 0.2), 4, max(len(alpha), 4)))
    alpha_tail = float(np.mean(alpha[-tail_size:])) if len(alpha) else 0.8
    x_guess = float(np.clip(0.45 + (0.35 * alpha_tail), 0.25, 0.95))

    return _leroy2013_sanitize_params(
        {
            "Av1": rate_guess,
            "Av2": max(rate_guess * 0.8, 1e-8),
            "Ev": ev_fixed,
            "X": x_guess,
            "Ar": max(rate_guess * 0.05, 0.0),
            "Er": er_fixed,
        }
    )


def _leroy_weights_for_curve(curve_data, residual_size):
    alpha = np.asarray(curve_data["alpha_real"], dtype=float)
    size = min(int(residual_size), int(alpha.size))
    if size <= 0:
        return np.asarray([], dtype=float)

    alpha = np.clip(alpha[:size], 0.0, 1.0)
    weights = np.ones((size,), dtype=float)

    weights[alpha <= 0.12] *= 1.8
    weights[alpha >= 0.98] *= 0.65

    for target in (0.10, 0.30, 0.50, 0.90):
        idx = int(np.argmin(np.abs(alpha - float(target))))
        low = max(idx - 2, 0)
        high = min(idx + 3, size)
        weights[low:high] *= 2.0

    return np.clip(weights, 0.2, 8.0)


def evaluate_parameter_identifiability(lsq_result, param_names, bounds=None):
    names = [str(name) for name in (param_names or [])]
    x_vec = np.asarray(getattr(lsq_result, "x", []), dtype=float)
    jac = np.asarray(getattr(lsq_result, "jac", []), dtype=float)

    if jac.ndim != 2:
        jac = np.asarray([], dtype=float).reshape(0, len(names))
    if jac.shape[1] != len(names):
        jac = np.asarray([], dtype=float).reshape(0, len(names))

    condition_indicator = None
    if jac.size:
        try:
            condition_indicator = float(np.linalg.cond(jac))
        except np.linalg.LinAlgError:
            condition_indicator = float("inf")

    high_pairs = []
    if jac.shape[0] >= 2 and jac.shape[1] >= 2:
        centered = jac - np.mean(jac, axis=0, keepdims=True)
        scales = np.std(centered, axis=0, ddof=1)
        valid = np.where(scales > 1e-15)[0]
        if valid.size >= 2:
            normalized = centered[:, valid] / scales[valid]
            corr = np.corrcoef(normalized, rowvar=False)
            for i in range(len(valid)):
                for j in range(i + 1, len(valid)):
                    corr_ij = float(corr[i, j])
                    if abs(corr_ij) >= 0.95:
                        high_pairs.append(
                            {
                                "param_a": names[int(valid[i])],
                                "param_b": names[int(valid[j])],
                                "correlation": corr_ij,
                            }
                        )

    parameters_near_bounds = []
    if bounds is not None and len(names) == len(x_vec):
        lb = np.asarray(bounds[0], dtype=float)
        ub = np.asarray(bounds[1], dtype=float)
        if lb.shape == x_vec.shape and ub.shape == x_vec.shape:
            span = np.maximum(ub - lb, 1e-12)
            margin_low = (x_vec - lb) / span
            margin_high = (ub - x_vec) / span
            for idx, name in enumerate(names):
                if float(min(margin_low[idx], margin_high[idx])) <= 0.03:
                    parameters_near_bounds.append(str(name))

    sensitivity = {}
    if jac.size and jac.shape[1] == len(names):
        norms = np.linalg.norm(jac, axis=0)
        max_norm = float(max(np.max(norms), 1e-12))
        for idx, name in enumerate(names):
            sensitivity[str(name)] = float(norms[idx] / max_norm)

    status = "ok"
    if (
        (condition_indicator is not None and np.isfinite(condition_indicator) and condition_indicator > 1e10)
        or len(high_pairs) >= 2
        or len(parameters_near_bounds) >= 2
    ):
        status = "poor"
    elif (
        (condition_indicator is not None and np.isfinite(condition_indicator) and condition_indicator > 1e7)
        or len(high_pairs) >= 1
        or len(parameters_near_bounds) >= 1
    ):
        status = "warning"

    return {
        "status": status,
        "parameters_near_bounds": parameters_near_bounds,
        "high_correlation_pairs": high_pairs,
        "condition_indicator": condition_indicator,
        "sensitivity": sensitivity,
    }


def _fit_leroy_parameters(
    curves_data,
    *,
    free_param_names,
    fixed_params=None,
    init_params=None,
    regularization_center=None,
    regularization_scale=LEROY2013_REGULARIZATION_SCALE,
    weighted=False,
    max_nfev=8000,
):
    free_names = [str(name) for name in free_param_names]
    if not free_names:
        return {
            "success": False,
            "message": "Nenhum parametro livre especificado para ajuste Leroy2013.",
            "model_parameters": _leroy2013_sanitize_params(init_params),
            "rmse_alpha": None,
            "cost": None,
            "cost_alpha_sse": None,
            "nfev": 0,
            "identifiability": {
                "status": "poor",
                "parameters_near_bounds": [],
                "high_correlation_pairs": [],
                "condition_indicator": None,
                "sensitivity": {},
            },
        }

    fixed = _leroy2013_sanitize_params(fixed_params)
    init = _leroy2013_sanitize_params(init_params)
    baseline = _leroy2013_sanitize_params({**fixed, **init})
    center = _leroy2013_sanitize_params(regularization_center) if regularization_center else dict(baseline)

    p0 = np.asarray([baseline[name] for name in free_names], dtype=float)
    lb, ub = _leroy2013_param_bounds_for(free_names)

    def _compose_params(p_vec):
        merged = dict(fixed)
        for idx, name in enumerate(free_names):
            merged[name] = float(p_vec[idx])
        return _leroy2013_sanitize_params(merged)

    def residuals(p_vec):
        params = _compose_params(p_vec)
        blocks = []
        for curve in curves_data:
            pred = np.asarray(
                predict_alpha_leroy2013(
                    time_s=curve["time_s"],
                    temperature_k=curve["temperature_k"],
                    params=params,
                ),
                dtype=float,
            )
            real = np.asarray(curve["alpha_real"], dtype=float)
            size = min(pred.size, real.size)
            if size <= 0:
                continue
            res = pred[:size] - real[:size]
            if weighted:
                weights = _leroy_weights_for_curve(curve, size)
                if weights.size == size:
                    res = res * weights
            blocks.append(res)

        if not blocks:
            return np.asarray([], dtype=float)

        residual_vec = np.concatenate(blocks)
        reg_scale = float(max(_safe_float(regularization_scale, default=0.0), 0.0))
        if reg_scale > 0.0:
            reg_terms = []
            for idx, name in enumerate(free_names):
                lo, hi = LEROY2013_FIT_BOUNDS[name]
                span = max(float(hi - lo), 1e-12)
                reg_terms.append((float(p_vec[idx]) - float(center[name])) / span)
            if reg_terms:
                residual_vec = np.concatenate(
                    [
                        residual_vec,
                        math.sqrt(reg_scale) * np.asarray(reg_terms, dtype=float),
                    ]
                )
        return residual_vec

    lsq = least_squares(
        residuals,
        p0,
        bounds=(lb, ub),
        method="trf",
        max_nfev=int(max_nfev),
    )
    params_opt = _compose_params(lsq.x)

    alpha_residuals = []
    for curve in curves_data:
        pred = np.asarray(
            predict_alpha_leroy2013(
                time_s=curve["time_s"],
                temperature_k=curve["temperature_k"],
                params=params_opt,
            ),
            dtype=float,
        )
        real = np.asarray(curve["alpha_real"], dtype=float)
        size = min(pred.size, real.size)
        if size <= 0:
            continue
        alpha_residuals.append(pred[:size] - real[:size])
    alpha_vec = np.concatenate(alpha_residuals) if alpha_residuals else np.asarray([], dtype=float)

    ident = evaluate_parameter_identifiability(lsq, free_names, bounds=(lb, ub))
    return {
        "success": bool(lsq.success),
        "message": str(lsq.message),
        "model_parameters": params_opt,
        "rmse_alpha": (
            float(np.sqrt(np.mean(np.square(alpha_vec))))
            if alpha_vec.size
            else None
        ),
        "cost": float(lsq.cost),
        "cost_alpha_sse": float(np.sum(np.square(alpha_vec))) if alpha_vec.size else None,
        "nfev": int(lsq.nfev),
        "free_parameters": list(free_names),
        "fixed_parameters": {k: float(v) for k, v in fixed.items() if k not in free_names},
        "identifiability": ident,
        "_lsq_result": lsq,
        "_bounds": {"lower": lb.tolist(), "upper": ub.tolist()},
    }


def _leroy_seed_statistics(seed_rows):
    out = {"alerts": []}
    if not seed_rows:
        return out

    for key in ("Av1", "Av2", "X", "Ar"):
        values = np.asarray(
            [
                _safe_float(row.get(key), default=np.nan)
                for row in seed_rows
                if _safe_float(row.get(key), default=None) is not None
            ],
            dtype=float,
        )
        values = values[np.isfinite(values)]
        if values.size == 0:
            continue

        median = float(np.median(values))
        std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        q1 = float(np.percentile(values, 25.0))
        q3 = float(np.percentile(values, 75.0))
        iqr = float(q3 - q1)
        cv = float(std / max(abs(median), 1e-12))

        out[f"{key}_median"] = median
        out[f"{key}_std"] = std
        out[f"{key}_iqr"] = iqr
        out[f"{key}_min"] = float(np.min(values))
        out[f"{key}_max"] = float(np.max(values))
        out[f"{key}_cv"] = cv

        if cv > 0.65 or iqr > abs(median) * 0.8:
            out["alerts"].append(f"high_seed_dispersion_{key.lower()}")

    return out


def fit_leroy2013_isothermal_seed(
    curves,
    *,
    ev_fixed=LEROY2013_EV_FIXED_DEFAULT,
    er_fixed=LEROY2013_ER_FIXED_DEFAULT,
):
    curves_data, total_points = _prepare_training_curves(curves)
    if total_points < 6:
        return {
            "success": False,
            "message": "Pontos insuficientes para gerar sementes isotermicas.",
            "ev_fixed": float(ev_fixed),
            "er_fixed": float(er_fixed),
            "seed_rows": [],
            "seed_statistics": {"alerts": ["insufficient_points"]},
        }

    seed_rows = []
    seed_success = []
    for curve in curves_data:
        init = _leroy_isothermal_initial_guess(curve, ev_fixed=ev_fixed, er_fixed=er_fixed)
        fit_row = _fit_leroy_parameters(
            [curve],
            free_param_names=("Av1", "Av2", "X", "Ar"),
            fixed_params={"Ev": ev_fixed, "Er": er_fixed},
            init_params=init,
            regularization_center=init,
            regularization_scale=LEROY2013_REGULARIZATION_SCALE,
            weighted=True,
            max_nfev=5000,
        )
        row = {
            "COD_ENSAIO": curve.get("COD_ENSAIO"),
            "temperature_K": float(np.median(np.asarray(curve["temperature_k"], dtype=float))),
            "success": bool(fit_row.get("success")),
            "rmse_alpha": fit_row.get("rmse_alpha"),
            "cost": fit_row.get("cost"),
            "message": fit_row.get("message"),
        }
        row.update(
            {
                "Av1": float(fit_row["model_parameters"]["Av1"]),
                "Av2": float(fit_row["model_parameters"]["Av2"]),
                "X": float(fit_row["model_parameters"]["X"]),
                "Ar": float(fit_row["model_parameters"]["Ar"]),
                "Ev": float(ev_fixed),
                "Er": float(er_fixed),
            }
        )
        if fit_row.get("success"):
            seed_success.append(row)
        seed_rows.append(row)

    stats = _leroy_seed_statistics(seed_success)
    return {
        "success": bool(seed_success),
        "message": (
            "Sementes isotermicas ajustadas."
            if seed_success
            else "Falha ao ajustar sementes isotermicas."
        ),
        "ev_fixed": float(ev_fixed),
        "er_fixed": float(er_fixed),
        "seed_rows": seed_rows,
        "seed_statistics": stats,
    }


def _leroy_stage2_ready(curves_data):
    temp_levels = []
    for curve in curves_data:
        temp_raw = curve.get("temperature_k")
        temp = np.asarray(temp_raw if temp_raw is not None else [], dtype=float)
        if temp.size:
            temp_levels.append(float(np.median(temp)))
    if not temp_levels:
        return False, {"distinct_temperatures": 0, "temperature_span_k": 0.0}

    uniq = sorted({round(float(v), 4) for v in temp_levels})
    span = float(max(uniq) - min(uniq)) if uniq else 0.0
    ready = len(uniq) >= LEROY2013_STAGE2_MIN_TEMPS and span >= LEROY2013_STAGE2_MIN_SPAN_K
    return ready, {"distinct_temperatures": len(uniq), "temperature_span_k": span}


def fit_leroy2013_global_stage1(
    curves,
    *,
    seed_statistics=None,
    ev_fixed=LEROY2013_EV_FIXED_DEFAULT,
    er_fixed=LEROY2013_ER_FIXED_DEFAULT,
):
    curves_data, total_points = _prepare_training_curves(curves)
    if total_points < 12:
        return {
            "success": False,
            "message": "Pontos insuficientes para ajuste global stage1.",
            "calibration_stage": "stage1",
        }

    stats = dict(seed_statistics or {})
    init_params = _leroy2013_sanitize_params(
        {
            "Av1": _safe_float(stats.get("Av1_median"), default=_leroy2013_param_defaults()["Av1"]),
            "Av2": _safe_float(stats.get("Av2_median"), default=_leroy2013_param_defaults()["Av2"]),
            "X": _safe_float(stats.get("X_median"), default=_leroy2013_param_defaults()["X"]),
            "Ar": _safe_float(stats.get("Ar_median"), default=_leroy2013_param_defaults()["Ar"]),
            "Ev": ev_fixed,
            "Er": er_fixed,
        }
    )

    fit = _fit_leroy_parameters(
        curves_data,
        free_param_names=("Av1", "Av2", "X", "Ar"),
        fixed_params={"Ev": ev_fixed, "Er": er_fixed},
        init_params=init_params,
        regularization_center=init_params,
        regularization_scale=LEROY2013_REGULARIZATION_SCALE,
        weighted=True,
        max_nfev=8000,
    )
    return {
        "success": bool(fit.get("success")),
        "message": fit.get("message"),
        "calibration_stage": "stage1",
        "model_parameters": dict(fit.get("model_parameters") or {}),
        "rmse_alpha": fit.get("rmse_alpha"),
        "cost": fit.get("cost"),
        "cost_alpha_sse": fit.get("cost_alpha_sse"),
        "nfev": fit.get("nfev"),
        "identifiability": dict(fit.get("identifiability") or {}),
        "fit_summary": {
            "free_parameters": list(fit.get("free_parameters") or []),
            "fixed_parameters": dict(fit.get("fixed_parameters") or {}),
        },
    }


def _leroy_identifiability_penalty(ident):
    status = str((ident or {}).get("status") or "warning").lower()
    near = len((ident or {}).get("parameters_near_bounds") or [])
    corr = len((ident or {}).get("high_correlation_pairs") or [])
    if status == "poor":
        return 1.50 + (0.10 * near) + (0.10 * corr)
    if status == "warning":
        return 0.30 + (0.05 * near) + (0.05 * corr)
    return 0.0


def fit_leroy2013_global_stage2(
    curves,
    *,
    stage1_result,
    allow_fit_er=True,
):
    curves_data, total_points = _prepare_training_curves(curves)
    if total_points < 12:
        return {
            "success": False,
            "message": "Pontos insuficientes para ajuste global stage2.",
            "calibration_stage_selected": "stage1",
            "selected_result": dict(stage1_result or {}),
        }

    ready, readiness_meta = _leroy_stage2_ready(curves_data)
    if not ready:
        return {
            "success": False,
            "message": "Dados insuficientes para liberar energias de ativacao no stage2.",
            "readiness": readiness_meta,
            "calibration_stage_selected": "stage1",
            "selected_result": dict(stage1_result or {}),
        }

    stage1_params = _leroy2013_sanitize_params((stage1_result or {}).get("model_parameters") or {})
    variants = {}

    fit_ev = _fit_leroy_parameters(
        curves_data,
        free_param_names=("Av1", "Av2", "X", "Ar", "Ev"),
        fixed_params={"Er": stage1_params["Er"]},
        init_params=stage1_params,
        regularization_center=stage1_params,
        regularization_scale=LEROY2013_REGULARIZATION_SCALE,
        weighted=True,
        max_nfev=12000,
    )
    variants["stage2_ev"] = {
        "success": bool(fit_ev.get("success")),
        "message": fit_ev.get("message"),
        "calibration_stage": "stage2_ev",
        "model_parameters": dict(fit_ev.get("model_parameters") or {}),
        "rmse_alpha": fit_ev.get("rmse_alpha"),
        "cost": fit_ev.get("cost"),
        "cost_alpha_sse": fit_ev.get("cost_alpha_sse"),
        "nfev": fit_ev.get("nfev"),
        "identifiability": dict(fit_ev.get("identifiability") or {}),
    }

    if allow_fit_er:
        fit_ev_er = _fit_leroy_parameters(
            curves_data,
            free_param_names=("Av1", "Av2", "X", "Ar", "Ev", "Er"),
            fixed_params={},
            init_params=stage1_params,
            regularization_center=stage1_params,
            regularization_scale=LEROY2013_REGULARIZATION_SCALE,
            weighted=True,
            max_nfev=14000,
        )
        variants["stage2_ev_er"] = {
            "success": bool(fit_ev_er.get("success")),
            "message": fit_ev_er.get("message"),
            "calibration_stage": "stage2_ev_er",
            "model_parameters": dict(fit_ev_er.get("model_parameters") or {}),
            "rmse_alpha": fit_ev_er.get("rmse_alpha"),
            "cost": fit_ev_er.get("cost"),
            "cost_alpha_sse": fit_ev_er.get("cost_alpha_sse"),
            "nfev": fit_ev_er.get("nfev"),
            "identifiability": dict(fit_ev_er.get("identifiability") or {}),
        }

    successful = [(name, data) for name, data in variants.items() if data.get("success")]
    if not successful:
        return {
            "success": False,
            "message": "Stage2 nao convergiu; usando stage1.",
            "readiness": readiness_meta,
            "variants": variants,
            "calibration_stage_selected": "stage1",
            "selected_result": dict(stage1_result or {}),
        }

    ranked = []
    for name, data in successful:
        base_cost = float(_safe_float(data.get("cost_alpha_sse"), default=data.get("cost")) or float("inf"))
        ident_penalty = _leroy_identifiability_penalty(data.get("identifiability"))
        score = base_cost * (1.0 + ident_penalty)
        ranked.append((score, name, data))
    ranked.sort(key=lambda row: row[0])
    _, selected_name, selected_data = ranked[0]

    if selected_name == "stage2_ev_er":
        ident = selected_data.get("identifiability") or {}
        if (
            str(ident.get("status") or "").lower() == "poor"
            or any(
                "Er" in {pair.get("param_a"), pair.get("param_b")}
                for pair in (ident.get("high_correlation_pairs") or [])
            )
        ):
            if variants.get("stage2_ev", {}).get("success"):
                selected_name = "stage2_ev"
                selected_data = variants["stage2_ev"]

    return {
        "success": True,
        "message": f"Stage2 concluido: {selected_name}.",
        "readiness": readiness_meta,
        "variants": variants,
        "calibration_stage_selected": selected_name,
        "selected_result": selected_data,
    }


def _fit_model_parameters_single_family(curves_data, family, reference_temperature_k, fit_method_norm):
    family = _predict_model_family(family)
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
    lb = np.asarray([FIT_BOUNDS_LOGK[0], FIT_BOUNDS_EA[0], FIT_BOUNDS_N[0]], dtype=float)
    ub = np.asarray([FIT_BOUNDS_LOGK[1], FIT_BOUNDS_EA[1], FIT_BOUNDS_N[1]], dtype=float)

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


def _candidate_alpha_metrics(curves_data, family, params, reference_temperature_k):
    residual_blocks = []
    t90_errors = []

    for curve in curves_data:
        pred = predict_alpha(
            time_s=curve["time_s"],
            temperature_k=curve["temperature_k"],
            params=params,
            model_family=family,
            reference_temperature_k=reference_temperature_k,
        )
        real = np.asarray(curve["alpha_real"], dtype=float)
        pred = np.clip(np.asarray(pred, dtype=float), 0.0, 1.0)
        size = min(real.size, pred.size, curve["time_s"].size)
        if size == 0:
            continue

        real = real[:size]
        pred = pred[:size]
        time = np.asarray(curve["time_s"], dtype=float)[:size]
        residual_blocks.append(pred - real)

        txx_rows = compute_txx_errors(time, real, pred, targets=(0.90,))
        for row in txx_rows:
            err = row.get("error_s")
            if err is not None and np.isfinite(float(err)):
                t90_errors.append(float(err))

    residual_vec = np.concatenate(residual_blocks) if residual_blocks else np.asarray([], dtype=float)
    rmse_alpha = float(np.sqrt(np.mean(np.square(residual_vec)))) if residual_vec.size else float("inf")

    if t90_errors:
        t90_arr = np.asarray(t90_errors, dtype=float)
        t90_mae = float(np.mean(np.abs(t90_arr)))
        t90_bias = float(np.mean(t90_arr))
    else:
        t90_mae = float("inf")
        t90_bias = None

    return {
        "rmse_alpha": rmse_alpha,
        "t90_mae_s": t90_mae,
        "t90_bias_s": t90_bias,
        "t90_count": int(len(t90_errors)),
    }


def _fit_stability_penalty(fit_result):
    params = dict((fit_result or {}).get("model_parameters") or {})
    ea = _safe_float(params.get("Ea"), default=None)
    n = _safe_float(params.get("n"), default=None)
    k = _safe_float(params.get("k_ref", params.get("k0")), default=None)

    if ea is None or n is None or k is None or k <= 0:
        return float("inf")

    penalty = 0.0
    ea_span = FIT_BOUNDS_EA[1] - FIT_BOUNDS_EA[0]
    n_span = FIT_BOUNDS_N[1] - FIT_BOUNDS_N[0]
    ea_margin = 0.02 * ea_span
    n_margin = 0.02 * n_span

    if ea <= (FIT_BOUNDS_EA[0] + ea_margin) or ea >= (FIT_BOUNDS_EA[1] - ea_margin):
        penalty += 1.0
    if n <= (FIT_BOUNDS_N[0] + n_margin) or n >= (FIT_BOUNDS_N[1] - n_margin):
        penalty += 1.0
    if not np.isfinite(math.log(max(k, 1e-300))):
        penalty += 2.0
    return penalty


def compare_model_families(
    curves,
    reference_temperature_k=None,
    fit_method=DEFAULT_FIT_METHOD,
    candidate_families=None,
):
    fit_method_norm = normalize_fit_method(fit_method)
    curves_data, total_points = _prepare_training_curves(curves)
    if total_points < 12:
        return {
            "success": False,
            "message": "Pontos insuficientes para comparacao de modelos.",
            "fit_method": fit_method_norm,
            "best_model_family": None,
            "reference_temperature_K": float(_safe_ref_temp(reference_temperature_k)),
            "candidates": {},
        }

    if reference_temperature_k is None:
        reference_temperature_k = _default_reference_temperature_k(curves_data)
    reference_temperature_k = _safe_ref_temp(reference_temperature_k)

    families = []
    for token in (candidate_families or AUTO_COMPARE_MODEL_FAMILIES):
        fam = normalize_model_family(token)
        if fam in (MODEL_FAMILY_AUTO,):
            continue
        if fam not in families:
            families.append(fam)

    candidates = {}
    for family in families:
        fit = _fit_model_parameters_single_family(
            curves_data=curves_data,
            family=family,
            reference_temperature_k=reference_temperature_k,
            fit_method_norm=fit_method_norm,
        )
        if fit.get("success"):
            metrics = _candidate_alpha_metrics(
                curves_data=curves_data,
                family=family,
                params=fit.get("model_parameters") or {},
                reference_temperature_k=reference_temperature_k,
            )
            stability_penalty = float(_fit_stability_penalty(fit))
        else:
            metrics = {
                "rmse_alpha": float("inf"),
                "t90_mae_s": float("inf"),
                "t90_bias_s": None,
                "t90_count": 0,
            }
            stability_penalty = float("inf")

        sort_key = (
            float(metrics["rmse_alpha"]),
            float(metrics["t90_mae_s"]),
            float(stability_penalty),
        )
        candidates[family] = {
            "family": family,
            "success": bool(fit.get("success")),
            "fit_result": fit,
            "metrics": metrics,
            "parameter_stability_penalty": stability_penalty,
            "selection_sort_key": [float(x) for x in sort_key],
        }

    successful = [c for c in candidates.values() if c["success"]]
    if successful:
        best = min(successful, key=lambda c: tuple(c["selection_sort_key"]))
        success = True
        message = "Comparacao concluida com sucesso."
    elif candidates:
        best = min(candidates.values(), key=lambda c: tuple(c["selection_sort_key"]))
        success = False
        message = "Nenhum modelo convergiu com sucesso."
    else:
        best = None
        success = False
        message = "Nenhuma familia candidata disponivel para comparacao."

    return {
        "success": success,
        "message": message,
        "fit_method": fit_method_norm,
        "reference_temperature_K": float(reference_temperature_k),
        "best_model_family": None if best is None else best["family"],
        "candidates": candidates,
        "selection_criteria": [
            "rmse_alpha (menor melhor)",
            "erro absoluto medio de T90 (menor melhor)",
            "penalidade de estabilidade de parametros (menor melhor)",
        ],
    }


def fit_model_parameters(
    curves,
    model_family=DEFAULT_MODEL_FAMILY,
    reference_temperature_k=None,
    fit_method=DEFAULT_FIT_METHOD,
):
    family = normalize_model_family(model_family)
    fit_method_norm = normalize_fit_method(fit_method)

    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        curves_data, total_points = _prepare_training_curves(curves)
        if total_points < 12:
            return {
                "success": False,
                "message": "Pontos insuficientes para ajuste Leroy2013. Selecione mais ensaios.",
                "fit_method": fit_method_norm,
                "model_family": family,
                "model_version": DEFAULT_MODEL_VERSION,
                "reference_temperature_K": float(_safe_ref_temp(reference_temperature_k)),
                "calibration_strategy": "hierarchical_v1",
            }

        if reference_temperature_k is None:
            reference_temperature_k = _default_reference_temperature_k(curves_data)
        reference_temperature_k = _safe_ref_temp(reference_temperature_k)

        seed_result = fit_leroy2013_isothermal_seed(
            curves,
            ev_fixed=LEROY2013_EV_FIXED_DEFAULT,
            er_fixed=LEROY2013_ER_FIXED_DEFAULT,
        )
        seed_stats = dict(seed_result.get("seed_statistics") or {})

        stage1 = fit_leroy2013_global_stage1(
            curves,
            seed_statistics=seed_stats,
            ev_fixed=LEROY2013_EV_FIXED_DEFAULT,
            er_fixed=LEROY2013_ER_FIXED_DEFAULT,
        )
        stage2 = fit_leroy2013_global_stage2(
            curves,
            stage1_result=stage1,
            allow_fit_er=True,
        )

        selected_stage = "stage1"
        selected_payload = stage1
        if bool(stage2.get("success")) and stage2.get("selected_result"):
            selected_stage = str(stage2.get("calibration_stage_selected") or "stage1")
            selected_payload = dict(stage2.get("selected_result") or stage1)
        elif str(stage2.get("calibration_stage_selected") or "").startswith("stage2"):
            selected_stage = str(stage2.get("calibration_stage_selected"))

        params_opt = _round_leroy_params(
            _leroy2013_sanitize_params((selected_payload or {}).get("model_parameters") or {})
        )
        alerts = list(seed_stats.get("alerts") or [])
        ident = dict((selected_payload or {}).get("identifiability") or {})
        if str(ident.get("status") or "").lower() == "poor":
            alerts.append("identifiability_poor")
        elif str(ident.get("status") or "").lower() == "warning":
            alerts.append("identifiability_warning")

        return {
            "success": bool((selected_payload or {}).get("success")),
            "message": (
                (selected_payload or {}).get("message")
                or stage2.get("message")
                or stage1.get("message")
                or "Ajuste Leroy2013 executado."
            ),
            "fit_method": fit_method_norm,
            "model_family": family,
            "model_version": DEFAULT_MODEL_VERSION,
            "reference_temperature_K": float(reference_temperature_k),
            "model_parameters": params_opt,
            "Av1": float(params_opt["Av1"]),
            "Av2": float(params_opt["Av2"]),
            "Ev": float(params_opt["Ev"]),
            "X": float(params_opt["X"]),
            "Ar": float(params_opt["Ar"]),
            "Er": float(params_opt["Er"]),
            "rmse_alpha": _safe_float((selected_payload or {}).get("rmse_alpha"), default=None),
            "cost": _safe_float((selected_payload or {}).get("cost"), default=None),
            "nfev": _safe_float((selected_payload or {}).get("nfev"), default=None),
            "calibration_strategy": "hierarchical_v1",
            "calibration_stage_selected": selected_stage,
            "seed_statistics": seed_stats,
            "seed_rows": list(seed_result.get("seed_rows") or []),
            "identifiability": ident,
            "calibration_stages": {
                "isothermal_seed": {
                    "success": bool(seed_result.get("success")),
                    "message": seed_result.get("message"),
                    "ev_fixed": seed_result.get("ev_fixed"),
                    "er_fixed": seed_result.get("er_fixed"),
                    "seed_statistics": seed_stats,
                },
                "stage1": {
                    "success": bool(stage1.get("success")),
                    "message": stage1.get("message"),
                    "rmse_alpha": stage1.get("rmse_alpha"),
                    "identifiability": stage1.get("identifiability"),
                },
                "stage2": {
                    "success": bool(stage2.get("success")),
                    "message": stage2.get("message"),
                    "calibration_stage_selected": stage2.get("calibration_stage_selected"),
                    "readiness": stage2.get("readiness"),
                    "variants": stage2.get("variants"),
                },
            },
            "alerts": alerts,
        }

    if family == MODEL_FAMILY_AUTO:
        comparison = compare_model_families(
            curves=curves,
            reference_temperature_k=reference_temperature_k,
            fit_method=fit_method_norm,
            candidate_families=AUTO_COMPARE_MODEL_FAMILIES,
        )
        best_family = comparison.get("best_model_family")
        if not best_family:
            return {
                "success": False,
                "message": comparison.get("message") or "Falha ao selecionar modelo automaticamente.",
                "fit_method": fit_method_norm,
                "model_family": MODEL_FAMILY_AUTO,
                "model_version": DEFAULT_MODEL_VERSION,
                "reference_temperature_K": float(comparison.get("reference_temperature_K", _safe_ref_temp(reference_temperature_k))),
                "auto_model_selection": comparison,
            }

        best_candidate = comparison.get("candidates", {}).get(best_family) or {}
        best_fit = dict(best_candidate.get("fit_result") or {})
        best_fit["requested_model_family"] = MODEL_FAMILY_AUTO
        best_fit["auto_selected_model_family"] = best_family
        best_fit["auto_model_selection"] = comparison
        best_fit["model_family"] = best_family
        return best_fit

    curves_data, total_points = _prepare_training_curves(curves)
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
    return _fit_model_parameters_single_family(
        curves_data=curves_data,
        family=family,
        reference_temperature_k=reference_temperature_k,
        fit_method_norm=fit_method_norm,
    )


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


def _resolve_payload_family(payload, model_family=None):
    family = normalize_model_family(model_family or (payload or {}).get("model_family"))
    if family != MODEL_FAMILY_AUTO:
        return family

    selected = normalize_model_family(
        (payload or {}).get("auto_selected_model_family")
        or (payload or {}).get("model_family_selected")
    )
    if selected == MODEL_FAMILY_AUTO:
        selected = DEFAULT_MODEL_FAMILY
    return selected


def extract_model_parameters(payload, model_family=None):
    family = _resolve_payload_family(payload, model_family=model_family)
    raw = dict((payload or {}).get("model_parameters") or {})

    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        leroy_raw = {}
        for key in ("Av1", "Av2", "Ev", "X", "Ar", "Er"):
            val = _safe_float(raw.get(key), default=None)
            if val is None:
                val = _safe_float((payload or {}).get(key), default=None)
            if val is not None:
                leroy_raw[key] = float(val)
        return _round_leroy_params(_leroy2013_sanitize_params(leroy_raw))

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


def update_model_parameters(payload, *, k_value=None, ea=None, n=None, parameter_updates=None):
    family = _resolve_payload_family(payload)
    params = extract_model_parameters(payload, model_family=family)
    updates = dict(parameter_updates or {})

    out = dict(payload or {})

    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        if not updates:
            if k_value is not None:
                updates["Av1"] = k_value
            if ea is not None:
                updates["Ev"] = ea
            if n is not None:
                updates["X"] = n

        merged = dict(params)
        for key in ("Av1", "Av2", "Ev", "X", "Ar", "Er"):
            if key in updates:
                numeric = _safe_float(updates.get(key), default=None)
                if numeric is not None:
                    merged[key] = float(numeric)
        merged = _round_leroy_params(_leroy2013_sanitize_params(merged))

        out["model_parameters"] = merged
        for key, value in merged.items():
            out[key] = float(value)
        return out

    if k_value is None:
        k_value = updates.get("k0", updates.get("k_ref", updates.get("k")))
    if ea is None:
        ea = updates.get("Ea", updates.get("ea"))
    if n is None:
        n = updates.get("n")

    if k_value is None:
        k_value = params.get("k0", params.get("k_ref"))
    if ea is None:
        ea = params.get("Ea")
    if n is None:
        n = params.get("n")

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
    "MODEL_FAMILY_AUTO",
    "MODEL_FAMILY_EDO_ORDER_N_V1",
    "MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1",
    "MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1",
    "MODEL_FAMILY_LEROY2013_CONTINUOUS_V1",
    "AUTO_COMPARE_MODEL_FAMILIES",
    "SUPPORTED_MODEL_FAMILIES",
    "DEFAULT_MODEL_FAMILY",
    "DEFAULT_MODEL_VERSION",
    "DEFAULT_FIT_METHOD",
    "DEFAULT_REFERENCE_TEMPERATURE_K",
    "FIT_BOUNDS_LOGK",
    "FIT_BOUNDS_EA",
    "FIT_BOUNDS_N",
    "LEROY2013_EV_FIXED_DEFAULT",
    "LEROY2013_ER_FIXED_DEFAULT",
    "LEROY2013_FIT_BOUNDS",
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
    "integrate_leroy2013_states",
    "predict_alpha_leroy2013",
    "predict_alpha_isothermal",
    "predict_alpha_nonisothermal",
    "predict_alpha",
    "compare_model_families",
    "fit_model_parameters",
    "fit_leroy2013_isothermal_seed",
    "fit_leroy2013_global_stage1",
    "fit_leroy2013_global_stage2",
    "evaluate_parameter_identifiability",
    "estimate_cure_completion_time",
    "compute_txx_errors",
    "extract_model_parameters",
    "update_model_parameters",
]
