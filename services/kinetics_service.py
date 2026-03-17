import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from connection import connect_to_database
from services.kinetic_model_service import (
    ALPHA_METRIC_TARGETS_DEFAULT,
    CURE_COMPLETION_RULE_ALPHA_099,
    DEFAULT_FIT_METHOD,
    DEFAULT_MODEL_FAMILY,
    DEFAULT_MODEL_VERSION,
    DEFAULT_REFERENCE_TEMPERATURE_K,
    alpha_from_torque,
    compute_equivalent_time,
    compute_txx_errors,
    crossing_or_nearest_time,
    estimate_cure_completion_time,
    extract_model_parameters,
    first_crossing_time,
    fit_model_parameters,
    normalize_cure_completion_rule,
    normalize_fit_method,
    normalize_model_family,
    predict_alpha,
    predict_alpha_leroy2013,
    torque_from_alpha,
    update_model_parameters,
    MODEL_FAMILY_AUTO,
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
)
from services.scorch_extraction import (
    DEFAULT_TIME_FLOOR_S,
    extract_scorch_points,
    fit_arrhenius_from_scorch,
)

OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ALPHA_COMPARE_TARGETS_DEFAULT = [0.10, 0.20, 0.50, 0.90]

MODEL_EXPRESSIONS = {
    MODEL_FAMILY_AUTO: "auto_compare(edo_order_n_v1, pinheiro_sigmoidal_teq_v1)",
    MODEL_FAMILY_EDO_ORDER_N_V1: "dalpha/dt = k(T) * (1 - alpha)^n",
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1: "alpha = (k(T) * t^n) / (1 + k(T) * t^n)",
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1: "alpha = (k_ref * t_eq(t)^n) / (1 + k_ref * t_eq(t)^n)",
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1: (
        "d(alpha_v)/dt=(Av1+Av2*alpha_v)*exp(-Ev/(R*T))*(1-alpha_v)^2; "
        "d(alpha_unstable)/dt=(1-X)*d(alpha_v)/dt-Ar*exp(-Er/(R*T))*alpha_unstable; "
        "alpha_total=X*alpha_v+alpha_unstable"
    ),
}

INDUCTION_MODEL_VERSION = 2
INDUCTION_MODEL_EXPRESSION = "ln(t_scorch) = intercept + slope*(1/T); t_scorch(T) = (1/A_ind) * exp(E_ind / (R*T))"
INDUCTION_SOURCE = "scorch_torque_min_after_time_floor"
INDUCTION_TIME_FLOOR_S = DEFAULT_TIME_FLOOR_S
INDUCTION_FIT_MIN_POINTS = 3
INDUCTION_FIT_MIN_TEMP_SPAN_K = 8.0
INDUCTION_FIT_MIN_R2 = 0.60


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def _safe_reference_temperature_k(payload):
    ref = _safe_float((payload or {}).get("reference_temperature_K"), default=None)
    if ref is not None:
        return float(max(ref, 1.0))

    ensaios = (payload or {}).get("ensaios") or []
    temps = []
    for ensaio in ensaios:
        tk = _safe_float((ensaio or {}).get("T_K"), default=None)
        if tk is not None:
            temps.append(tk)
    if temps:
        return float(max(np.median(np.asarray(temps, dtype=float)), 1.0))

    curves = (payload or {}).get("curves") or []
    for curve in curves:
        tk = _safe_float((curve or {}).get("T_K"), default=None)
        if tk is not None:
            temps.append(tk)
    if temps:
        return float(max(np.median(np.asarray(temps, dtype=float)), 1.0))

    return float(DEFAULT_REFERENCE_TEMPERATURE_K)


def _confidence_from_fit(arrhenius_result):
    if not arrhenius_result.get("success"):
        return "low"
    quality = str(arrhenius_result.get("fit_quality") or "").lower()
    if quality in {"high", "medium", "low"}:
        return quality
    return "medium"


def _build_induction_payload(curves):
    scorch_points = extract_scorch_points(curves, time_floor=INDUCTION_TIME_FLOOR_S)
    arrhenius_result = fit_arrhenius_from_scorch(
        scorch_points,
        min_points=INDUCTION_FIT_MIN_POINTS,
        min_temp_span_k=INDUCTION_FIT_MIN_TEMP_SPAN_K,
        min_r2=INDUCTION_FIT_MIN_R2,
    )
    confidence = _confidence_from_fit(arrhenius_result)

    induction_payload = {
        "enabled": bool(arrhenius_result.get("success")),
        "source": INDUCTION_SOURCE,
        "message": arrhenius_result.get("message"),
        "time_floor_s": float(INDUCTION_TIME_FLOOR_S),
        "confidence": confidence,
        "fit_quality": arrhenius_result.get("fit_quality"),
        "model_regime": arrhenius_result.get("induction_model_regime") or "single_arrhenius",
        "samples": arrhenius_result.get("points_table") or [],
        "points_used": arrhenius_result.get("points_used") or [],
        "points_discarded": arrhenius_result.get("points_discarded") or [],
        "temperature_range_k": arrhenius_result.get("temperature_range_k"),
        "temperature_range_c": arrhenius_result.get("temperature_range_c"),
        "temperature_validity_range": arrhenius_result.get("temperature_validity_range"),
        "criteria": arrhenius_result.get("criteria") or {},
        "regression": {
            "intercept": arrhenius_result.get("intercept"),
            "slope": arrhenius_result.get("slope"),
            "A_ind": arrhenius_result.get("A_ind"),
            "E_ind": arrhenius_result.get("E_ind"),
            "R2": arrhenius_result.get("R2"),
            "points_used": arrhenius_result.get("valid_points"),
        },
    }
    return induction_payload, arrhenius_result


