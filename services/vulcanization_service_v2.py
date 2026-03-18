import math
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from services.mechanistic_kinetics import (
    MechanisticKinetics,
    MechanisticKineticsParams,
    R_GAS,
)
from services.process_state_machine import (
    ProcessState,
    ProcessThermalConfig,
    VulcanizationProcessStateMachine,
)
from services.thermo_solver import (
    CP_RUBBER,
    LAMBDA_RUBBER,
    QV_VULCANIZATION,
    RHO_RUBBER,
    reaction_heat_source,
    thermal_increment_from_source,
)
from services.kinetic_model_service import (
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
    extract_model_parameters,
    normalize_model_family,
)
from services.simulation_diagnostics import extract_simulation_diagnostics

OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_SNAPSHOT_BYTES = 768 * 1024 * 1024  # 768 MB for persisted snapshots
MAX_SNAPSHOT_CELLS = 1_500_000
# Safe fallback defaults: avoid near-instant induction when calibration is missing
# while keeping the solver operational.
# They target t_scorch ~15 s at 180 C and ~90 s at 150 C.
DEFAULT_A_IND = 6.3e9  # 1/s
DEFAULT_E_IND = 9.52e4  # J/mol
INDUCTION_MIN_SCORCH_REF_TEMP_C = 180.0
INDUCTION_MIN_SCORCH_REF_TIME_S = 0.1
INDUCTION_EXTRAPOLATION_MILD_DELTA_C = 5.0
INDUCTION_EXTRAPOLATION_STRONG_DELTA_C = 15.0
INDUCTION_ACTIVATION_MIN_TEMP_C = 95.0
INDUCTION_ACTIVATION_RAMP_C = 12.0
INDUCTION_LOW_EXTRAP_DECAY_C = 12.0
CRITICAL_TRACE_KEEP_STEPS = 5
MAX_SUBSTEPS_PER_STEP = 256
QUALITY_THRESHOLDS = {
    "clip_events_count": {"info": 1, "warning": 25, "critical": 120},
    "nan_recovery_events": {"info": 1, "warning": 1, "critical": 6},
    "max_source_to_diffusion_dT_ratio": {"info": 5.0, "warning": 9.0, "critical": 16.0},
    "substeps_max_per_step": {"info": 4, "warning": 8, "critical": 16},
    "stiffness_alert_count": {"info": 1, "warning": 2, "critical": 4},
}


def _build_simulation_v2_path(sim_id):
    return OUT_DIR / f"sim_v2_{sim_id}.npz"


def _parse_shape(dim, shape_raw):
    parts = [int(x.strip()) for x in str(shape_raw).split(",") if str(x).strip()]
    if len(parts) != dim:
        defaults = {1: [200], 2: [80, 80], 3: [30, 40, 40]}
        return tuple(defaults[dim])
    return tuple(parts)


def _temperature_profile(mode, t, base_temp, ramp_rate):
    if mode == "autoclave" and ramp_rate > 0:
        return base_temp + ramp_rate * t
    return base_temp


def _apply_dirichlet_boundaries(field, boundary_value, axes=None):
    dim = field.ndim
    if axes is None:
        axes = tuple(range(dim))
    else:
        axes = tuple(int(a) for a in axes if 0 <= int(a) < dim)

    slices = [slice(None)] * dim
    for axis in axes:
        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        field[tuple(low)] = boundary_value
        field[tuple(high)] = boundary_value


def _apply_convective_boundaries(field, dx, conductivity, h_conv, ambient_k, axes=None):
    """
    Robin boundary condition using a half-cell flux balance at faces:
      -k * (T_face - T_inner) / (dx/2) = h * (T_face - T_inf)

    Rearranged boundary update:
      T_face = ((2*k/dx) * T_inner + h * T_inf) / ((2*k/dx) + h)
    """
    dim = field.ndim
    if axes is None:
        axes = tuple(range(dim))
    else:
        axes = tuple(int(a) for a in axes if 0 <= int(a) < dim)

    h_conv = float(max(h_conv, 0.0))
    dx_safe = float(max(dx, 1e-12))
    cond_safe = float(max(conductivity, 1e-12))
    inner_weight = 2.0 * cond_safe / dx_safe
    denom = inner_weight + h_conv
    if denom <= 0.0:
        return
    ambient_k = float(ambient_k)

    slices = [slice(None)] * dim
    for axis in axes:
        if field.shape[axis] < 2:
            continue
        low = slices.copy()
        low[axis] = 0
        low_inner = slices.copy()
        low_inner[axis] = 1
        high = slices.copy()
        high[axis] = -1
        high_inner = slices.copy()
        high_inner[axis] = -2
        low_t = tuple(low)
        low_inner_t = tuple(low_inner)
        high_t = tuple(high)
        high_inner_t = tuple(high_inner)
        field[low_t] = (inner_weight * field[low_inner_t] + h_conv * ambient_k) / denom
        field[high_t] = (inner_weight * field[high_inner_t] + h_conv * ambient_k) / denom


def _stable_dt_limit(alpha_diff, dx, dim):
    dim_safe = max(int(dim), 1)
    return (dx * dx) / (2.0 * dim_safe * alpha_diff)


def _substep_config(dt, alpha_diff, dx, dim, safety=0.95):
    dt_limit = _stable_dt_limit(alpha_diff, dx, dim) * float(safety)
    if dt_limit <= 0:
        return 1, dt
    substeps = max(1, int(math.ceil(dt / dt_limit)))
    return substeps, dt / substeps


