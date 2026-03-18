import numpy as np

from services.engine_registry import (
    ENGINE_AXISYMMETRIC_FIPY,
    ENGINE_EMPIRICAL_V1,
    ENGINE_THERMO_KINETIC_V2,
    get_engine_status,
    normalize_engine,
)


ENGINE_TYPE_BY_KEY = {
    ENGINE_EMPIRICAL_V1: "operational",
    ENGINE_THERMO_KINETIC_V2: "physical",
    ENGINE_AXISYMMETRIC_FIPY: "physical",
}

ENGINE_ROLE_LABEL_BY_KEY = {
    ENGINE_EMPIRICAL_V1: "operational_baseline",
    ENGINE_THERMO_KINETIC_V2: "physical_experimental",
    ENGINE_AXISYMMETRIC_FIPY: "physical_prototype",
}

LOW_CONFIDENCE_FLAGS = {
    "",
    "low",
    "unknown",
    "insufficient_data",
    "not_calibrated",
    "manual_override_blocked",
}


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def _safe_bool(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _field_from_payload(payload, *keys):
    source = payload if isinstance(payload, dict) else {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
        if key in metrics and metrics.get(key) is not None:
            return metrics.get(key)
    return None


def _mean_series(snapshots):
    arr = np.asarray(snapshots, dtype=float)
    if arr.ndim == 0:
        return np.asarray([], dtype=float)
    if arr.ndim == 1:
        return np.asarray(arr, dtype=float)
    axes = tuple(range(1, arr.ndim))
    return np.mean(arr, axis=axes)


def _final_stats(snapshots):
    arr = np.asarray(snapshots, dtype=float)
    if arr.size == 0:
        return None, None, None
    if arr.ndim == 1:
        final = np.asarray(arr, dtype=float)
    else:
        final = np.asarray(arr[-1], dtype=float)
    return (
        _safe_float(np.mean(final), default=None),
        _safe_float(np.min(final), default=None),
        _safe_float(np.max(final), default=None),
    )


def _time_to_threshold(times, alpha_mean_series, threshold):
    if len(times) == 0 or len(alpha_mean_series) == 0:
        return None
    size = int(min(len(times), len(alpha_mean_series)))
    if size <= 0:
        return None
    time_arr = np.asarray(times[:size], dtype=float)
    alpha_arr = np.asarray(alpha_mean_series[:size], dtype=float)
    idx = np.where(alpha_arr >= float(threshold))[0]
    if idx.size == 0:
        return None
    return _safe_float(time_arr[int(idx[0])], default=None)


def _extract_mold_temp_c(payload):
    value = _field_from_payload(
        payload,
        "mold_temp_c",
        "mold_temperature_c",
        "temp_molde_c",
        "temperature_max_c",
    )
    return _safe_float(value, default=None)


def _extract_core_temp_max_c(payload, t_snaps):
    value = _field_from_payload(payload, "core_temp_max_c", "temperature_max_c")
    numeric = _safe_float(value, default=None)
    if numeric is not None:
        return numeric
    arr = np.asarray(t_snaps, dtype=float)
    if arr.size == 0:
        return None
    return _safe_float(np.max(arr) - 273.15, default=None)


def _prediction_validity_for_engine(
    *,
    engine_key,
    induction_extrapolation_warning,
    induction_extrapolation_level,
    induction_confidence,
    process_state_final,
    alpha_mean_final,
    quality_status,
    degraded_mode,
):
    if engine_key != ENGINE_THERMO_KINETIC_V2:
        return {
            "prediction_validity": "operational_baseline",
            "prediction_validity_reason": "empirical_engine_baseline",
        }

    confidence_norm = str(induction_confidence or "").strip().lower()
    state_norm = str(process_state_final or "").strip().upper()
    quality_norm = str(quality_status or "").strip().lower()
    level_norm = str(induction_extrapolation_level or "").strip().lower()
    degraded_mode_norm = str(degraded_mode or "none").strip().lower()

    low_confidence = confidence_norm in LOW_CONFIDENCE_FLAGS
    stuck_heating = state_norm == "HEATING"
    low_cure = (alpha_mean_final is not None) and (float(alpha_mean_final) <= 0.02)
    critical_extrapolation = level_norm == "strong"
    invalid_combo = bool(induction_extrapolation_warning and low_confidence and stuck_heating and low_cure)

    if degraded_mode_norm in {"blocked_prediction", "blocked"}:
        return {
            "prediction_validity": "invalid_for_prediction",
            "prediction_validity_reason": "degraded_mode_blocked_prediction",
        }
    if invalid_combo:
        return {
            "prediction_validity": "invalid_for_prediction",
            "prediction_validity_reason": "critical_extrapolation_with_no_cure_progress",
        }
    if bool(induction_extrapolation_warning) and (critical_extrapolation or quality_norm == "critical"):
        return {
            "prediction_validity": "invalid_for_prediction",
            "prediction_validity_reason": "critical_extrapolation",
        }
    if bool(induction_extrapolation_warning) or low_confidence or quality_norm in {"warning", "critical"}:
        return {
            "prediction_validity": "low_confidence_exploratory",
            "prediction_validity_reason": "outside_recommended_induction_validity",
        }
    return {
        "prediction_validity": "valid_for_prediction",
        "prediction_validity_reason": "within_recommended_induction_validity",
    }


def build_reliability_note(diagnostics):
    prediction_validity = str(diagnostics.get("prediction_validity") or "")
    reason = str(diagnostics.get("prediction_validity_reason") or "")
    process_state = str(diagnostics.get("process_state_final") or "")
    alpha_mean_final = _safe_float(diagnostics.get("alpha_mean_final"), default=None)

    if prediction_validity == "invalid_for_prediction":
        return "Resultado exploratorio: invalido para previsao confiavel."
    if prediction_validity == "low_confidence_exploratory":
        return "Resultado exploratorio: usar apenas para analise qualitativa."
    if prediction_validity == "operational_baseline":
        return "Engine operacional de baseline."
    if process_state == "HEATING" and (alpha_mean_final is not None) and alpha_mean_final < 0.05:
        return "Simulacao terminou em HEATING com baixa cura."
    if reason:
        return f"Confianca: {reason}."
    return "Resultado dentro da faixa recomendada."


def extract_simulation_diagnostics(sim_payload, *, engine=None):
    payload = sim_payload if isinstance(sim_payload, dict) else {}
    engine_key = normalize_engine(engine or payload.get("engine"))

    times = np.asarray(payload.get("times") if payload.get("times") is not None else [], dtype=float).reshape(-1)
    alpha_snaps = np.asarray(payload.get("alpha_snaps") if payload.get("alpha_snaps") is not None else [], dtype=float)
    t_snaps = np.asarray(payload.get("t_snaps") if payload.get("t_snaps") is not None else [], dtype=float)

    if alpha_snaps.size == 0:
        alpha_c = np.asarray(payload.get("alpha_c_snaps") if payload.get("alpha_c_snaps") is not None else [], dtype=float)
        alpha_r = np.asarray(payload.get("alpha_r_snaps") if payload.get("alpha_r_snaps") is not None else [], dtype=float)
        if alpha_c.size > 0:
            if alpha_r.size == 0:
                alpha_r = np.zeros_like(alpha_c, dtype=float)
            alpha_snaps = np.clip(alpha_c - alpha_r, 0.0, 1.0)

    alpha_mean_series = _mean_series(alpha_snaps)
    alpha_mean_final, alpha_min_final, alpha_max_final = _final_stats(alpha_snaps)

    process_state_final = str(
        _field_from_payload(payload, "process_state_final", "process_state") or "HEATING"
    ).strip()
    induction_confidence = str(_field_from_payload(payload, "induction_confidence") or "low").strip().lower()
    induction_extrapolation_warning = _safe_bool(
        _field_from_payload(payload, "induction_extrapolation_warning")
    )
    induction_extrapolation_level = str(
        _field_from_payload(payload, "induction_extrapolation_level") or ""
    ).strip().lower()
    quality_status = str(_field_from_payload(payload, "quality_status") or "healthy").strip().lower()
    degraded_mode = str(_field_from_payload(payload, "degraded_mode") or "none").strip().lower()

    validity = _prediction_validity_for_engine(
        engine_key=engine_key,
        induction_extrapolation_warning=bool(induction_extrapolation_warning),
        induction_extrapolation_level=induction_extrapolation_level,
        induction_confidence=induction_confidence,
        process_state_final=process_state_final,
        alpha_mean_final=alpha_mean_final,
        quality_status=quality_status,
        degraded_mode=degraded_mode,
    )

    diagnostics = {
        "engine": engine_key,
        "engine_status": get_engine_status(engine_key),
        "engine_type": ENGINE_TYPE_BY_KEY.get(engine_key, "unknown"),
        "engine_role": ENGINE_ROLE_LABEL_BY_KEY.get(engine_key, "unknown"),
        "alpha_mean_final": alpha_mean_final,
        "alpha_min_final": alpha_min_final,
        "alpha_max_final": alpha_max_final,
        "time_to_alpha_0_1": _time_to_threshold(times, alpha_mean_series, 0.1),
        "time_to_alpha_0_5": _time_to_threshold(times, alpha_mean_series, 0.5),
        "time_to_alpha_0_9": _time_to_threshold(times, alpha_mean_series, 0.9),
        "mold_temp_c": _extract_mold_temp_c(payload),
        "core_temp_max_c": _extract_core_temp_max_c(payload, t_snaps),
        "process_state_final": process_state_final,
        "induction_confidence": induction_confidence,
        "induction_extrapolation_warning": bool(induction_extrapolation_warning),
        "quality_status": quality_status,
        "total_simulated_time": _safe_float(times[-1], default=None) if len(times) else None,
        "prediction_validity": validity["prediction_validity"],
        "prediction_validity_reason": validity["prediction_validity_reason"],
        "degraded_mode": degraded_mode,
    }
    diagnostics["reliability_note"] = build_reliability_note(diagnostics)
    return diagnostics


def build_engine_comparison(simulations_by_engine):
    ordered_engines = [ENGINE_EMPIRICAL_V1, ENGINE_THERMO_KINETIC_V2]
    diagnostics_by_engine = {}
    for engine_key in ordered_engines:
        sim_payload = (simulations_by_engine or {}).get(engine_key)
        if sim_payload is None:
            continue
        diagnostics_by_engine[engine_key] = extract_simulation_diagnostics(sim_payload, engine=engine_key)

    metric_keys = [
        "alpha_mean_final",
        "alpha_min_final",
        "alpha_max_final",
        "time_to_alpha_0_1",
        "time_to_alpha_0_5",
        "time_to_alpha_0_9",
        "mold_temp_c",
        "core_temp_max_c",
        "process_state_final",
        "induction_confidence",
        "induction_extrapolation_warning",
        "quality_status",
        "prediction_validity",
        "total_simulated_time",
    ]

    rows = []
    left = diagnostics_by_engine.get(ENGINE_EMPIRICAL_V1, {})
    right = diagnostics_by_engine.get(ENGINE_THERMO_KINETIC_V2, {})
    for key in metric_keys:
        left_value = left.get(key)
        right_value = right.get(key)
        delta = None
        left_num = _safe_float(left_value, default=None)
        right_num = _safe_float(right_value, default=None)
        if left_num is not None and right_num is not None:
            delta = float(right_num - left_num)
        rows.append(
            {
                "key": key,
                ENGINE_EMPIRICAL_V1: left_value,
                ENGINE_THERMO_KINETIC_V2: right_value,
                "delta_v2_minus_v1": delta,
            }
        )

    return {
        "available_engines": [engine for engine in ordered_engines if engine in diagnostics_by_engine],
        "diagnostics_by_engine": diagnostics_by_engine,
        "rows": rows,
    }