def _curve_temperature_profile_k(curve, t_rel):
    temp_profile = curve.get("temp_k_profile")
    if temp_profile is None:
        t_k = _safe_float(curve.get("T_K"), default=_safe_float(curve.get("T_C"), default=25.0) + 273.15)
        return np.full((len(t_rel),), fill_value=float(max(t_k, 1.0)), dtype=float)

    temp = np.asarray(temp_profile, dtype=float)
    if temp.size == 1:
        return np.full((len(t_rel),), fill_value=float(max(temp[0], 1.0)), dtype=float)

    out = np.full((len(t_rel),), fill_value=float(max(_safe_float(curve.get("T_K"), default=298.15), 1.0)), dtype=float)
    lim = min(len(out), len(temp))
    out[:lim] = temp[:lim]
    return np.maximum(out, 1.0)


def _estimate_scorch_time_approx(t_rel, torque, time_floor_s=10.0):
    time = np.asarray(t_rel, dtype=float)
    tq = np.asarray(torque, dtype=float)
    size = min(time.size, tq.size)
    if size < 3:
        return None

    time = np.maximum.accumulate(np.maximum(time[:size], 0.0))
    tq = tq[:size]
    start_idx = int(np.searchsorted(time, float(max(time_floor_s, 0.0)), side="left"))
    start_idx = int(np.clip(start_idx, 0, size - 1))
    sub = tq[start_idx:]
    if sub.size == 0:
        return None
    idx_local = int(np.argmin(sub))
    idx = start_idx + idx_local
    return float(time[idx])


def _extract_curve_markers(curve):
    t_rel_raw = curve.get("t_rel")
    torque_raw = curve.get("torque")
    alpha_raw = curve.get("alpha")
    t_rel = np.asarray(t_rel_raw if t_rel_raw is not None else [], dtype=float)
    torque = np.asarray(torque_raw if torque_raw is not None else [], dtype=float)
    alpha = np.asarray(alpha_raw if alpha_raw is not None else [], dtype=float)
    size = min(t_rel.size, torque.size, alpha.size)
    if size < 3:
        return {
            "T10_s": None,
            "T30_s": None,
            "T50_s": None,
            "T90_s": None,
            "T95_s": None,
            "T99_s": None,
            "scorch_time_s": None,
            "torque_max_observed": None,
            "regions": {},
            "reversion_detected": False,
        }

    t_rel = np.maximum.accumulate(np.maximum(t_rel[:size], 0.0))
    torque = torque[:size]
    alpha = np.clip(alpha[:size], 0.0, 1.0)

    markers = {
        "T10_s": first_crossing_time(t_rel, alpha, 0.10),
        "T30_s": first_crossing_time(t_rel, alpha, 0.30),
        "T50_s": first_crossing_time(t_rel, alpha, 0.50),
        "T90_s": first_crossing_time(t_rel, alpha, 0.90),
        "T95_s": first_crossing_time(t_rel, alpha, 0.95),
        "T99_s": first_crossing_time(t_rel, alpha, 0.99),
        "scorch_time_s": _estimate_scorch_time_approx(t_rel, torque, time_floor_s=8.0),
        "torque_max_observed": float(np.max(torque)),
    }

    torque_peak_idx = int(np.argmax(torque))
    tail_count = int(np.clip(round(size * 0.15), 5, max(size, 5)))
    tail_mean = float(np.mean(torque[-tail_count:]))
    drop_from_peak = float(torque[torque_peak_idx] - tail_mean)

    induction_end_s = markers["T10_s"]
    growth_start_s = markers["T10_s"]
    growth_end_s = markers["T90_s"]
    reversion_start_s = float(t_rel[torque_peak_idx]) if drop_from_peak > 0.03 else None

    markers["regions"] = {
        "induction": {"start_s": float(t_rel[0]), "end_s": induction_end_s},
        "growth": {"start_s": growth_start_s, "end_s": growth_end_s},
        "possible_reversion": {"start_s": reversion_start_s, "end_s": float(t_rel[-1]) if reversion_start_s is not None else None},
    }
    markers["reversion_detected"] = bool(reversion_start_s is not None)
    markers["reversion_drop_torque"] = drop_from_peak if reversion_start_s is not None else 0.0
    return markers