def _snapshot_plan(shape, dim, steps, snapshot_every):
    base_every = max(int(snapshot_every), 1)
    total_cells = int(np.prod(shape, dtype=np.int64))

    if total_cells <= 0:
        return {
            "snapshot_every_store": base_every,
            "spatial_stride": 1,
            "stored_shape": tuple(shape),
            "store_indices": [0, steps],
            "estimated_bytes": 0,
            "downsampled": False,
        }

    stride = 1
    if total_cells > MAX_SNAPSHOT_CELLS:
        stride = max(1, int(math.ceil((total_cells / MAX_SNAPSHOT_CELLS) ** (1.0 / max(dim, 1)))))

    def _stored_shape_for_stride(current_stride):
        return tuple(((int(n) - 1) // current_stride) + 1 for n in shape)

    def _store_indices_for_every(current_every):
        indices = list(range(0, steps + 1, current_every))
        if not indices or indices[-1] != steps:
            indices.append(steps)
        return indices

    def _estimated_bytes(current_shape, index_count):
        # Stored fields: temperature, alpha_c, alpha_r, alpha_unstable, alpha, heat_source, induction_progress.
        cells = int(np.prod(current_shape, dtype=np.int64))
        return cells * index_count * 7 * np.dtype(np.float32).itemsize

    stored_shape = _stored_shape_for_stride(stride)
    snapshot_every_store = base_every
    store_indices = _store_indices_for_every(snapshot_every_store)
    est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    if est_bytes > MAX_SNAPSHOT_BYTES:
        time_stride = int(math.ceil(est_bytes / MAX_SNAPSHOT_BYTES))
        snapshot_every_store = max(base_every, base_every * max(time_stride, 1))
        store_indices = _store_indices_for_every(snapshot_every_store)
        est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    while est_bytes > MAX_SNAPSHOT_BYTES and stride < max(shape):
        stride += 1
        stored_shape = _stored_shape_for_stride(stride)
        est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    return {
        "snapshot_every_store": snapshot_every_store,
        "spatial_stride": int(stride),
        "stored_shape": tuple(int(x) for x in stored_shape),
        "store_indices": store_indices,
        "estimated_bytes": int(est_bytes),
        "downsampled": bool(int(stride) > 1 or snapshot_every_store > base_every),
    }


def _laplacian_with_mixed_boundaries(field, dx, dirichlet_axes, dirichlet_value):
    dim = field.ndim
    lap = np.zeros_like(field)
    slices = [slice(None)] * dim

    for axis in range(dim):
        backward = np.roll(field, 1, axis=axis)
        forward = np.roll(field, -1, axis=axis)

        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        low_t = tuple(low)
        high_t = tuple(high)

        if axis in dirichlet_axes:
            backward[low_t] = dirichlet_value
            forward[high_t] = dirichlet_value
        else:
            backward[low_t] = field[low_t]
            forward[high_t] = field[high_t]

        lap += (backward - (2.0 * field) + forward) / (dx * dx)

    return lap


def _safe_material_props(rho, cp, lambda_rubber, qv):
    rho_val = float(max(rho, 1e-9))
    cp_val = float(max(cp, 1e-9))
    lambda_val = float(max(lambda_rubber, 1e-12))
    qv_val = float(max(qv, 0.0))
    return rho_val, cp_val, lambda_val, qv_val


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return out


def _to_list_of_strings(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(x) for x in value]
    return [str(value)]


def _solve_tridiagonal_many(lower, diag, upper, rhs):
    diag_arr = np.asarray(diag, dtype=float)
    n = int(diag_arr.size)
    if n <= 0:
        return np.asarray(rhs, dtype=float)

    rhs_arr = np.asarray(rhs, dtype=float)
    if rhs_arr.ndim == 1:
        rhs_mat = rhs_arr.reshape(n, 1).copy()
        squeeze_back = True
    else:
        rhs_mat = rhs_arr.copy()
        squeeze_back = False

    if n == 1:
        denom = float(diag_arr[0])
        if abs(denom) < 1e-18:
            denom = 1e-18
        out = rhs_mat / denom
        return out[:, 0] if squeeze_back else out

    lower_arr = np.asarray(lower, dtype=float)
    upper_arr = np.asarray(upper, dtype=float)

    c_prime = np.zeros((n - 1,), dtype=float)
    d_prime = rhs_mat

    denom0 = float(diag_arr[0])
    if abs(denom0) < 1e-18:
        denom0 = 1e-18
    c_prime[0] = float(upper_arr[0]) / denom0
    d_prime[0, :] = d_prime[0, :] / denom0

    for i in range(1, n):
        denom = float(diag_arr[i] - (lower_arr[i - 1] * c_prime[i - 1]))
        if abs(denom) < 1e-18:
            denom = 1e-18
        if i < (n - 1):
            c_prime[i] = float(upper_arr[i]) / denom
        d_prime[i, :] = (d_prime[i, :] - (lower_arr[i - 1] * d_prime[i - 1, :])) / denom

    out = np.empty_like(d_prime)
    out[-1, :] = d_prime[-1, :]
    for i in range(n - 2, -1, -1):
        out[i, :] = d_prime[i, :] - (c_prime[i] * out[i + 1, :])

    return out[:, 0] if squeeze_back else out


def _implicit_axis_sweep(field, axis, coeff, *, dirichlet_axes=(), dirichlet_value=0.0):
    arr = np.moveaxis(np.asarray(field, dtype=float), int(axis), 0)
    n = int(arr.shape[0])
    if n <= 1 or coeff <= 0.0:
        return np.asarray(field, dtype=float)

    flat = arr.reshape(n, -1)
    rhs = flat.copy()

    lower = np.full((n - 1,), -float(coeff), dtype=float)
    diag = np.full((n,), 1.0 + (2.0 * float(coeff)), dtype=float)
    upper = np.full((n - 1,), -float(coeff), dtype=float)

    if int(axis) in set(int(a) for a in dirichlet_axes):
        diag[0] = 1.0
        diag[-1] = 1.0
        upper[0] = 0.0
        lower[-1] = 0.0
        rhs[0, :] = float(dirichlet_value)
        rhs[-1, :] = float(dirichlet_value)
    else:
        diag[0] = 1.0 + float(coeff)
        diag[-1] = 1.0 + float(coeff)

    solved = _solve_tridiagonal_many(lower, diag, upper, rhs)
    solved_arr = solved.reshape(arr.shape)
    return np.moveaxis(solved_arr, 0, int(axis))


def _imex_diffusion_step(field, source_increment, alpha_diff, dt, dx, *, dirichlet_axes=(), dirichlet_value=0.0):
    rhs = np.asarray(field, dtype=float) + np.asarray(source_increment, dtype=float)

    alpha_val = float(max(alpha_diff, 0.0))
    dt_val = float(max(dt, 0.0))
    dx_val = float(max(dx, 1e-12))
    if alpha_val <= 0.0 or dt_val <= 0.0:
        return rhs

    coeff = alpha_val * dt_val / (dx_val * dx_val)
    if coeff <= 1e-15:
        return rhs

    out = rhs
    for axis in range(rhs.ndim):
        out = _implicit_axis_sweep(
            out,
            axis=axis,
            coeff=coeff,
            dirichlet_axes=dirichlet_axes,
            dirichlet_value=dirichlet_value,
        )
    return out


def _normalize_temperature_validity_range(raw_range):
    if not isinstance(raw_range, dict):
        return None

    min_c = _safe_float(raw_range.get("min_c"), default=None)
    max_c = _safe_float(raw_range.get("max_c"), default=None)
    min_k = _safe_float(raw_range.get("min_k"), default=None)
    max_k = _safe_float(raw_range.get("max_k"), default=None)

    if min_c is None:
        min_c_candidate = _safe_float(raw_range.get("min"), default=None)
        if min_c_candidate is not None:
            min_c = float(min_c_candidate)
    if max_c is None:
        max_c_candidate = _safe_float(raw_range.get("max"), default=None)
        if max_c_candidate is not None:
            max_c = float(max_c_candidate)

    if min_c is None and min_k is not None:
        min_c = float(min_k - 273.15)
    if max_c is None and max_k is not None:
        max_c = float(max_k - 273.15)
    if min_k is None and min_c is not None:
        min_k = float(min_c + 273.15)
    if max_k is None and max_c is not None:
        max_k = float(max_c + 273.15)

    if min_c is None or max_c is None or min_k is None or max_k is None:
        return None
    if max_c < min_c:
        min_c, max_c = max_c, min_c
    if max_k < min_k:
        min_k, max_k = max_k, min_k

    span_c = float(max_c - min_c)
    span_k = float(max_k - min_k)
    return {
        "min_c": float(min_c),
        "max_c": float(max_c),
        "min_k": float(min_k),
        "max_k": float(max_k),
        "span_c": span_c,
        "span_k": span_k,
        "source": str(raw_range.get("source") or "fit_payload"),
    }


def _extract_temperature_validity_range(fit_payload):
    if not isinstance(fit_payload, dict):
        return None

    candidates = []
    candidates.append(fit_payload.get("temperature_validity_range"))

    induction_block = fit_payload.get("induction")
    if isinstance(induction_block, dict):
        candidates.append(induction_block.get("temperature_validity_range"))
        candidates.append(induction_block.get("temperature_range_c"))
        candidates.append(induction_block.get("temperature_range_k"))

    candidates.append(fit_payload.get("temperature_range_c"))
    candidates.append(fit_payload.get("temperature_range_k"))

    for candidate in candidates:
        normalized = _normalize_temperature_validity_range(candidate)
        if normalized is not None:
            return normalized
    return None


def _extract_induction_model_regime(fit_payload):
    if not isinstance(fit_payload, dict):
        return "single_arrhenius"
    direct = fit_payload.get("induction_model_regime")
    if direct is not None:
        return str(direct)
    induction_block = fit_payload.get("induction")
    if isinstance(induction_block, dict):
        regime = induction_block.get("model_regime")
        if regime is not None:
            return str(regime)
    return "single_arrhenius"


def _assess_induction_extrapolation(temp_min_c, temp_max_c, validity_range):
    if validity_range is None:
        return {
            "induction_extrapolation_warning": False,
            "induction_extrapolation_level": "unknown",
            "outside_low_c": 0.0,
            "outside_high_c": 0.0,
            "max_extrapolation_c": 0.0,
        }

    low_out = max(float(validity_range["min_c"]) - float(temp_min_c), 0.0)
    high_out = max(float(temp_max_c) - float(validity_range["max_c"]), 0.0)
    max_out = max(low_out, high_out)
    if max_out >= INDUCTION_EXTRAPOLATION_STRONG_DELTA_C:
        level = "strong"
    elif max_out >= 1e-9:
        level = "mild"
    else:
        level = "none"

    return {
        "induction_extrapolation_warning": bool(level in {"mild", "strong"}),
        "induction_extrapolation_level": level,
        "outside_low_c": float(low_out),
        "outside_high_c": float(high_out),
        "max_extrapolation_c": float(max_out),
    }


def _downgrade_induction_confidence(confidence, extrapolation_level):
    levels = ["low", "medium", "high"]
    confidence_norm = str(confidence or "low").lower()
    confidence_alias = {
        "manual_override": "high",
        "manual": "high",
        "unknown": "low",
        "not_calibrated": "low",
        "insufficient_data": "low",
        "manual_override_blocked": "low",
    }
    confidence_norm = confidence_alias.get(confidence_norm, confidence_norm)
    if confidence_norm not in levels:
        return confidence_norm

    idx = levels.index(confidence_norm)
    if extrapolation_level == "mild":
        return levels[max(idx - 1, 0)]
    if extrapolation_level == "strong":
        return levels[max(idx - 2, 0)]
    return confidence_norm


def _severity_from_threshold(value, threshold):
    value_f = float(value)
    if value_f >= float(threshold["critical"]):
        return "critical"
    if value_f >= float(threshold["warning"]):
        return "warning"
    if value_f >= float(threshold["info"]):
        return "info"
    return None


def _build_quality_payload(
    *,
    clip_events_count,
    nan_recovery_events,
    max_source_to_diffusion_ratio,
    substeps_max_per_step,
    stiffness_alert_count,
    induction_extrapolation_level,
    induction_extrapolation_c,
):
    flags = []

    def _append_flag(code, severity, value, threshold, message):
        if severity is None:
            return
        flags.append(
            {
                "code": str(code),
                "severity": str(severity),
                "value": float(value),
                "thresholds": {
                    "info": float(threshold["info"]),
                    "warning": float(threshold["warning"]),
                    "critical": float(threshold["critical"]),
                },
                "message": str(message),
            }
        )

    for metric_code, value, message in (
        ("clip_events_count", clip_events_count, "Clipping numerico observado."),
        ("nan_recovery_events", nan_recovery_events, "Recuperacao de NaN/Inf acionada."),
        (
            "max_source_to_diffusion_dT_ratio",
            max_source_to_diffusion_ratio,
            "Termo exotermico dominando difusao termica.",
        ),
        ("substeps_max_per_step", substeps_max_per_step, "Muitos substeps em um unico step."),
        ("stiffness_alert_count", stiffness_alert_count, "Alertas de rigidez acumulados."),
    ):
        threshold = QUALITY_THRESHOLDS[metric_code]
        severity = _severity_from_threshold(value, threshold)
        _append_flag(metric_code, severity, value, threshold, message)

    if induction_extrapolation_level == "mild":
        flags.append(
            {
                "code": "induction_extrapolation",
                "severity": "warning",
                "value": float(induction_extrapolation_c),
                "thresholds": {
                    "info": 0.0,
                    "warning": float(INDUCTION_EXTRAPOLATION_MILD_DELTA_C),
                    "critical": float(INDUCTION_EXTRAPOLATION_STRONG_DELTA_C),
                },
                "message": "Simulacao levemente fora da faixa termica calibrada da inducao.",
            }
        )
    elif induction_extrapolation_level == "strong":
        flags.append(
            {
                "code": "induction_extrapolation",
                "severity": "critical",
                "value": float(induction_extrapolation_c),
                "thresholds": {
                    "info": 0.0,
                    "warning": float(INDUCTION_EXTRAPOLATION_MILD_DELTA_C),
                    "critical": float(INDUCTION_EXTRAPOLATION_STRONG_DELTA_C),
                },
                "message": "Simulacao muito fora da faixa termica calibrada da inducao.",
            }
        )
    elif induction_extrapolation_level == "unknown":
        flags.append(
            {
                "code": "induction_validity_unknown",
                "severity": "info",
                "value": 0.0,
                "thresholds": {
                    "info": 0.0,
                    "warning": 0.0,
                    "critical": 0.0,
                },
                "message": "Faixa termica de validade da inducao nao informada.",
            }
        )

    breakdown = {"info": 0, "warning": 0, "critical": 0}
    for flag in flags:
        sev = str(flag.get("severity") or "")
        if sev in breakdown:
            breakdown[sev] += 1

    if breakdown["critical"] > 0:
        status = "critical"
    elif breakdown["warning"] > 0:
        status = "warning"
    elif breakdown["info"] > 0:
        status = "info"
    else:
        status = "healthy"

    return {
        "quality_flags": flags,
        "quality_status": status,
        "quality_severity_breakdown": breakdown,
    }


def _infer_critical_reason(step_context):
    if int(step_context.get("step_nan_events", 0)) > 0:
        return "clipping_recovery"
    if int(step_context.get("step_clip_events", 0)) > 0 and float(
        step_context.get("step_max_source_to_diffusion_ratio", 0.0)
    ) >= 8.0:
        return "clipping_recovery"
    if float(step_context.get("step_induction_extrapolation_c", 0.0)) >= INDUCTION_EXTRAPOLATION_STRONG_DELTA_C:
        return "induction_extrapolation"
    if float(step_context.get("step_max_reversion_rate", 0.0)) > max(
        0.02,
        1.2 * float(step_context.get("step_max_cure_rate", 0.0)),
    ):
        return "reversion_kinetics"
    if float(step_context.get("step_max_source_to_diffusion_ratio", 0.0)) >= 6.0:
        return "exothermia_source_dominance"
    if int(step_context.get("substeps", 1)) > 1:
        return "diffusion_substepping"
    if float(step_context.get("step_max_convective_delta_k", 0.0)) > max(
        0.2,
        float(step_context.get("step_max_dT_source", 0.0)),
    ):
        return "convection_boundary_jump"
    if float(step_context.get("step_induction_progress_rate_s_inv", 0.0)) > 0.02 and float(
        step_context.get("step_max_cure_rate", 0.0)
    ) < 1e-5:
        return "induction_front"
    return "nominal"


def _update_critical_trace(trace, step_context, keep_n=CRITICAL_TRACE_KEEP_STEPS):
    rows = list(trace or [])
    rows.append(dict(step_context))
    rows.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return rows[: int(max(keep_n, 1))]


def _clip_with_diagnostics(array, min_value, max_value, diagnostics, counter_key, *, step=None):
    arr = np.asarray(array, dtype=float)
    clipped = np.clip(arr, min_value, max_value)
    changed = int(np.count_nonzero(arr != clipped))
    if changed > 0:
        diagnostics["clip_events_count"] += changed
        diagnostics["clip_breakdown"][counter_key] = diagnostics["clip_breakdown"].get(counter_key, 0) + changed
        diagnostics["last_clip_step"] = int(step) if step is not None else diagnostics.get("last_clip_step")
    return clipped


def _nan_to_num_with_diagnostics(array, diagnostics, counter_key, *, nan=0.0, posinf=1e6, neginf=0.0, step=None):
    arr = np.asarray(array, dtype=float)
    recovered = int(np.count_nonzero(~np.isfinite(arr)))
    out = np.nan_to_num(arr, nan=nan, posinf=posinf, neginf=neginf)
    if recovered > 0:
        diagnostics["nan_recovery_events"] += recovered
        diagnostics["nan_breakdown"][counter_key] = diagnostics["nan_breakdown"].get(counter_key, 0) + recovered
        diagnostics["last_nan_recovery_step"] = int(step) if step is not None else diagnostics.get(
            "last_nan_recovery_step"
        )
    return out


def _append_numerical_warning(diagnostics, code, message, *, step=None, severity="warning"):
    warning = {
        "code": str(code),
        "severity": str(severity),
        "message": str(message),
        "step": None if step is None else int(step),
    }
    diagnostics["numerical_warnings"].append(warning)


def _resolve_induction_params(fit_payload, a_ind, e_ind):
    """
    Resolve induction Arrhenius parameters used in:
      t_scorch(T) = (1 / A_ind) * exp(E_ind / (R * T))
    """
    warnings = []

    fit_source = "safe_default"
    fit_confidence = "low"
    fit_quality = "not_calibrated"
    fit_blocked = False
    temperature_validity_range = _extract_temperature_validity_range(fit_payload)
    induction_model_regime = _extract_induction_model_regime(fit_payload)

    if isinstance(fit_payload, dict):
        fit_confidence = str(fit_payload.get("induction_confidence") or fit_confidence)
        fit_source = str(fit_payload.get("induction_source") or fit_source)
        fit_quality = str(fit_payload.get("induction_fit_quality") or fit_quality)
        induction_block = fit_payload.get("induction") if isinstance(fit_payload.get("induction"), dict) else {}
        fit_confidence = str(induction_block.get("confidence") or fit_confidence)
        fit_quality = str(induction_block.get("fit_quality") or fit_quality)
        fit_source = str(induction_block.get("source") or fit_source)

    source = None
    candidate_a = None
    candidate_e = None

    if a_ind is not None or e_ind is not None:
        source = "runtime_override"
        candidate_a = _safe_float(a_ind, default=None)
        candidate_e = _safe_float(e_ind, default=None)
        fit_confidence = "manual_override"
        fit_quality = "manual_override"
    elif isinstance(fit_payload, dict):
        payload_a = fit_payload.get("A_ind")
        payload_e = fit_payload.get("E_ind")
        if payload_a is None and isinstance(fit_payload.get("induction"), dict):
            regression = fit_payload["induction"].get("regression")
            if isinstance(regression, dict):
                payload_a = regression.get("A_ind")
                payload_e = regression.get("E_ind") if payload_e is None else payload_e

        candidate_a = _safe_float(payload_a, default=None)
        candidate_e = _safe_float(payload_e, default=None)
        if candidate_a is not None and candidate_e is not None:
            source = "fit_payload"
        else:
            source = "safe_default"
    else:
        source = "safe_default"

    if candidate_a is None or candidate_a <= 0.0:
        if source != "safe_default":
            warnings.append("invalid_A_ind_replaced_by_safe_default")
        source = "safe_default"
        candidate_a = float(DEFAULT_A_IND)
        fit_blocked = True
    if candidate_e is None or candidate_e < 0.0:
        if source != "safe_default":
            warnings.append("invalid_E_ind_replaced_by_safe_default")
        source = "safe_default"
        candidate_e = float(DEFAULT_E_IND)
        fit_blocked = True

    a_ind_val = float(max(candidate_a, 1e-30))
    e_ind_val = float(max(candidate_e, 0.0))

    # Guard rail against "almost instant" induction from non-reliable sources.
    t_ref = float(
        _scorch_time_arrhenius(
            np.asarray([INDUCTION_MIN_SCORCH_REF_TEMP_C + 273.15], dtype=float),
            a_ind_val,
            e_ind_val,
        )[0]
    )
    untrusted_source = source in {"safe_default", "runtime_override"} or str(fit_confidence).lower() in {
        "low",
        "unknown",
        "insufficient_data",
    }
    if untrusted_source and t_ref < INDUCTION_MIN_SCORCH_REF_TIME_S:
        a_ind_val = float(DEFAULT_A_IND)
        e_ind_val = float(DEFAULT_E_IND)
        t_ref = float(
            _scorch_time_arrhenius(
                np.asarray([INDUCTION_MIN_SCORCH_REF_TEMP_C + 273.15], dtype=float),
                a_ind_val,
                e_ind_val,
            )[0]
        )
        source = "safe_default"
        fit_confidence = "low"
        fit_quality = "blocked_dangerous_default"
        fit_blocked = True
        warnings.append("dangerous_near_instant_induction_blocked")

    if source == "safe_default":
        fit_confidence = "low"
        if fit_quality == "manual_override":
            fit_quality = "manual_override_blocked" if fit_blocked else fit_quality
        elif fit_quality not in {"insufficient_data", "blocked_dangerous_default"}:
            fit_quality = "not_calibrated"

    return {
        "A_ind": a_ind_val,
        "E_ind": e_ind_val,
        "induction_source": source,
        "induction_confidence": fit_confidence,
        "induction_fit_quality": fit_quality,
        "temperature_validity_range": temperature_validity_range,
        "induction_model_regime": induction_model_regime,
        "induction_guarded": bool(fit_blocked),
        "induction_warnings": warnings,
        "reference_temp_c": float(INDUCTION_MIN_SCORCH_REF_TEMP_C),
        "reference_t_scorch_s": float(t_ref),
    }


def _scorch_time_arrhenius(temp_k, a_ind, e_ind, *, diagnostics=None, step=None):
    temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
    exponent_raw = float(e_ind) / (R_GAS * temp_safe)
    if diagnostics is not None:
        exponent = _clip_with_diagnostics(
            exponent_raw,
            -700.0,
            700.0,
            diagnostics,
            "arrhenius_exponent",
            step=step,
        )
    else:
        exponent = np.clip(exponent_raw, -700.0, 700.0)
    t_scorch = np.exp(exponent) / float(max(a_ind, 1e-30))
    return np.maximum(t_scorch, 1e-12)


def _resolve_induction_activation_params(validity_range):
    if isinstance(validity_range, dict):
        min_c = _safe_float(validity_range.get("min_c"), default=None)
    else:
        min_c = None
    if min_c is None:
        activation_temp_c = float(INDUCTION_ACTIVATION_MIN_TEMP_C)
    else:
        activation_temp_c = float(max(INDUCTION_ACTIVATION_MIN_TEMP_C, min_c - INDUCTION_EXTRAPOLATION_MILD_DELTA_C))
    return {
        "activation_temp_c": activation_temp_c,
        "activation_ramp_c": float(INDUCTION_ACTIVATION_RAMP_C),
    }


def _induction_activation_factor(temp_c, activation_temp_c, activation_ramp_c):
    ramp = float(max(activation_ramp_c, 1e-6))
    temp_arr = np.asarray(temp_c, dtype=float)
    return np.clip((temp_arr - float(activation_temp_c)) / ramp, 0.0, 1.0)


def _induction_low_extrapolation_penalty(temp_c, validity_range):
    if not isinstance(validity_range, dict):
        return np.ones_like(np.asarray(temp_c, dtype=float), dtype=float)

    min_c = _safe_float(validity_range.get("min_c"), default=None)
    if min_c is None:
        return np.ones_like(np.asarray(temp_c, dtype=float), dtype=float)

    temp_arr = np.asarray(temp_c, dtype=float)
    low_delta = np.maximum(float(min_c) - temp_arr, 0.0)
    decay = float(max(INDUCTION_LOW_EXTRAP_DECAY_C, 1e-6))
    penalty = np.exp(-low_delta / decay)
    # Strong low-side extrapolation should be strongly damped in induction stage.
    penalty = np.where(low_delta >= INDUCTION_EXTRAPOLATION_STRONG_DELTA_C, penalty * 0.2, penalty)
    return np.clip(penalty, 0.0, 1.0)


def _resolve_degraded_mode(
    *,
    induction_extrapolation_level,
    induction_confidence,
    process_state_final,
    alpha_mean_final,
):
    level_norm = str(induction_extrapolation_level or "").strip().lower()
    confidence_norm = str(induction_confidence or "").strip().lower()
    state_norm = str(process_state_final or "").strip().upper()
    alpha_mean = _safe_float(alpha_mean_final, default=None)

    low_confidence = confidence_norm in {"", "low", "unknown", "insufficient_data", "not_calibrated", "manual_override_blocked"}
    low_cure = (alpha_mean is not None) and (alpha_mean <= 0.02)
    stuck_heating = state_norm == "HEATING"

    if level_norm == "strong" and low_confidence and stuck_heating and low_cure:
        return "blocked_prediction"
    if level_norm in {"mild", "strong"}:
        return "limited_exploratory"
    return "none"


def _build_kinetics(fit_payload, kinetics_params):
    defaults = MechanisticKineticsParams()
    merged = asdict(defaults)

    if isinstance(fit_payload, dict):
        family = normalize_model_family(fit_payload.get("model_family"))
        params = extract_model_parameters(fit_payload, model_family=family)
        if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
            av1 = float(max(_safe_float(params.get("Av1"), default=merged["Ac"]), 0.0))
            av2 = float(max(_safe_float(params.get("Av2"), default=0.0), 0.0))
            merged["Ac"] = av1
            merged["Eac"] = float(max(_safe_float(params.get("Ev"), default=merged["Eac"]), 0.0))
            merged["Kn"] = float(np.clip(av2 / max(av1, 1e-12), 0.0, 1000.0))
            merged["Nn"] = 2.0
            merged["Ar"] = float(max(_safe_float(params.get("Ar"), default=merged["Ar"]), 0.0))
            merged["Ear"] = float(max(_safe_float(params.get("Er"), default=merged["Ear"]), 0.0))
            merged["Kx"] = 1.0
            merged["Nx"] = 1.0
        else:
            if params.get("k0") is not None:
                merged["Ac"] = float(params["k0"])
            elif params.get("k_ref") is not None:
                merged["Ac"] = float(params["k_ref"])
            if params.get("Ea") is not None:
                merged["Eac"] = float(params["Ea"])
            if params.get("n") is not None:
                merged["Nn"] = float(params["n"])

    if isinstance(kinetics_params, MechanisticKineticsParams):
        merged.update(asdict(kinetics_params))
    elif isinstance(kinetics_params, dict):
        for key in merged:
            if key in kinetics_params and kinetics_params[key] is not None:
                merged[key] = kinetics_params[key]

    params = MechanisticKineticsParams(**merged)
    return MechanisticKinetics(params)


def save_simulation_v2(
    *,
    fit_id,
    mode,
    dim,
    stored_shape,
    full_shape,
    platen_axis,
    heated_axes,
    store_stride,
    snapshot_every_source,
    snapshot_every_store,
    dx,
    dt,
    t_end,
    times,
    t_snaps,
    alpha_c_snaps,
    alpha_r_snaps,
    alpha_snaps,
    heat_source_snaps,
    process_state_over_time,
    process_state_final,
    demold_time,
    rho,
    cp,
    lambda_rubber,
    qv,
    exotherm_enabled,
    alpha_unstable_snaps=None,
    model_family=None,
    metrics=None,
    numerical_warnings=None,
    induction_confidence="low",
    induction_source="safe_default",
    induction_fit_quality="not_calibrated",
    temperature_validity_range=None,
    induction_extrapolation_warning=False,
    induction_model_regime="single_arrhenius",
    quality_status="healthy",
    prediction_validity="low_confidence_exploratory",
    prediction_validity_reason="not_evaluated",
    degraded_mode="none",
    reliability_note="",
    A_ind=DEFAULT_A_IND,
    E_ind=DEFAULT_E_IND,
    kinetics_params=None,
    induction_progress_snaps=None,
    sim_id=None,
):
    """
    Persist v2 simulation snapshots and metadata to data/out/sim_v2_<sim_id>.npz.

    This function is versioned and isolated from legacy v1 format.
    """
    if sim_id is None:
        sim_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]

    path = _build_simulation_v2_path(sim_id)
    state_trace = np.asarray(process_state_over_time, dtype="<U16")
    demold_scalar = np.nan if demold_time is None else float(demold_time)

    np.savez_compressed(
        path,
        sim_id=str(sim_id),
        solver_version=np.array(2, dtype=int),
        fit_id=str(fit_id),
        mode=str(mode),
        dim=int(dim),
        shape=np.asarray(stored_shape, dtype=int),
        full_shape=np.asarray(full_shape, dtype=int),
        platen_axis=np.array(int(platen_axis), dtype=int),
        heated_axes=np.asarray(tuple(int(x) for x in heated_axes), dtype=int),
        store_stride=np.array(int(store_stride), dtype=int),
        snapshot_every_source=np.array(int(snapshot_every_source), dtype=int),
        snapshot_every_store=np.array(int(snapshot_every_store), dtype=int),
        dx=float(dx),
        dt=float(dt),
        t_end=float(t_end),
        model_family=np.array(str(model_family or ""), dtype="<U64"),
        times=np.asarray(times, dtype=np.float64),
        t_snaps=np.asarray(t_snaps, dtype=np.float32),
        alpha_c_snaps=np.asarray(alpha_c_snaps, dtype=np.float32),
        alpha_r_snaps=np.asarray(alpha_r_snaps, dtype=np.float32),
        alpha_unstable_snaps=np.asarray(
            alpha_unstable_snaps if alpha_unstable_snaps is not None else np.zeros_like(alpha_snaps),
            dtype=np.float32,
        ),
        alpha_snaps=np.asarray(alpha_snaps, dtype=np.float32),
        heat_source_snaps=np.asarray(heat_source_snaps, dtype=np.float32),
        induction_progress_snaps=np.asarray(
            induction_progress_snaps if induction_progress_snaps is not None else np.zeros_like(alpha_snaps),
            dtype=np.float32,
        ),
        process_state_over_time=state_trace,
        process_state_final=np.array(str(process_state_final), dtype="<U16"),
        demold_time=np.array(demold_scalar, dtype=np.float64),
        rho=np.array(float(rho), dtype=np.float64),
        cp=np.array(float(cp), dtype=np.float64),
        lambda_rubber=np.array(float(lambda_rubber), dtype=np.float64),
        qv=np.array(float(qv), dtype=np.float64),
        A_ind=np.array(float(A_ind), dtype=np.float64),
        E_ind=np.array(float(E_ind), dtype=np.float64),
        exotherm_enabled=np.array(bool(exotherm_enabled), dtype=np.bool_),
        kinetics_params=np.array(dict(kinetics_params or {}), dtype=object),
        metrics=np.array(dict(metrics or {}), dtype=object),
        numerical_warnings=np.array(list(numerical_warnings or []), dtype=object),
        induction_confidence=np.array(str(induction_confidence or "low"), dtype="<U32"),
        induction_source=np.array(str(induction_source or "safe_default"), dtype="<U64"),
        induction_fit_quality=np.array(str(induction_fit_quality or "not_calibrated"), dtype="<U64"),
        temperature_validity_range=np.array(dict(temperature_validity_range or {}), dtype=object),
        induction_extrapolation_warning=np.array(bool(induction_extrapolation_warning), dtype=np.bool_),
        induction_model_regime=np.array(str(induction_model_regime or "single_arrhenius"), dtype="<U64"),
        quality_status=np.array(str(quality_status or "healthy"), dtype="<U16"),
        prediction_validity=np.array(str(prediction_validity or "low_confidence_exploratory"), dtype="<U64"),
        prediction_validity_reason=np.array(str(prediction_validity_reason or "not_evaluated"), dtype="<U128"),
        degraded_mode=np.array(str(degraded_mode or "none"), dtype="<U64"),
        reliability_note=np.array(str(reliability_note or ""), dtype="<U256"),
    )
    return str(sim_id), str(path)


def run_simulation_v2(
    fit_payload,
    mode,
    dim,
    shape_raw,
    dx,
    dt,
    t_end,
    mold_temp_c,
    init_temp_c=25.0,
    ramp_rate=0.0,
    snapshot_every=20,
    platen_axis=None,
    progress_callback=None,
    cancel_checker=None,
    rho=RHO_RUBBER,
    cp=CP_RUBBER,
    lambda_rubber=LAMBDA_RUBBER,
    qv=QV_VULCANIZATION,
    exotherm_enabled=True,
    A_ind=None,
    E_ind=None,
    kinetics_params=None,
    h_mold=10000.0,
    h_air=11.4,
    ambient_temp_c=25.0,
    thermal_integration_mode="imex",
):
    def _emit_progress(stage, progress, message):
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": str(stage),
                    "progress": int(np.clip(progress, 0, 100)),
                    "message": str(message),
                }
            )

    def _check_cancel():
        if callable(cancel_checker) and bool(cancel_checker()):
            raise RuntimeError("SIMULATION_CANCELLED")

    rho_val, cp_val, lambda_val, qv_val = _safe_material_props(rho, cp, lambda_rubber, qv)
    induction_info = _resolve_induction_params(fit_payload, A_ind, E_ind)
    a_ind_val = float(induction_info["A_ind"])
    e_ind_val = float(induction_info["E_ind"])
    alpha_diff = lambda_val / (rho_val * cp_val)
    use_imex_diffusion = str(thermal_integration_mode or "imex").lower() != "explicit"
    model_family = normalize_model_family((fit_payload or {}).get("model_family"))
    use_leroy_kinetics = model_family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1
    leroy_params = extract_model_parameters(fit_payload or {}, model_family=model_family)
    kinetics = _build_kinetics(fit_payload, kinetics_params)
    process_config = ProcessThermalConfig(
        mold_temperature_c=float(mold_temp_c),
        ambient_temperature_c=float(ambient_temp_c),
        h_mold=float(h_mold),
        h_air=float(h_air),
        demold_alpha_mean_threshold=0.9,
    )
    process_sm = VulcanizationProcessStateMachine(process_config)

    numerical_diagnostics = {
        "clip_events_count": 0,
        "nan_recovery_events": 0,
        "clip_breakdown": {},
        "nan_breakdown": {},
        "numerical_warnings": [],
        "last_clip_step": None,
        "last_nan_recovery_step": None,
    }
    for warning_code in induction_info.get("induction_warnings") or []:
        _append_numerical_warning(
            numerical_diagnostics,
            code=warning_code,
            message=f"Induction policy warning: {warning_code}.",
            step=0,
            severity="warning",
        )

    stiffness_track = {
        "total_substeps": 0,
        "max_substeps_per_step": 0,
        "max_cure_rate": 0.0,
        "max_reversion_rate": 0.0,
        "max_heat_source": 0.0,
        "max_source_to_diffusion_ratio": 0.0,
        "critical_step_index": 0,
        "critical_step_time_s": 0.0,
        "critical_score": -1.0,
        "critical_reason": "initialization",
        "critical_context": {},
        "critical_metrics": {},
    }
    critical_steps_trace = []

    _emit_progress("validando", 1, "Validando parametros e malha (v2)...")
    shape = _parse_shape(dim, shape_raw)
    plan = _snapshot_plan(shape, dim, int(max(math.ceil(t_end / dt), 1)), snapshot_every)
    store_stride = int(plan["spatial_stride"])
    store_slices = tuple(slice(None, None, store_stride) for _ in range(dim))
    store_indices = plan["store_indices"]
    store_count = len(store_indices)

    if plan["downsampled"]:
        approx_mb = plan["estimated_bytes"] / (1024.0 * 1024.0)
        _emit_progress(
            "otimizando_memoria",
            2,
            (
                f"Aplicando otimizacao de memoria (v2): stride espacial={store_stride}, "
                f"snapshot a cada {plan['snapshot_every_store']} steps, "
                f"armazenamento estimado ~{approx_mb:.0f} MB."
            ),
        )

    steps = int(max(math.ceil(t_end / dt), 1))
    t_field = np.full(shape, init_temp_c + 273.15, dtype=np.float32)
    temp_seen_min_c = float(np.min(np.asarray(t_field, dtype=float)) - 273.15)
    temp_seen_max_c = float(np.max(np.asarray(t_field, dtype=float)) - 273.15)

    alpha_c0 = float(
        _clip_with_diagnostics(
            np.asarray([kinetics.params.alpha_c0], dtype=float),
            0.0,
            1.0,
            numerical_diagnostics,
            "alpha_c0",
            step=0,
        )[0]
    )
    alpha_r0 = float(
        _clip_with_diagnostics(
            np.asarray([kinetics.params.alpha_r0], dtype=float),
            0.0,
            alpha_c0,
            numerical_diagnostics,
            "alpha_r0",
            step=0,
        )[0]
    )
    alpha_c_field = np.full(shape, alpha_c0, dtype=np.float32)
    alpha_r_field = np.full(shape, alpha_r0, dtype=np.float32)
    alpha_field = _clip_with_diagnostics(
        alpha_c_field - alpha_r_field,
        0.0,
        1.0,
        numerical_diagnostics,
        "alpha_initial",
        step=0,
    ).astype(np.float32, copy=False)
    leroy_av1 = float(max(_safe_float(leroy_params.get("Av1"), default=0.0), 0.0))
    leroy_av2 = float(max(_safe_float(leroy_params.get("Av2"), default=0.0), 0.0))
    leroy_ev = float(max(_safe_float(leroy_params.get("Ev"), default=0.0), 0.0))
    leroy_x = float(np.clip(_safe_float(leroy_params.get("X"), default=0.7), 1e-4, 1.0 - 1e-4))
    leroy_ar = float(max(_safe_float(leroy_params.get("Ar"), default=0.0), 0.0))
    leroy_er = float(max(_safe_float(leroy_params.get("Er"), default=0.0), 0.0))
    alpha_unstable_field = np.clip(
        ((1.0 - leroy_x) * np.asarray(alpha_c_field, dtype=float)) - np.asarray(alpha_r_field, dtype=float),
        0.0,
        1.0,
    ).astype(np.float32, copy=False)
    q_source_field = np.zeros(shape, dtype=np.float32)
    induction_progress_field = np.zeros(shape, dtype=np.float32)

    if mode == "prensa":
        selected_platen_axis = 0 if platen_axis is None else int(platen_axis)
        selected_platen_axis = int(np.clip(selected_platen_axis, 0, max(dim - 1, 0)))
        heated_axes = (selected_platen_axis,)
    else:
        selected_platen_axis = -1
        heated_axes = tuple(range(dim))

    _emit_progress("inicializando", 3, "Inicializando campos acoplados (v2)...")
    times = np.empty((store_count,), dtype=np.float64)
    t_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_c_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_r_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_unstable_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    heat_source_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    induction_progress_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    process_state_over_time = np.empty((store_count,), dtype="<U16")

    prev_t = 0.0
    t_bc0 = _temperature_profile(mode, 0.0, mold_temp_c, ramp_rate) + 273.15
    _apply_dirichlet_boundaries(t_field, t_bc0, axes=heated_axes)
    induction_validity_range = induction_info.get("temperature_validity_range")
    induction_model_regime = str(induction_info.get("induction_model_regime") or "single_arrhenius")
    induction_confidence_runtime = str(induction_info.get("induction_confidence") or "low")
    induction_activation = _resolve_induction_activation_params(induction_validity_range)
    induction_activation_temp_c = float(induction_activation["activation_temp_c"])
    induction_activation_ramp_c = float(induction_activation["activation_ramp_c"])
    cure_activation_stage_counts = {
        "HEATING": 0,
        "INDUCTION": 0,
        "ACTIVE_CURE": 0,
    }
    cure_activation_stage_final = "HEATING"

    progress_stride = max(1, steps // 120)
    store_pos = 0
    _emit_progress("calculando", 5, "Executando passos de simulacao v2...")

    for step in range(steps + 1):
        _check_cancel()
        t_now = min(step * dt, t_end)

        if step > 0:
            dt_step = t_now - prev_t
            if use_imex_diffusion:
                substeps_diffusion = 1
            else:
                substeps_diffusion, _ = _substep_config(dt_step, alpha_diff, dx, dim)

            if use_leroy_kinetics:
                temp_pred = np.maximum(np.asarray(t_field, dtype=float), 1.0)
                alpha_c_pred = np.asarray(alpha_c_field, dtype=float)
                pred_cure_rate = (
                    np.maximum(leroy_av1 + (leroy_av2 * alpha_c_pred), 0.0)
                    * np.exp(np.clip(-leroy_ev / (R_GAS * temp_pred), -700.0, 700.0))
                    * np.square(np.maximum(1.0 - alpha_c_pred, 0.0))
                )
                alpha_unstable_pred = np.clip(
                    ((1.0 - leroy_x) * alpha_c_pred) - np.asarray(alpha_r_field, dtype=float),
                    0.0,
                    1.0,
                )
                pred_reversion_rate = (
                    np.maximum(leroy_ar, 0.0)
                    * np.exp(np.clip(-leroy_er / (R_GAS * temp_pred), -700.0, 700.0))
                    * alpha_unstable_pred
                )
            else:
                pred_cure_rate = kinetics.cure_rate(T=t_field, alpha_c=np.asarray(alpha_c_field, dtype=float))
                pred_reversion_rate = kinetics.reversion_rate(T=t_field, alpha_r=np.asarray(alpha_r_field, dtype=float))
            max_pred_rate = max(
                float(np.max(np.asarray(pred_cure_rate, dtype=float))),
                float(np.max(np.asarray(pred_reversion_rate, dtype=float))),
            )
            if max_pred_rate > 1e-12:
                reaction_dt_limit = max(0.35 / max_pred_rate, 1e-6)
                substeps_reaction = max(1, int(math.ceil(float(dt_step) / float(reaction_dt_limit))))
            else:
                substeps_reaction = 1

            substeps = max(int(substeps_diffusion), int(substeps_reaction), 1)
            if substeps > MAX_SUBSTEPS_PER_STEP:
                _append_numerical_warning(
                    numerical_diagnostics,
                    code="substeps_capped",
                    message=(
                        f"Substeps capped from {substeps} to {MAX_SUBSTEPS_PER_STEP} at step {step} "
                        "to keep runtime bounded."
                    ),
                    step=step,
                    severity="warning",
                )
                substeps = int(MAX_SUBSTEPS_PER_STEP)
            dt_inner = float(dt_step) / float(substeps)
            stiffness_track["total_substeps"] += int(substeps)
            stiffness_track["max_substeps_per_step"] = max(stiffness_track["max_substeps_per_step"], int(substeps))
            step_clip_start = int(numerical_diagnostics["clip_events_count"])
            step_nan_start = int(numerical_diagnostics["nan_recovery_events"])
            step_max_source_to_diffusion_ratio = 0.0
            step_max_cure_rate = 0.0
            step_max_reversion_rate = 0.0
            step_max_dT_source = 0.0
            step_max_dT_diff_ref = 0.0
            step_max_convective_delta_k = 0.0
            step_max_induction_progress_rate = 0.0
            step_max_induction_extrapolation_c = 0.0

            for sub_idx in range(substeps):
                _check_cancel()
                t_sub = prev_t + (sub_idx + 1) * dt_inner
                t_mold_k = _temperature_profile(mode, t_sub, mold_temp_c, ramp_rate) + 273.15

                if process_sm.state == ProcessState.HEATING:
                    h_conv = process_config.h_mold
                    ambient_k = t_mold_k
                    convective_axes = heated_axes
                else:
                    h_conv = process_config.h_air
                    ambient_k = process_config.ambient_temperature_c + 273.15
                    convective_axes = tuple(range(dim))

                lap = _laplacian_with_mixed_boundaries(t_field, dx, tuple(), 0.0)
                dT_diff_ref = alpha_diff * dt_inner * lap

                alpha_c_prev = np.asarray(alpha_c_field, dtype=float)
                alpha_r_prev = np.asarray(alpha_r_field, dtype=float)
                alpha_r_prev = np.minimum(np.clip(alpha_r_prev, 0.0, 1.0), np.clip(alpha_c_prev, 0.0, 1.0))

                t_scorch_local = _scorch_time_arrhenius(
                    t_field,
                    a_ind_val,
                    e_ind_val,
                    diagnostics=numerical_diagnostics,
                    step=step,
                )
                temp_local_c_before = np.asarray(t_field, dtype=float) - 273.15
                induction_rate = (1.0 / np.maximum(np.asarray(t_scorch_local, dtype=float), 1e-12)).astype(float, copy=False)
                induction_activation_factor = _induction_activation_factor(
                    temp_local_c_before,
                    induction_activation_temp_c,
                    induction_activation_ramp_c,
                )
                induction_low_penalty = _induction_low_extrapolation_penalty(
                    temp_local_c_before,
                    induction_validity_range,
                )
                induction_gate = np.clip(induction_activation_factor * induction_low_penalty, 0.0, 1.0)
                induction_rate_effective = induction_rate * induction_gate
                induction_progress_increment = dt_inner * induction_rate_effective
                step_max_induction_progress_rate = max(
                    step_max_induction_progress_rate,
                    float(np.max(np.asarray(induction_rate_effective, dtype=float))),
                )
                induction_progress_field = induction_progress_field + induction_progress_increment
                induction_progress_field = _nan_to_num_with_diagnostics(
                    induction_progress_field,
                    numerical_diagnostics,
                    "induction_progress",
                    nan=0.0,
                    posinf=1e9,
                    neginf=0.0,
                ).astype(np.float32, copy=False)
                induction_unlocked = np.asarray(induction_progress_field >= 1.0, dtype=bool)
                mean_temp_c_before = float(np.mean(temp_local_c_before))
                mean_induction_progress = float(np.mean(np.asarray(induction_progress_field, dtype=float)))
                if mean_temp_c_before < induction_activation_temp_c:
                    cure_activation_stage_final = "HEATING"
                elif mean_induction_progress < 1.0:
                    cure_activation_stage_final = "INDUCTION"
                else:
                    cure_activation_stage_final = "ACTIVE_CURE"
                cure_activation_stage_counts[cure_activation_stage_final] = (
                    cure_activation_stage_counts.get(cure_activation_stage_final, 0) + 1
                )

                # Semi-implicit damping for cure/reversion rates:
                # explicit rates are divided by a local saturation factor tied to remaining capacity.
                if use_leroy_kinetics:
                    temp_safe = np.maximum(np.asarray(t_field, dtype=float), 1.0)
                    cure_rate_now = (
                        np.maximum(leroy_av1 + (leroy_av2 * alpha_c_prev), 0.0)
                        * np.exp(np.clip(-leroy_ev / (R_GAS * temp_safe), -700.0, 700.0))
                        * np.square(np.maximum(1.0 - alpha_c_prev, 0.0))
                    )
                    alpha_unstable_prev = np.clip(
                        ((1.0 - leroy_x) * alpha_c_prev) - alpha_r_prev,
                        0.0,
                        1.0,
                    )
                    reversion_rate_now = (
                        np.maximum(leroy_ar, 0.0)
                        * np.exp(np.clip(-leroy_er / (R_GAS * temp_safe), -700.0, 700.0))
                        * alpha_unstable_prev
                    )
                else:
                    cure_rate_now = kinetics.cure_rate(T=t_field, alpha_c=alpha_c_prev)
                    reversion_rate_now = kinetics.reversion_rate(T=t_field, alpha_r=alpha_r_prev)
                cure_capacity = np.maximum(1.0 - alpha_c_prev, 1e-6)
                reversion_capacity = np.maximum(1.0 - alpha_r_prev, 1e-6)
                delta_alpha_c = (cure_rate_now * dt_inner) / (
                    1.0 + (np.maximum(cure_rate_now, 0.0) * dt_inner / cure_capacity)
                )
                delta_alpha_r = (reversion_rate_now * dt_inner) / (
                    1.0 + (np.maximum(reversion_rate_now, 0.0) * dt_inner / reversion_capacity)
                )

                alpha_c_next = np.clip(alpha_c_prev + delta_alpha_c, 0.0, 1.0)
                alpha_r_next = np.clip(alpha_r_prev + delta_alpha_r, 0.0, 1.0)

                alpha_c_next = np.where(induction_unlocked, alpha_c_next, alpha_c_prev)
                alpha_r_next = np.minimum(alpha_r_next, alpha_c_next)
                if use_leroy_kinetics:
                    alpha_r_next = np.minimum(alpha_r_next, (1.0 - leroy_x) * alpha_c_next)
                alpha_next = _clip_with_diagnostics(
                    alpha_c_next - alpha_r_next,
                    0.0,
                    1.0,
                    numerical_diagnostics,
                    "alpha_net_prestate",
                    step=step,
                )

                # Heat source uses only cure growth (d alpha_c / dt), excluding reversion.
                alpha_c_rate = np.maximum((alpha_c_next - alpha_c_prev) / max(dt_inner, 1e-12), 0.0)
                alpha_r_rate = np.maximum((alpha_r_next - alpha_r_prev) / max(dt_inner, 1e-12), 0.0)
                step_max_cure_rate = max(step_max_cure_rate, float(np.max(np.asarray(alpha_c_rate, dtype=float))))
                step_max_reversion_rate = max(
                    step_max_reversion_rate,
                    float(np.max(np.asarray(alpha_r_rate, dtype=float))),
                )
                stiffness_track["max_cure_rate"] = max(stiffness_track["max_cure_rate"], float(np.max(alpha_c_rate)))
                stiffness_track["max_reversion_rate"] = max(
                    stiffness_track["max_reversion_rate"], float(np.max(alpha_r_rate))
                )
                if bool(exotherm_enabled) and qv_val > 0.0:
                    q_source_field = reaction_heat_source(alpha_c_rate, rho=rho_val, qv=qv_val).astype(
                        np.float32, copy=False
                    )
                else:
                    q_source_field.fill(0.0)

                stiffness_track["max_heat_source"] = max(
                    stiffness_track["max_heat_source"], float(np.max(np.abs(np.asarray(q_source_field, dtype=float))))
                )

                dT_source = thermal_increment_from_source(q_source_field, dt_inner, rho=rho_val, cp=cp_val)
                step_max_dT_source = max(step_max_dT_source, float(np.max(np.abs(np.asarray(dT_source, dtype=float)))))
                step_max_dT_diff_ref = max(
                    step_max_dT_diff_ref,
                    float(np.max(np.abs(np.asarray(dT_diff_ref, dtype=float)))),
                )

                denom = float(max(np.max(np.abs(dT_diff_ref)), 1e-12))
                source_to_diffusion_ratio = float(np.max(np.abs(dT_source)) / denom)
                step_max_source_to_diffusion_ratio = max(step_max_source_to_diffusion_ratio, source_to_diffusion_ratio)
                stiffness_track["max_source_to_diffusion_ratio"] = max(
                    stiffness_track["max_source_to_diffusion_ratio"], source_to_diffusion_ratio
                )

                if use_imex_diffusion:
                    t_field_next = _imex_diffusion_step(
                        t_field,
                        dT_source,
                        alpha_diff=alpha_diff,
                        dt=dt_inner,
                        dx=dx,
                        dirichlet_axes=tuple(),
                        dirichlet_value=0.0,
                    )
                else:
                    t_field_next = np.asarray(t_field, dtype=float) + dT_diff_ref + dT_source

                t_before_conv = np.asarray(t_field_next, dtype=float)
                t_field = np.asarray(t_field_next, dtype=float)
                _apply_convective_boundaries(
                    t_field,
                    dx=dx,
                    conductivity=lambda_val,
                    h_conv=h_conv,
                    ambient_k=ambient_k,
                    axes=convective_axes,
                )
                convective_delta = np.max(np.abs(np.asarray(t_field, dtype=float) - t_before_conv))
                step_max_convective_delta_k = max(step_max_convective_delta_k, float(convective_delta))
                t_field = _nan_to_num_with_diagnostics(
                    t_field,
                    numerical_diagnostics,
                    "temperature_field",
                    nan=0.0,
                    posinf=1e6,
                    neginf=0.0,
                    step=step,
                ).astype(np.float32, copy=False)
                temp_local_c = np.asarray(t_field, dtype=float) - 273.15
                temp_seen_min_c = min(temp_seen_min_c, float(np.min(temp_local_c)))
                temp_seen_max_c = max(temp_seen_max_c, float(np.max(temp_local_c)))
                if induction_validity_range is not None:
                    induction_eval_sub = _assess_induction_extrapolation(
                        float(np.min(temp_local_c)),
                        float(np.max(temp_local_c)),
                        induction_validity_range,
                    )
                    step_max_induction_extrapolation_c = max(
                        step_max_induction_extrapolation_c,
                        float(induction_eval_sub.get("max_extrapolation_c", 0.0)),
                    )

                alpha_c_field = _clip_with_diagnostics(
                    alpha_c_next,
                    0.0,
                    1.0,
                    numerical_diagnostics,
                    "alpha_c_state",
                    step=step,
                ).astype(np.float32, copy=False)
                alpha_r_field = _clip_with_diagnostics(
                    alpha_r_next,
                    0.0,
                    1.0,
                    numerical_diagnostics,
                    "alpha_r_state",
                    step=step,
                ).astype(np.float32, copy=False)
                alpha_r_excess = int(np.count_nonzero(alpha_r_field > alpha_c_field))
                if alpha_r_excess > 0:
                    numerical_diagnostics["clip_events_count"] += alpha_r_excess
                    numerical_diagnostics["clip_breakdown"]["alpha_r_limited_by_alpha_c"] = (
                        numerical_diagnostics["clip_breakdown"].get("alpha_r_limited_by_alpha_c", 0) + alpha_r_excess
                    )
                alpha_r_field = np.minimum(alpha_r_field, alpha_c_field).astype(np.float32, copy=False)
                alpha_field = _clip_with_diagnostics(
                    alpha_next,
                    0.0,
                    1.0,
                    numerical_diagnostics,
                    "alpha_state",
                    step=step,
                ).astype(np.float32, copy=False)
                alpha_unstable_field = np.clip(
                    ((1.0 - leroy_x) * np.asarray(alpha_c_field, dtype=float)) - np.asarray(alpha_r_field, dtype=float),
                    0.0,
                    1.0,
                ).astype(np.float32, copy=False)
                process_sm.update(mean_alpha=float(np.mean(alpha_field)), t_now=t_sub)

            step_clip_events = int(numerical_diagnostics["clip_events_count"] - step_clip_start)
            step_nan_events = int(numerical_diagnostics["nan_recovery_events"] - step_nan_start)
            if step_nan_events > 0:
                _append_numerical_warning(
                    numerical_diagnostics,
                    code="nan_recovery",
                    message=f"Recovered {step_nan_events} non-finite values at step {step}.",
                    step=step,
                )
            if step_clip_events > 0 and step_max_source_to_diffusion_ratio > 8.0:
                _append_numerical_warning(
                    numerical_diagnostics,
                    code="aggressive_clipping",
                    message=(
                        f"Clipping events detected at step {step} with source/diffusion ratio "
                        f"{step_max_source_to_diffusion_ratio:.3f}."
                    ),
                    step=step,
                )

            step_score = (
                float(substeps)
                + (0.35 * float(step_clip_events))
                + (0.75 * float(step_nan_events))
                + (0.25 * float(step_max_source_to_diffusion_ratio))
                + (0.20 * float(step_max_induction_extrapolation_c))
                + (0.05 * float(step_max_convective_delta_k))
            )
            step_context = {
                "step": int(step),
                "time_s": float(t_now),
                "score": float(step_score),
                "substeps": int(substeps),
                "step_clip_events": int(step_clip_events),
                "step_nan_events": int(step_nan_events),
                "step_max_source_to_diffusion_ratio": float(step_max_source_to_diffusion_ratio),
                "step_max_cure_rate": float(step_max_cure_rate),
                "step_max_reversion_rate": float(step_max_reversion_rate),
                "step_max_dT_source": float(step_max_dT_source),
                "step_max_dT_diff_ref": float(step_max_dT_diff_ref),
                "step_max_convective_delta_k": float(step_max_convective_delta_k),
                "step_induction_progress_rate_s_inv": float(step_max_induction_progress_rate),
                "step_induction_extrapolation_c": float(step_max_induction_extrapolation_c),
                "process_state": str(process_sm.state.value),
                "cure_activation_stage": str(cure_activation_stage_final),
                "thermal_integration_mode": "imex_diffusion" if use_imex_diffusion else "explicit_split",
            }
            step_context["reason"] = _infer_critical_reason(step_context)
            critical_steps_trace = _update_critical_trace(critical_steps_trace, step_context)

            if step_score > float(stiffness_track["critical_score"]):
                stiffness_track["critical_score"] = float(step_score)
                stiffness_track["critical_step_index"] = int(step)
                stiffness_track["critical_step_time_s"] = float(t_now)
                stiffness_track["critical_reason"] = str(step_context["reason"])
                stiffness_track["critical_context"] = {
                    "process_state": str(step_context["process_state"]),
                    "cure_activation_stage": str(step_context["cure_activation_stage"]),
                    "substeps": int(step_context["substeps"]),
                    "thermal_integration_mode": str(step_context["thermal_integration_mode"]),
                }
                stiffness_track["critical_metrics"] = {
                    "score": float(step_context["score"]),
                    "step_clip_events": int(step_context["step_clip_events"]),
                    "step_nan_events": int(step_context["step_nan_events"]),
                    "step_max_source_to_diffusion_ratio": float(step_context["step_max_source_to_diffusion_ratio"]),
                    "step_max_cure_rate_s_inv": float(step_context["step_max_cure_rate"]),
                    "step_max_reversion_rate_s_inv": float(step_context["step_max_reversion_rate"]),
                    "step_max_dT_source_k": float(step_context["step_max_dT_source"]),
                    "step_max_dT_diff_ref_k": float(step_context["step_max_dT_diff_ref"]),
                    "step_max_convective_delta_k": float(step_context["step_max_convective_delta_k"]),
                    "step_induction_progress_rate_s_inv": float(step_context["step_induction_progress_rate_s_inv"]),
                    "step_induction_extrapolation_c": float(step_context["step_induction_extrapolation_c"]),
                }

        if store_pos < store_count and step == int(store_indices[store_pos]):
            times[store_pos] = t_now
            t_snaps[store_pos] = t_field[store_slices].astype(np.float32, copy=False)
            alpha_c_snaps[store_pos] = alpha_c_field[store_slices].astype(np.float32, copy=False)
            alpha_r_snaps[store_pos] = alpha_r_field[store_slices].astype(np.float32, copy=False)
            alpha_unstable_snaps[store_pos] = alpha_unstable_field[store_slices].astype(np.float32, copy=False)
            alpha_snaps[store_pos] = alpha_field[store_slices].astype(np.float32, copy=False)
            heat_source_snaps[store_pos] = q_source_field[store_slices].astype(np.float32, copy=False)
            induction_progress_snaps[store_pos] = induction_progress_field[store_slices].astype(np.float32, copy=False)
            process_state_over_time[store_pos] = process_sm.state.value
            store_pos += 1

        if step == 0 or step == steps or (step % progress_stride == 0):
            progress = 5 + int((float(step) / max(float(steps), 1.0)) * 90.0)
            _emit_progress("calculando", progress, f"Passo {step} de {steps}")

        prev_t = t_now

    stiffness_alerts = []
    if stiffness_track["max_substeps_per_step"] >= QUALITY_THRESHOLDS["substeps_max_per_step"]["warning"]:
        stiffness_alerts.append(
            f"high_substepping:max_substeps_per_step={int(stiffness_track['max_substeps_per_step'])}"
        )
    if stiffness_track["max_source_to_diffusion_ratio"] >= QUALITY_THRESHOLDS["max_source_to_diffusion_dT_ratio"]["warning"]:
        stiffness_alerts.append(
            "source_dominance:max_source_to_diffusion_ratio="
            f"{float(stiffness_track['max_source_to_diffusion_ratio']):.3f}"
        )
    if numerical_diagnostics["nan_recovery_events"] > 0:
        stiffness_alerts.append(
            f"nan_recovery:nan_recovery_events={int(numerical_diagnostics['nan_recovery_events'])}"
        )
    if numerical_diagnostics["clip_events_count"] > 0:
        stiffness_alerts.append(f"clipping:clip_events_count={int(numerical_diagnostics['clip_events_count'])}")
    if induction_info.get("induction_guarded"):
        stiffness_alerts.append("induction_guarded_by_safe_default")

    induction_eval = _assess_induction_extrapolation(
        temp_seen_min_c,
        temp_seen_max_c,
        induction_validity_range,
    )
    if induction_eval["induction_extrapolation_level"] in {"mild", "strong"}:
        severity = "warning" if induction_eval["induction_extrapolation_level"] == "mild" else "critical"
        _append_numerical_warning(
            numerical_diagnostics,
            code="induction_extrapolation",
            message=(
                "Induction extrapolation detected: "
                f"{induction_eval['induction_extrapolation_level']} "
                f"(delta={induction_eval['max_extrapolation_c']:.2f}C)."
            ),
            step=int(stiffness_track["critical_step_index"]),
            severity=severity,
        )
        stiffness_alerts.append(
            "induction_extrapolation:"
            f"level={induction_eval['induction_extrapolation_level']},delta_c={induction_eval['max_extrapolation_c']:.2f}"
        )
    elif induction_eval["induction_extrapolation_level"] == "unknown":
        _append_numerical_warning(
            numerical_diagnostics,
            code="induction_validity_unknown",
            message="No induction temperature validity range was provided by the fit payload.",
            step=0,
            severity="info",
        )

    induction_confidence_effective = _downgrade_induction_confidence(
        induction_confidence_runtime,
        induction_eval["induction_extrapolation_level"],
    )
    if str(induction_confidence_effective) != str(induction_confidence_runtime):
        _append_numerical_warning(
            numerical_diagnostics,
            code="induction_confidence_degraded",
            message=(
                "Induction confidence degraded due to extrapolation "
                f"({induction_confidence_runtime} -> {induction_confidence_effective})."
            ),
            step=int(stiffness_track["critical_step_index"]),
            severity="warning",
        )
        induction_confidence_runtime = str(induction_confidence_effective)

    quality_payload = _build_quality_payload(
        clip_events_count=int(numerical_diagnostics["clip_events_count"]),
        nan_recovery_events=int(numerical_diagnostics["nan_recovery_events"]),
        max_source_to_diffusion_ratio=float(stiffness_track["max_source_to_diffusion_ratio"]),
        substeps_max_per_step=int(stiffness_track["max_substeps_per_step"]),
        stiffness_alert_count=int(len(stiffness_alerts)),
        induction_extrapolation_level=str(induction_eval["induction_extrapolation_level"]),
        induction_extrapolation_c=float(induction_eval["max_extrapolation_c"]),
    )
    alpha_mean_final = None
    if len(alpha_snaps):
        alpha_mean_final = float(np.mean(np.asarray(alpha_snaps[-1], dtype=float)))
    degraded_mode = _resolve_degraded_mode(
        induction_extrapolation_level=induction_eval["induction_extrapolation_level"],
        induction_confidence=induction_confidence_runtime,
        process_state_final=process_sm.state.value,
        alpha_mean_final=alpha_mean_final,
    )
    if degraded_mode != "none":
        degraded_severity = "critical" if degraded_mode == "blocked_prediction" else "warning"
        quality_payload["quality_flags"].append(
            {
                "code": "degraded_mode",
                "severity": degraded_severity,
                "value": 1.0,
                "thresholds": {"info": 0.0, "warning": 0.0, "critical": 1.0},
                "message": f"Degraded mode active: {degraded_mode}.",
            }
        )
        severity_breakdown = quality_payload.get("quality_severity_breakdown") or {}
        for key in ("info", "warning", "critical"):
            severity_breakdown.setdefault(key, 0)
        severity_breakdown[degraded_severity] += 1
        quality_payload["quality_severity_breakdown"] = severity_breakdown
        if severity_breakdown["critical"] > 0:
            quality_payload["quality_status"] = "critical"
        elif severity_breakdown["warning"] > 0:
            quality_payload["quality_status"] = "warning"
        elif severity_breakdown["info"] > 0:
            quality_payload["quality_status"] = "info"
        else:
            quality_payload["quality_status"] = "healthy"
    diagnostics_preview = extract_simulation_diagnostics(
        {
            "engine": "thermo_kinetic_v2",
            "times": times,
            "t_snaps": t_snaps,
            "alpha_snaps": alpha_snaps,
            "process_state_final": process_sm.state.value,
            "induction_confidence": induction_confidence_runtime,
            "induction_extrapolation_warning": bool(induction_eval["induction_extrapolation_warning"]),
            "degraded_mode": degraded_mode,
            "quality_status": quality_payload["quality_status"],
            "metrics": {
                "induction_extrapolation_level": str(induction_eval["induction_extrapolation_level"]),
            },
        },
        engine="thermo_kinetic_v2",
    )
    prediction_validity = str(diagnostics_preview.get("prediction_validity") or "low_confidence_exploratory")
    prediction_validity_reason = str(diagnostics_preview.get("prediction_validity_reason") or "not_evaluated")
    reliability_note = str(diagnostics_preview.get("reliability_note") or "")
    if degraded_mode != "none":
        _append_numerical_warning(
            numerical_diagnostics,
            code="degraded_mode",
            message=(
                f"Degraded mode active ({degraded_mode}) due to induction validity limits; "
                f"prediction_validity={prediction_validity}."
            ),
            step=int(stiffness_track["critical_step_index"]),
            severity="warning" if degraded_mode != "blocked_prediction" else "critical",
        )

    metrics_payload = {
        "kinetic_model_family": model_family,
        "leroy_state_fields": bool(use_leroy_kinetics),
        "leroy_x_stable": float(leroy_x) if use_leroy_kinetics else None,
        "thermal_integration_mode": "imex_diffusion" if use_imex_diffusion else "explicit_split",
        "substeps_total": int(stiffness_track["total_substeps"]),
        "substeps_max_per_step": int(stiffness_track["max_substeps_per_step"]),
        "substeps_mean_per_step": float(stiffness_track["total_substeps"] / max(steps, 1)),
        "max_cure_rate_s_inv": float(stiffness_track["max_cure_rate"]),
        "max_reversion_rate_s_inv": float(stiffness_track["max_reversion_rate"]),
        "max_heat_source_w_m3": float(stiffness_track["max_heat_source"]),
        "max_source_to_diffusion_dT_ratio": float(stiffness_track["max_source_to_diffusion_ratio"]),
        "clip_events_count": int(numerical_diagnostics["clip_events_count"]),
        "nan_recovery_events": int(numerical_diagnostics["nan_recovery_events"]),
        "clip_event_breakdown": dict(numerical_diagnostics["clip_breakdown"]),
        "nan_recovery_breakdown": dict(numerical_diagnostics["nan_breakdown"]),
        "numerical_warning_count": int(len(numerical_diagnostics["numerical_warnings"])),
        "step_count": int(steps),
        "critical_step_index": int(stiffness_track["critical_step_index"]),
        "critical_step_time_s": float(stiffness_track["critical_step_time_s"]),
        "critical_step_reason": str(stiffness_track["critical_reason"]),
        "critical_step_context": dict(stiffness_track.get("critical_context") or {}),
        "critical_step_metrics": dict(stiffness_track.get("critical_metrics") or {}),
        "critical_steps_trace": list(critical_steps_trace),
        "stiffness_alerts": list(stiffness_alerts),
        "stiffness_alert_count": int(len(stiffness_alerts)),
        "induction_confidence": induction_confidence_runtime,
        "induction_source": induction_info.get("induction_source"),
        "induction_fit_quality": induction_info.get("induction_fit_quality"),
        "induction_model_regime": induction_model_regime,
        "temperature_validity_range": dict(induction_validity_range or {}),
        "induction_extrapolation_warning": bool(induction_eval["induction_extrapolation_warning"]),
        "induction_extrapolation_level": str(induction_eval["induction_extrapolation_level"]),
        "induction_extrapolation_delta_c": float(induction_eval["max_extrapolation_c"]),
        "induction_extrapolation_outside_low_c": float(induction_eval["outside_low_c"]),
        "induction_extrapolation_outside_high_c": float(induction_eval["outside_high_c"]),
        "temperature_min_seen_c": float(temp_seen_min_c),
        "temperature_max_seen_c": float(temp_seen_max_c),
        "induction_reference_temp_c": induction_info.get("reference_temp_c"),
        "induction_reference_t_scorch_s": induction_info.get("reference_t_scorch_s"),
        "induction_activation_temp_c": float(induction_activation_temp_c),
        "induction_activation_ramp_c": float(induction_activation_ramp_c),
        "cure_activation_stage_final": str(cure_activation_stage_final),
        "cure_activation_stage_counts": dict(cure_activation_stage_counts),
        "h_mold_w_m2k": float(process_config.h_mold),
        "h_air_w_m2k": float(process_config.h_air),
        "quality_flags": list(quality_payload["quality_flags"]),
        "quality_status": str(quality_payload["quality_status"]),
        "quality_severity_breakdown": dict(quality_payload["quality_severity_breakdown"]),
        "prediction_validity": prediction_validity,
        "prediction_validity_reason": prediction_validity_reason,
        "degraded_mode": degraded_mode,
        "reliability_note": reliability_note,
    }

    _check_cancel()
    _emit_progress("salvando", 97, "Salvando resultados da simulacao v2...")
    sim_id, out_path = save_simulation_v2(
        fit_id=fit_payload.get("fit_id", "fit_unknown"),
        mode=mode,
        dim=dim,
        stored_shape=plan["stored_shape"],
        full_shape=shape,
        platen_axis=selected_platen_axis,
        heated_axes=heated_axes,
        store_stride=store_stride,
        snapshot_every_source=max(int(snapshot_every), 1),
        snapshot_every_store=int(plan["snapshot_every_store"]),
        dx=dx,
        dt=dt,
        t_end=t_end,
        times=times,
        t_snaps=t_snaps,
        alpha_c_snaps=alpha_c_snaps,
        alpha_r_snaps=alpha_r_snaps,
        alpha_unstable_snaps=alpha_unstable_snaps,
        alpha_snaps=alpha_snaps,
        heat_source_snaps=heat_source_snaps,
        induction_progress_snaps=induction_progress_snaps,
        process_state_over_time=process_state_over_time,
        process_state_final=process_sm.state.value,
        demold_time=process_sm.demold_time,
        rho=rho_val,
        cp=cp_val,
        lambda_rubber=lambda_val,
        qv=qv_val,
        A_ind=a_ind_val,
        E_ind=e_ind_val,
        exotherm_enabled=bool(exotherm_enabled),
        model_family=model_family,
        metrics=metrics_payload,
        numerical_warnings=numerical_diagnostics["numerical_warnings"],
        induction_confidence=induction_confidence_runtime,
        induction_source=induction_info.get("induction_source"),
        induction_fit_quality=induction_info.get("induction_fit_quality"),
        temperature_validity_range=induction_validity_range,
        induction_extrapolation_warning=bool(induction_eval["induction_extrapolation_warning"]),
        induction_model_regime=induction_model_regime,
        quality_status=quality_payload["quality_status"],
        prediction_validity=prediction_validity,
        prediction_validity_reason=prediction_validity_reason,
        degraded_mode=degraded_mode,
        reliability_note=reliability_note,
        kinetics_params=asdict(kinetics.params),
    )
    _emit_progress("concluido", 100, "Simulacao v2 concluida.")
    return sim_id, out_path


def load_simulation_v2(sim_id):
    path = _build_simulation_v2_path(sim_id)
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=True)
    files = set(d.files)

    platen_axis = int(d["platen_axis"]) if "platen_axis" in files else 0
    heated_axes = tuple(int(x) for x in d["heated_axes"]) if "heated_axes" in files else tuple(range(int(d["dim"])))
    full_shape = tuple(int(x) for x in d["full_shape"]) if "full_shape" in files else tuple(int(x) for x in d["shape"])
    store_stride = int(d["store_stride"]) if "store_stride" in files else 1
    snapshot_every_source = int(d["snapshot_every_source"]) if "snapshot_every_source" in files else 20
    snapshot_every_store = int(d["snapshot_every_store"]) if "snapshot_every_store" in files else snapshot_every_source
    model_family = str(d["model_family"]) if "model_family" in files else ""

    alpha_snaps = d["alpha_snaps"]
    alpha_c_snaps = d["alpha_c_snaps"] if "alpha_c_snaps" in files else alpha_snaps
    alpha_r_snaps = d["alpha_r_snaps"] if "alpha_r_snaps" in files else np.zeros_like(alpha_snaps)
    alpha_unstable_snaps = (
        d["alpha_unstable_snaps"] if "alpha_unstable_snaps" in files else np.clip(alpha_c_snaps - alpha_snaps, 0.0, 1.0)
    )
    heat_source_snaps = d["heat_source_snaps"] if "heat_source_snaps" in files else np.zeros_like(alpha_snaps)
    induction_progress_snaps = (
        d["induction_progress_snaps"] if "induction_progress_snaps" in files else np.zeros_like(alpha_snaps)
    )
    process_state_over_time = (
        np.asarray(d["process_state_over_time"], dtype=str)
        if "process_state_over_time" in files
        else np.full((len(d["times"]),), ProcessState.HEATING.value, dtype="<U16")
    )
    process_state_final = (
        str(d["process_state_final"])
        if "process_state_final" in files
        else str(process_state_over_time[-1] if len(process_state_over_time) else ProcessState.HEATING.value)
    )
    demold_time_raw = float(d["demold_time"]) if "demold_time" in files else float("nan")
    demold_time = None if np.isnan(demold_time_raw) else demold_time_raw

    kinetics_params = None
    if "kinetics_params" in files:
        raw = d["kinetics_params"]
        if isinstance(raw, np.ndarray) and raw.shape == ():
            try:
                kinetics_params = dict(raw.item())
            except Exception:
                kinetics_params = None

    metrics = {}
    if "metrics" in files:
        raw = d["metrics"]
        if isinstance(raw, np.ndarray) and raw.shape == ():
            try:
                metrics = dict(raw.item())
            except Exception:
                metrics = {}

    numerical_warnings = []
    if "numerical_warnings" in files:
        raw_warnings = d["numerical_warnings"]
        if isinstance(raw_warnings, np.ndarray):
            numerical_warnings = [w for w in raw_warnings.tolist() if w is not None]
        else:
            numerical_warnings = _to_list_of_strings(raw_warnings)

    induction_confidence = (
        str(d["induction_confidence"])
        if "induction_confidence" in files
        else str(metrics.get("induction_confidence") or "low")
    )
    induction_source = (
        str(d["induction_source"])
        if "induction_source" in files
        else str(metrics.get("induction_source") or "safe_default")
    )
    induction_fit_quality = (
        str(d["induction_fit_quality"])
        if "induction_fit_quality" in files
        else str(metrics.get("induction_fit_quality") or "not_calibrated")
    )
    temperature_validity_range = {}
    if "temperature_validity_range" in files:
        raw = d["temperature_validity_range"]
        if isinstance(raw, np.ndarray) and raw.shape == ():
            try:
                temperature_validity_range = dict(raw.item() or {})
            except Exception:
                temperature_validity_range = {}
        elif isinstance(raw, dict):
            temperature_validity_range = dict(raw)
    elif isinstance(metrics.get("temperature_validity_range"), dict):
        temperature_validity_range = dict(metrics.get("temperature_validity_range") or {})

    induction_extrapolation_warning = (
        bool(d["induction_extrapolation_warning"])
        if "induction_extrapolation_warning" in files
        else bool(metrics.get("induction_extrapolation_warning", False))
    )
    induction_model_regime = (
        str(d["induction_model_regime"])
        if "induction_model_regime" in files
        else str(metrics.get("induction_model_regime") or "single_arrhenius")
    )
    quality_status = (
        str(d["quality_status"])
        if "quality_status" in files
        else str(metrics.get("quality_status") or "healthy")
    )
    prediction_validity = (
        str(d["prediction_validity"])
        if "prediction_validity" in files
        else str(metrics.get("prediction_validity") or "low_confidence_exploratory")
    )
    prediction_validity_reason = (
        str(d["prediction_validity_reason"])
        if "prediction_validity_reason" in files
        else str(metrics.get("prediction_validity_reason") or "not_evaluated")
    )
    degraded_mode = (
        str(d["degraded_mode"])
        if "degraded_mode" in files
        else str(metrics.get("degraded_mode") or "none")
    )
    reliability_note = (
        str(d["reliability_note"])
        if "reliability_note" in files
        else str(metrics.get("reliability_note") or "")
    )
    clip_events_count = int(metrics.get("clip_events_count", 0) or 0)
    nan_recovery_events = int(metrics.get("nan_recovery_events", 0) or 0)

    metrics.setdefault("temperature_validity_range", dict(temperature_validity_range))
    metrics.setdefault("induction_extrapolation_warning", bool(induction_extrapolation_warning))
    metrics.setdefault("induction_model_regime", str(induction_model_regime))
    metrics.setdefault("quality_status", str(quality_status))
    metrics.setdefault("prediction_validity", str(prediction_validity))
    metrics.setdefault("prediction_validity_reason", str(prediction_validity_reason))
    metrics.setdefault("degraded_mode", str(degraded_mode))
    metrics.setdefault("reliability_note", str(reliability_note))

    return {
        "sim_id": str(d["sim_id"]),
        "solver_version": int(d["solver_version"]) if "solver_version" in files else 2,
        "fit_id": str(d["fit_id"]),
        "mode": str(d["mode"]),
        "dim": int(d["dim"]),
        "shape": tuple(int(x) for x in d["shape"]),
        "full_shape": full_shape,
        "platen_axis": platen_axis,
        "heated_axes": heated_axes,
        "store_stride": store_stride,
        "snapshot_every_source": snapshot_every_source,
        "snapshot_every_store": snapshot_every_store,
        "model_family": model_family,
        "dx": float(d["dx"]) * float(store_stride),
        "dx_compute": float(d["dx"]),
        "dt": float(d["dt"]),
        "t_end": float(d["t_end"]),
        "times": d["times"],
        "t_snaps": d["t_snaps"],
        "alpha_c_snaps": alpha_c_snaps,
        "alpha_r_snaps": alpha_r_snaps,
        "alpha_unstable_snaps": alpha_unstable_snaps,
        "alpha_snaps": alpha_snaps,
        "heat_source_snaps": heat_source_snaps,
        "induction_progress_snaps": induction_progress_snaps,
        "process_state_over_time": process_state_over_time,
        "process_state_final": process_state_final,
        "demold_time": demold_time,
        "rho": float(d["rho"]) if "rho" in files else RHO_RUBBER,
        "cp": float(d["cp"]) if "cp" in files else CP_RUBBER,
        "lambda_rubber": float(d["lambda_rubber"]) if "lambda_rubber" in files else LAMBDA_RUBBER,
        "qv": float(d["qv"]) if "qv" in files else QV_VULCANIZATION,
        "A_ind": float(d["A_ind"]) if "A_ind" in files else DEFAULT_A_IND,
        "E_ind": float(d["E_ind"]) if "E_ind" in files else DEFAULT_E_IND,
        "exotherm_enabled": bool(d["exotherm_enabled"]) if "exotherm_enabled" in files else True,
        "kinetics_params": kinetics_params,
        "metrics": metrics,
        "numerical_warnings": list(numerical_warnings),
        "clip_events_count": clip_events_count,
        "nan_recovery_events": nan_recovery_events,
        "induction_confidence": induction_confidence,
        "induction_source": induction_source,
        "induction_fit_quality": induction_fit_quality,
        "temperature_validity_range": dict(temperature_validity_range),
        "induction_extrapolation_warning": bool(induction_extrapolation_warning),
        "induction_model_regime": str(induction_model_regime),
        "quality_status": str(quality_status),
        "prediction_validity": str(prediction_validity),
        "prediction_validity_reason": str(prediction_validity_reason),
        "degraded_mode": str(degraded_mode),
        "reliability_note": str(reliability_note),
    }


def load_simulation_v2_normalized(sim_id):
    from services.engine_registry import ENGINE_THERMO_KINETIC_V2
    from services.simulation_schema import normalize_simulation_output

    sim = load_simulation_v2(sim_id)
    if sim is None:
        return None
    return normalize_simulation_output(sim, engine=ENGINE_THERMO_KINETIC_V2)


def run_simulation(*args, **kwargs):
    return run_simulation_v2(*args, **kwargs)


def load_simulation(sim_id):
    return load_simulation_v2(sim_id)