def extract_experimental_markers(curves):
    out = {}
    for curve in curves or []:
        code = curve.get("COD_ENSAIO")
        out[code] = _extract_curve_markers(curve)
    return out


def _attach_curve_markers(curves):
    for curve in curves or []:
        curve["markers"] = _extract_curve_markers(curve)


def _rebuild_curve_predictions(payload):
    family = normalize_model_family(payload.get("model_family"))
    params = extract_model_parameters(payload, model_family=family)
    ref_k = float(_safe_reference_temperature_k(payload))
    legacy_arrhenius_mode = bool(payload.get("legacy_arrhenius_mode"))

    curves = payload.get("curves") or []
    for curve in curves:
        t_rel = np.asarray(curve.get("t_rel") or [], dtype=float)
        if t_rel.size == 0:
            curve["alpha_model"] = []
            curve["torque_model"] = []
            curve.pop("t_eq", None)
            continue

        temp_k_profile = _curve_temperature_profile_k(curve, t_rel)
        if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
            states = predict_alpha_leroy2013(
                time_s=t_rel,
                temperature_k=temp_k_profile,
                params=params,
                return_states=True,
            )
            alpha_total = states.get("alpha_total")
            alpha_v = states.get("alpha_v")
            alpha_stable = states.get("alpha_stable")
            alpha_unstable = states.get("alpha_unstable")

            alpha_model = np.clip(np.asarray(alpha_total if alpha_total is not None else [], dtype=float), 0.0, 1.0)
            curve["alpha_v_model"] = np.clip(np.asarray(alpha_v if alpha_v is not None else [], dtype=float), 0.0, 1.0).tolist()
            curve["alpha_stable_model"] = np.clip(
                np.asarray(alpha_stable if alpha_stable is not None else [], dtype=float),
                0.0,
                1.0,
            ).tolist()
            curve["alpha_unstable_model"] = np.clip(
                np.asarray(alpha_unstable if alpha_unstable is not None else [], dtype=float),
                0.0,
                1.0,
            ).tolist()
        elif legacy_arrhenius_mode and family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1:
            k0 = float(max(_safe_float(payload.get("k0"), default=params.get("k_ref", 1e-8)), 1e-300))
            ea = float(max(params.get("Ea", 0.0), 0.0))
            n = float(np.clip(params.get("n", 1.2), 0.2, 12.0))
            temp_safe = np.maximum(temp_k_profile, 1.0)
            k_t = np.maximum(k0 * np.exp(np.clip(-ea / (8.314462618 * temp_safe), -700.0, 700.0)), 0.0)
            ln_x = np.log(np.maximum(k_t, 1e-300)) + (n * np.log(np.maximum(t_rel, 1e-300)))
            drive = np.exp(np.clip(ln_x, -700.0, 700.0))
            drive = np.where((t_rel <= 0.0) | (k_t <= 0.0), 0.0, drive)
            alpha_model = drive / (1.0 + drive)
        else:
            alpha_model = predict_alpha(
                time_s=t_rel,
                temperature_k=temp_k_profile,
                params=params,
                model_family=family,
                reference_temperature_k=ref_k,
            )
        alpha_model = np.clip(np.asarray(alpha_model, dtype=float), 0.0, 1.0)
        curve["alpha_model"] = alpha_model.tolist()

        ml = _safe_float(curve.get("ML"), default=None)
        mh = _safe_float(curve.get("MH"), default=None)
        if ml is not None and mh is not None:
            curve["torque_model"] = torque_from_alpha(alpha_model, ml, mh).tolist()
        else:
            curve.pop("torque_model", None)

        if family == MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1:
            t_eq = compute_equivalent_time(
                time_s=t_rel,
                temperature_k=temp_k_profile,
                ea=params.get("Ea"),
                reference_temperature_k=ref_k,
            )
            curve["t_eq"] = np.asarray(t_eq, dtype=float).tolist()
        else:
            curve.pop("t_eq", None)

        if family != MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
            curve.pop("alpha_v_model", None)
            curve.pop("alpha_stable_model", None)
            curve.pop("alpha_unstable_model", None)


def _build_txx_summary(curve_metrics):
    by_target = {}
    for curve in curve_metrics:
        for row in curve.get("txx_errors") or []:
            target = row.get("alpha_target")
            err = row.get("error_s")
            if target is None or err is None:
                continue
            key = float(target)
            by_target.setdefault(key, []).append(float(err))

    out = []
    for target, values in sorted(by_target.items()):
        arr = np.asarray(values, dtype=float)
        out.append(
            {
                "alpha_target": float(target),
                "count": int(arr.size),
                "mae_s": float(np.mean(np.abs(arr))) if arr.size else None,
                "rmse_s": float(np.sqrt(np.mean(np.square(arr)))) if arr.size else None,
                "bias_s": float(np.mean(arr)) if arr.size else None,
            }
        )
    return out


def _compute_payload_metrics(payload):
    curves = payload.get("curves") or []
    cure_rule = normalize_cure_completion_rule(payload.get("cure_completion_rule"))
    family = normalize_model_family(payload.get("model_family"))
    params = extract_model_parameters(payload, model_family=family)

    alpha_residuals = []
    torque_residuals = []
    curve_metrics = []
    scorch_errors = []
    peak_errors = []
    plateau_errors = []
    reversion_errors = []

    for curve in curves:
        t_rel = np.asarray(curve.get("t_rel") or [], dtype=float)
        alpha_real = np.asarray(curve.get("alpha") or [], dtype=float)
        alpha_model = np.asarray(curve.get("alpha_model") or [], dtype=float)
        size = min(t_rel.size, alpha_real.size, alpha_model.size)
        if size == 0:
            continue

        t_rel = t_rel[:size]
        alpha_real = np.clip(alpha_real[:size], 0.0, 1.0)
        alpha_model = np.clip(alpha_model[:size], 0.0, 1.0)

        alpha_res = alpha_model - alpha_real
        alpha_residuals.append(alpha_res)

        torque_real = np.asarray(curve.get("torque") or [], dtype=float)
        torque_model = np.asarray(curve.get("torque_model") or [], dtype=float)
        torque_size = min(torque_real.size, torque_model.size)
        if torque_size > 0:
            torque_residuals.append(torque_model[:torque_size] - torque_real[:torque_size])

        txx_rows = compute_txx_errors(t_rel, alpha_real, alpha_model, targets=ALPHA_METRIC_TARGETS_DEFAULT)
        completion_real_s = estimate_cure_completion_time(
            t_rel,
            alpha_real,
            cure_completion_rule=cure_rule,
            n_value=params.get("n"),
        )
        completion_model_s = estimate_cure_completion_time(
            t_rel,
            alpha_model,
            cure_completion_rule=cure_rule,
            n_value=params.get("n", params.get("X")),
        )

        markers = dict(curve.get("markers") or {})
        if not markers:
            markers = _extract_curve_markers(curve)

        scorch_real_s = _safe_float(markers.get("scorch_time_s"), default=None)
        scorch_model_s = None
        if torque_size > 0:
            scorch_model_s = _estimate_scorch_time_approx(
                t_rel[:torque_size],
                torque_model[:torque_size],
                time_floor_s=8.0,
            )
        if scorch_model_s is None:
            scorch_model_s = first_crossing_time(t_rel, alpha_model, 0.02)
        scorch_error_s = (
            float(scorch_model_s - scorch_real_s)
            if scorch_real_s is not None and scorch_model_s is not None
            else None
        )
        if scorch_error_s is not None and np.isfinite(float(scorch_error_s)):
            scorch_errors.append(float(scorch_error_s))

        peak_error = None
        plateau_error = None
        reversion_error = None
        if torque_size > 0:
            torque_real_i = torque_real[:torque_size]
            torque_model_i = torque_model[:torque_size]
            peak_error = float(np.max(torque_model_i) - np.max(torque_real_i))

            tail_count = int(np.clip(round(torque_size * 0.15), 5, max(torque_size, 5)))
            plateau_real = float(np.mean(torque_real_i[-tail_count:]))
            plateau_model = float(np.mean(torque_model_i[-tail_count:]))
            plateau_error = float(plateau_model - plateau_real)

            reversion_real = float(np.max(torque_real_i) - plateau_real)
            reversion_model = float(np.max(torque_model_i) - plateau_model)
            reversion_error = float(reversion_model - reversion_real)

            peak_errors.append(peak_error)
            plateau_errors.append(plateau_error)
            reversion_errors.append(reversion_error)

        curve_metrics.append(
            {
                "COD_ENSAIO": curve.get("COD_ENSAIO"),
                "T_C": _safe_float(curve.get("T_C"), default=None),
                "RMSE_alpha": float(np.sqrt(np.mean(np.square(alpha_res)))) if alpha_res.size else None,
                "RMSE_torque": (
                    float(np.sqrt(np.mean(np.square(torque_model[:torque_size] - torque_real[:torque_size]))))
                    if torque_size > 0
                    else None
                ),
                "txx_errors": txx_rows,
                "cure_completion_real_s": float(completion_real_s) if completion_real_s is not None else None,
                "cure_completion_model_s": float(completion_model_s) if completion_model_s is not None else None,
                "cure_completion_error_s": (
                    float(completion_model_s - completion_real_s)
                    if completion_real_s is not None and completion_model_s is not None
                    else None
                ),
                "scorch_real_s": float(scorch_real_s) if scorch_real_s is not None else None,
                "scorch_model_s": float(scorch_model_s) if scorch_model_s is not None else None,
                "scorch_error_s": float(scorch_error_s) if scorch_error_s is not None else None,
                "peak_error_torque": peak_error,
                "plateau_error_torque": plateau_error,
                "reversion_error_torque": reversion_error,
                "markers": markers,
            }
        )

    alpha_vec = np.concatenate(alpha_residuals) if alpha_residuals else np.asarray([], dtype=float)
    torque_vec = np.concatenate(torque_residuals) if torque_residuals else np.asarray([], dtype=float)

    rmse_alpha = float(np.sqrt(np.mean(np.square(alpha_vec)))) if alpha_vec.size else None
    rmse_torque = float(np.sqrt(np.mean(np.square(torque_vec)))) if torque_vec.size else None

    payload["rmse_alpha"] = rmse_alpha
    payload["rmse_torque"] = rmse_torque
    payload["metrics"] = {
        "RMSE_alpha": rmse_alpha,
        "RMSE_torque": rmse_torque,
        "curve_metrics": curve_metrics,
        "txx_summary": _build_txx_summary(curve_metrics),
        "cure_completion_rule": cure_rule,
        "scorch_mae_s": (
            float(np.mean(np.abs(np.asarray(scorch_errors, dtype=float))))
            if scorch_errors
            else None
        ),
        "scorch_rmse_s": (
            float(np.sqrt(np.mean(np.square(np.asarray(scorch_errors, dtype=float)))))
            if scorch_errors
            else None
        ),
        "peak_error_mae": (
            float(np.mean(np.abs(np.asarray(peak_errors, dtype=float))))
            if peak_errors
            else None
        ),
        "plateau_error_mae": (
            float(np.mean(np.abs(np.asarray(plateau_errors, dtype=float))))
            if plateau_errors
            else None
        ),
        "reversion_error_mae": (
            float(np.mean(np.abs(np.asarray(reversion_errors, dtype=float))))
            if reversion_errors
            else None
        ),
    }


def _normalize_fit_payload_model(payload):
    changed = False

    family = str(payload.get("model_family") or "").strip().lower()
    requested_family = str(payload.get("requested_model_family") or "").strip().lower()
    legacy_arrhenius_mode = bool(payload.get("legacy_arrhenius_mode"))
    if family:
        family = normalize_model_family(family)
    else:
        raw_params = payload.get("model_parameters") if isinstance(payload.get("model_parameters"), dict) else {}
        has_k_ref_hint = (
            _safe_float(payload.get("k_ref"), default=None) is not None
            or _safe_float(raw_params.get("k_ref"), default=None) is not None
            or _safe_float(raw_params.get("k"), default=None) is not None
        )
        if legacy_arrhenius_mode or has_k_ref_hint:
            family = MODEL_FAMILY_PINHEIRO_SIGMOIDAL_V1
            legacy_arrhenius_mode = True
        else:
            family = DEFAULT_MODEL_FAMILY
            legacy_arrhenius_mode = False
        changed = True

    model_version = str(payload.get("model_version") or "").strip().lower()
    if not model_version:
        model_version = DEFAULT_MODEL_VERSION
        changed = True

    fit_method = normalize_fit_method(payload.get("fit_method"))
    if payload.get("fit_method") != fit_method:
        changed = True

    cure_rule = normalize_cure_completion_rule(payload.get("cure_completion_rule"))
    if payload.get("cure_completion_rule") != cure_rule:
        changed = True

    ref_k = _safe_reference_temperature_k(payload)
    if _safe_float(payload.get("reference_temperature_K"), default=None) is None:
        changed = True

    params = extract_model_parameters(payload, model_family=family)

    payload["model_family"] = family
    payload["requested_model_family"] = normalize_model_family(requested_family or family)
    payload["model_version"] = model_version
    payload["fit_method"] = fit_method
    payload["reference_temperature_K"] = float(ref_k)
    payload["cure_completion_rule"] = cure_rule
    payload["model_parameters"] = params
    payload["legacy_arrhenius_mode"] = bool(legacy_arrhenius_mode)

    payload["kinetic_model_version"] = 3
    payload["kinetic_model_expression"] = MODEL_EXPRESSIONS.get(family)

    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        for key in ("Av1", "Av2", "Ev", "X", "Ar", "Er"):
            payload[key] = float(params.get(key, 0.0))
        payload.pop("k0", None)
        payload.pop("k_ref", None)
        payload.pop("Ea", None)
        payload.pop("n", None)
        if not payload.get("calibration_strategy"):
            payload["calibration_strategy"] = "hierarchical_v1"
        if not payload.get("calibration_stage_selected"):
            payload["calibration_stage_selected"] = "stage1"
    else:
        payload["Ea"] = float(params.get("Ea", 0.0))
        payload["n"] = float(params.get("n", 0.0))

    if family == MODEL_FAMILY_EDO_ORDER_N_V1:
        payload["k0"] = float(params.get("k0", params.get("k_ref", 0.0)))
        if "k_ref" in payload:
            payload.pop("k_ref", None)
    elif family != MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        payload["k_ref"] = float(params.get("k_ref", params.get("k", 0.0)))
        # Compatibility alias for existing consumers.
        payload["k0"] = float(payload["k_ref"])

    return payload, changed


def list_ensaios_for_fit(date_start=None, date_end=None, text_query=None, limit=300):
    conn = connect_to_database()
    cur = conn.cursor()
    lote_batch_expr = "COALESCE(CAST(e.NUMERO_LOTE AS varchar(100)), CAST(e.BATCH AS varchar(100)), '')"

    sql = f"""
    SELECT TOP (?)
        e.COD_ENSAIO,
        e.DATA,
        e.TEMP_PLATO_INF,
        e.AMOSTRA,
        e.CODIGO,
        {lote_batch_expr} AS LOTE_BATCH,
        e.TMINTEMPO,
        e.TMINTORQUE,
        e.TMAXTORQUE
    FROM dbo.ENSAIO e
    WHERE 1=1
    """
    params = [int(limit)]

    if date_start:
        sql += " AND CAST(e.DATA AS date) >= ?"
        params.append(date_start)
    if date_end:
        sql += " AND CAST(e.DATA AS date) <= ?"
        params.append(date_end)
    if text_query:
        sql += f" AND (CAST(e.COD_ENSAIO AS varchar(30)) LIKE ? OR e.AMOSTRA LIKE ? OR e.CODIGO LIKE ? OR {lote_batch_expr} LIKE ?)"
        like = f"%{text_query}%"
        params.extend([like, like, like, like])

    sql += " ORDER BY e.DATA DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_curve_points(cod_ensaios):
    if not cod_ensaios:
        return []

    conn = connect_to_database()
    cur = conn.cursor()
    marks = ",".join("?" for _ in cod_ensaios)
    sql = f"""
    SELECT
        e.COD_ENSAIO,
        e.TEMP_PLATO_INF,
        e.TMINTEMPO,
        e.TMINTORQUE,
        e.TMAXTORQUE,
        v.TEMPO,
        v.TORQUE
    FROM dbo.ENSAIO e
    JOIN dbo.ENSAIO_VALORES v ON v.COD_ENSAIO = e.COD_ENSAIO
    WHERE e.COD_ENSAIO IN ({marks})
    ORDER BY e.COD_ENSAIO, v.TEMPO
    """
    cur.execute(sql, list(cod_ensaios))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def _robust_ml_mh_from_torque(torque_vec, *, ml_hint=None, mh_hint=None):
    torque = np.asarray(torque_vec, dtype=float)
    torque = torque[np.isfinite(torque)]
    if torque.size == 0:
        ml_fallback = float(_safe_float(ml_hint, default=0.0))
        mh_fallback = float(_safe_float(mh_hint, default=max(ml_fallback + 1.0, 1.0)))
        if mh_fallback <= ml_fallback:
            mh_fallback = ml_fallback + 1e-6
        return ml_fallback, mh_fallback

    size = int(torque.size)
    head_count = int(np.clip(round(size * 0.10), 5, 40))
    tail_count = int(np.clip(round(size * 0.20), 5, 60))
    head = torque[:head_count]
    tail = torque[-tail_count:]

    ml_candidates = [
        float(np.mean(head)),
        float(np.percentile(head, 10.0)),
        float(np.percentile(torque, 5.0)),
    ]
    mh_candidates = [
        float(np.mean(tail)),
        float(np.percentile(tail, 90.0)),
        float(np.percentile(torque, 95.0)),
    ]

    ml_hint_val = _safe_float(ml_hint, default=None)
    mh_hint_val = _safe_float(mh_hint, default=None)
    if ml_hint_val is not None:
        ml_candidates.append(float(ml_hint_val))
    if mh_hint_val is not None:
        mh_candidates.append(float(mh_hint_val))

    ml = float(np.median(np.asarray(ml_candidates, dtype=float)))
    mh = float(np.median(np.asarray(mh_candidates, dtype=float)))

    torque_min = float(np.min(torque))
    torque_max = float(np.max(torque))
    ml = float(np.clip(ml, torque_min, torque_max))
    mh = float(np.clip(mh, torque_min, torque_max))

    if mh <= ml + 1e-9:
        ml = torque_min
        mh = torque_max
    if mh <= ml + 1e-9:
        mh = ml + 1e-6
    return ml, mh


def _group_curves(rows):
    grouped = {}
    for r in rows:
        key = int(r["COD_ENSAIO"])
        grouped.setdefault(
            key,
            {
                "COD_ENSAIO": key,
                "T_C": float(r["TEMP_PLATO_INF"] or 0),
                "T_K": float(r["TEMP_PLATO_INF"] or 0) + 273.15,
                "ML": float(r["TMINTORQUE"] or 0),
                "MH": float(r["TMAXTORQUE"] or 0),
                "TMINTEMPO": float(r["TMINTEMPO"] or 0),
                "tempo": [],
                "torque": [],
            },
        )
        grouped[key]["tempo"].append(float(r["TEMPO"] or 0))
        grouped[key]["torque"].append(float(r["TORQUE"] or 0))

    curves = []
    for c in grouped.values():
        tempo = np.asarray(c["tempo"], dtype=float)
        torque = np.asarray(c["torque"], dtype=float)
        ml_db = _safe_float(c.get("ML"), default=None)
        mh_db = _safe_float(c.get("MH"), default=None)
        ml_robust, mh_robust = _robust_ml_mh_from_torque(torque, ml_hint=ml_db, mh_hint=mh_db)

        t_rel = np.maximum(tempo - float(c["TMINTEMPO"]), 0.0)
        alpha = alpha_from_torque(torque, ml_robust, mh_robust)

        c["ML_raw"] = ml_db
        c["MH_raw"] = mh_db
        c["ML"] = float(ml_robust)
        c["MH"] = float(mh_robust)
        c["t_rel"] = t_rel
        c["alpha"] = alpha
        curves.append(c)
    return curves


def _serialize_curves_for_payload(curves):
    serialized = []
    for c in curves:
        serialized.append(
            {
                "COD_ENSAIO": c["COD_ENSAIO"],
                "T_C": c["T_C"],
                "T_K": c["T_K"],
                "ML": c["ML"],
                "MH": c["MH"],
                "ML_raw": c.get("ML_raw"),
                "MH_raw": c.get("MH_raw"),
                "TMINTEMPO": c["TMINTEMPO"],
                "tempo": np.asarray(c["tempo"], dtype=float).tolist(),
                "torque": np.asarray(c["torque"], dtype=float).tolist(),
                "t_rel": np.asarray(c["t_rel"], dtype=float).tolist(),
                "alpha": np.asarray(c["alpha"], dtype=float).tolist(),
                "markers": dict(c.get("markers") or {}),
            }
        )
    return serialized


def run_fit(
    cod_ensaios,
    *,
    model_family=DEFAULT_MODEL_FAMILY,
    fit_method=DEFAULT_FIT_METHOD,
    reference_temperature_k=None,
    cure_completion_rule=CURE_COMPLETION_RULE_ALPHA_099,
):
    rows = get_curve_points(cod_ensaios)
    curves = _group_curves(rows)
    _attach_curve_markers(curves)

    if reference_temperature_k is None and curves:
        reference_temperature_k = float(np.median([float(c["T_K"]) for c in curves]))

    result = fit_model_parameters(
        curves=curves,
        model_family=model_family,
        reference_temperature_k=reference_temperature_k,
        fit_method=fit_method,
    )

    model_family_norm = normalize_model_family(result.get("model_family") or model_family)
    fit_method_norm = normalize_fit_method(result.get("fit_method") or fit_method)
    cure_rule_norm = normalize_cure_completion_rule(cure_completion_rule)
    ref_k = _safe_float(result.get("reference_temperature_K"), default=reference_temperature_k)
    if ref_k is None:
        ref_k = DEFAULT_REFERENCE_TEMPERATURE_K

    induction_payload, induction_fit = _build_induction_payload(curves)

    payload = {
        "fit_id": datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "created_at": datetime.utcnow().isoformat(),
        **result,
        "model_family": model_family_norm,
        "requested_model_family": normalize_model_family(result.get("requested_model_family") or model_family),
        "model_version": str(result.get("model_version") or DEFAULT_MODEL_VERSION),
        "fit_method": fit_method_norm,
        "reference_temperature_K": float(max(ref_k, 1.0)),
        "cure_completion_rule": cure_rule_norm,
        "kinetic_model_version": 3,
        "kinetic_model_expression": MODEL_EXPRESSIONS.get(model_family_norm),
        "induction_model_version": INDUCTION_MODEL_VERSION,
        "induction_model_expression": INDUCTION_MODEL_EXPRESSION,
        "induction": induction_payload,
        "A_ind": induction_fit.get("A_ind"),
        "E_ind": induction_fit.get("E_ind"),
        "induction_confidence": induction_payload.get("confidence"),
        "induction_source": induction_payload.get("source"),
        "induction_fit_quality": induction_payload.get("fit_quality"),
        "temperature_validity_range": induction_payload.get("temperature_validity_range"),
        "induction_model_regime": induction_payload.get("model_regime"),
        "ensaios": [
            {"COD_ENSAIO": c["COD_ENSAIO"], "T_C": c["T_C"], "T_K": c["T_K"]}
            for c in curves
        ],
        "experimental_markers": extract_experimental_markers(curves),
        "curves": _serialize_curves_for_payload(curves),
        "legacy_arrhenius_mode": False,
    }
    if result.get("auto_model_selection") is not None:
        payload["auto_model_selection"] = result.get("auto_model_selection")
    if result.get("auto_selected_model_family") is not None:
        payload["auto_selected_model_family"] = result.get("auto_selected_model_family")

    payload, _ = _normalize_fit_payload_model(payload)

    if payload.get("success"):
        _rebuild_curve_predictions(payload)
        _compute_payload_metrics(payload)

    save_fit_payload(payload)
    return payload


def save_fit_payload(payload):
    file_path = OUT_DIR / f"fit_{payload['fit_id']}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(file_path)


def load_fit_payload(fit_id):
    file_path = OUT_DIR / f"fit_{fit_id}.json"
    if not file_path.exists():
        return None
    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    payload, migrated = _normalize_fit_payload_model(payload)
    curves = payload.get("curves") or []
    if curves and any(not isinstance((curve or {}).get("markers"), dict) for curve in curves):
        _attach_curve_markers(curves)
        payload["experimental_markers"] = extract_experimental_markers(curves)
        migrated = True

    if payload.get("success"):
        _rebuild_curve_predictions(payload)
        _compute_payload_metrics(payload)

    if migrated:
        save_fit_payload(payload)

    return payload


def update_fit_parameters(fit_id, k_value=None, ea=None, n=None, parameter_updates=None):
    payload = load_fit_payload(fit_id)
    if not payload:
        return None

    family = normalize_model_family(payload.get("model_family"))
    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        updates = {}
        for key in ("Av1", "Av2", "Ev", "X", "Ar", "Er"):
            numeric = _safe_float((parameter_updates or {}).get(key), default=None)
            if numeric is not None:
                updates[key] = float(numeric)
        if not updates:
            if k_value is not None:
                updates["Av1"] = float(k_value)
            if ea is not None:
                updates["Ev"] = float(ea)
            if n is not None:
                updates["X"] = float(n)
        payload = update_model_parameters(payload, parameter_updates=updates)
    else:
        if family == MODEL_FAMILY_EDO_ORDER_N_V1:
            k_safe = float(np.clip(float(k_value), 1e-12, 1e8))
        else:
            k_safe = float(np.clip(float(k_value), 1e-12, 1e8))

        ea_safe = float(np.clip(float(ea), 0.0, 350000.0))
        n_safe = float(np.clip(float(n), 0.2, 12.0))
        payload = update_model_parameters(payload, k_value=k_safe, ea=ea_safe, n=n_safe)

    payload["updated_at"] = datetime.utcnow().isoformat()

    payload, _ = _normalize_fit_payload_model(payload)
    _rebuild_curve_predictions(payload)
    _compute_payload_metrics(payload)

    save_fit_payload(payload)
    return payload


def get_preview_curve(cod_ensaio):
    rows = get_curve_points([int(cod_ensaio)])
    curves = _group_curves(rows)
    if not curves:
        return None
    c = curves[0]
    return {
        "COD_ENSAIO": c["COD_ENSAIO"],
        "tempo": c["tempo"],
        "torque": c["torque"],
        "t_rel": c["t_rel"].tolist(),
        "alpha": c["alpha"].tolist(),
    }


def build_alpha_time_comparison(payload, alpha_targets=None):
    alpha_targets = [float(a) for a in (alpha_targets or ALPHA_COMPARE_TARGETS_DEFAULT)]
    rows = []
    curves = payload.get("curves") or []

    for c in curves:
        t_rel = np.asarray(c.get("t_rel") or [], dtype=float)
        alpha_real = np.asarray(c.get("alpha") or [], dtype=float)
        alpha_sim = np.asarray(c.get("alpha_model") or [], dtype=float) if c.get("alpha_model") is not None else None

        can_eval_real = len(t_rel) >= 1 and len(t_rel) == len(alpha_real)
        can_eval_sim = alpha_sim is not None and len(t_rel) >= 1 and len(t_rel) == len(alpha_sim)

        comparisons = []
        for alpha_target in alpha_targets:
            t_real = crossing_or_nearest_time(t_rel, alpha_real, alpha_target) if can_eval_real else None
            t_sim = crossing_or_nearest_time(t_rel, alpha_sim, alpha_target) if can_eval_sim else None
            comparisons.append(
                {
                    "alpha": alpha_target,
                    "real_s": float(t_real) if t_real is not None else None,
                    "sim_s": float(t_sim) if t_sim is not None else None,
                    "diff_s": float(t_sim - t_real) if (t_real is not None and t_sim is not None) else None,
                }
            )

        rows.append(
            {
                "COD_ENSAIO": c.get("COD_ENSAIO"),
                "T_C": c.get("T_C"),
                "comparisons": comparisons,
            }
        )

    return rows
