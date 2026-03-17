import numpy as np

from services.engine_registry import get_engine_status, normalize_engine


SIMULATION_SCHEMA_VERSION = "simulation_output_v1"

REQUIRED_FIELDS = (
    "schema_version",
    "engine",
    "engine_status",
    "geometry_type",
    "coordinates",
    "times",
    "temperature_snapshots",
    "alpha_c",
    "alpha_r",
    "alpha",
    "heat_source",
    "metrics",
    "demold_time",
    "process_state",
    "process_state_over_time",
    "process_state_final",
)

OPTIONAL_FIELDS = (
    "sim_id",
    "fit_id",
    "mode",
    "dim",
    "shape",
    "full_shape",
    "dx",
    "dx_compute",
    "dt",
    "t_end",
    "geometry_metadata",
    "induction_confidence",
    "induction_source",
    "induction_fit_quality",
    "temperature_validity_range",
    "induction_extrapolation_warning",
    "induction_model_regime",
    "quality_status",
    "clip_events_count",
    "nan_recovery_events",
    "numerical_warnings",
    "alpha_unstable_snaps",
)


def _to_numpy(value, dtype):
    if value is None:
        return np.asarray([], dtype=dtype)
    return np.asarray(value, dtype=dtype)


def _to_int_tuple(value):
    if value is None:
        return tuple()
    if isinstance(value, (list, tuple, np.ndarray)):
        arr = np.asarray(value).reshape(-1)
        return tuple(int(x) for x in arr.tolist())
    try:
        return (int(value),)
    except (TypeError, ValueError):
        return tuple()


def _safe_float(value, default=None):
    try:
        f_value = float(value)
    except (TypeError, ValueError):
        return default
    if np.isnan(f_value):
        return default
    return f_value


def _coerce_demold_time(value):
    demold = _safe_float(value, default=None)
    if demold is None:
        return None
    return max(demold, 0.0)


def _infer_geometry_type(payload):
    raw_geometry = str(payload.get("geometry") or payload.get("solver_family") or "").lower()
    if "axisymmetric" in raw_geometry or "cylindrical" in raw_geometry:
        return "axisymmetric_cylindrical_section"

    dim = int(_safe_float(payload.get("dim"), default=0) or 0)
    if dim == 1:
        return "cartesian_1d"
    if dim == 2:
        return "cartesian_2d"
    if dim == 3:
        return "cartesian_3d"
    return "unknown"


def _build_coordinates_metadata(payload, geometry_type):
    if geometry_type == "axisymmetric_cylindrical_section":
        mesh = dict(payload.get("mesh") or {})
        return {
            "system": "cylindrical_rz",
            "axes": [
                {
                    "name": "r",
                    "size": int(_safe_float(mesh.get("nr"), default=0) or 0),
                    "spacing_m": _safe_float(mesh.get("dr"), default=None),
                },
                {
                    "name": "z",
                    "size": int(_safe_float(mesh.get("nz"), default=0) or 0),
                    "spacing_m": _safe_float(mesh.get("dz"), default=None),
                },
            ],
        }

    dim = int(_safe_float(payload.get("dim"), default=0) or 0)
    full_shape = _to_int_tuple(payload.get("full_shape")) or _to_int_tuple(payload.get("shape"))
    if dim <= 0:
        dim = len(full_shape)
    axis_names = ("x", "y", "z")[: max(dim, 0)]
    spacing = _safe_float(payload.get("dx_compute"), default=_safe_float(payload.get("dx"), default=None))

    axes = []
    for idx, axis_name in enumerate(axis_names):
        axis_size = full_shape[idx] if idx < len(full_shape) else None
        axes.append({"name": axis_name, "size": axis_size, "spacing_m": spacing})
    return {"system": "cartesian", "axes": axes}


def _build_geometry_metadata(payload, geometry_type):
    heated_axes_raw = payload.get("heated_axes")
    if heated_axes_raw is None:
        heated_axes = tuple()
    else:
        heated_axes = tuple(int(x) for x in np.asarray(heated_axes_raw).reshape(-1).tolist())

    return {
        "geometry_type": geometry_type,
        "mode": payload.get("mode"),
        "dim": int(_safe_float(payload.get("dim"), default=0) or 0),
        "shape": _to_int_tuple(payload.get("shape")),
        "full_shape": _to_int_tuple(payload.get("full_shape")) or _to_int_tuple(payload.get("shape")),
        "platen_axis": payload.get("platen_axis"),
        "heated_axes": heated_axes,
        "store_stride": int(_safe_float(payload.get("store_stride"), default=1) or 1),
        "snapshot_every_source": int(_safe_float(payload.get("snapshot_every_source"), default=0) or 0),
        "snapshot_every_store": int(_safe_float(payload.get("snapshot_every_store"), default=0) or 0),
        "mesh": dict(payload.get("mesh") or {}),
    }


def _compute_basic_metrics(t_snaps, alpha_snaps, *, times, demold_time):
    if t_snaps.size == 0 or alpha_snaps.size == 0:
        return {
            "temperature_max_c": None,
            "temperature_mean_c": None,
            "alpha_min": None,
            "alpha_mean": None,
            "alpha_max": None,
            "delta_alpha": None,
            "demold_time_s": demold_time,
            "final_time_s": _safe_float(times[-1], default=None) if len(times) else None,
            "snapshot_count": int(len(times)),
        }

    t_final = np.asarray(t_snaps[-1], dtype=float)
    alpha_final = np.asarray(alpha_snaps[-1], dtype=float)
    return {
        "temperature_max_c": float(np.max(t_final) - 273.15),
        "temperature_mean_c": float(np.mean(t_final) - 273.15),
        "alpha_min": float(np.min(alpha_final)),
        "alpha_mean": float(np.mean(alpha_final)),
        "alpha_max": float(np.max(alpha_final)),
        "delta_alpha": float(np.max(alpha_final) - np.min(alpha_final)),
        "demold_time_s": demold_time,
        "final_time_s": _safe_float(times[-1], default=None) if len(times) else None,
        "snapshot_count": int(len(times)),
    }


def normalize_simulation_output(raw_payload, *, engine):
    payload = dict(raw_payload or {})
    normalized = dict(payload)

    engine_key = normalize_engine(engine)
    engine_status = get_engine_status(engine_key)

    times = _to_numpy(payload.get("times"), np.float64)
    t_snaps = _to_numpy(payload.get("t_snaps"), np.float32)

    alpha_snaps = _to_numpy(payload.get("alpha_snaps"), np.float32)
    alpha_c_snaps = _to_numpy(payload.get("alpha_c_snaps"), np.float32)
    alpha_r_snaps = _to_numpy(payload.get("alpha_r_snaps"), np.float32)
    alpha_unstable_snaps = _to_numpy(payload.get("alpha_unstable_snaps"), np.float32)

    if alpha_snaps.size == 0 and alpha_c_snaps.size > 0:
        if alpha_r_snaps.size == 0:
            alpha_r_snaps = np.zeros_like(alpha_c_snaps, dtype=np.float32)
        alpha_snaps = np.clip(alpha_c_snaps - alpha_r_snaps, 0.0, 1.0).astype(np.float32, copy=False)

    if alpha_c_snaps.size == 0:
        alpha_c_snaps = np.asarray(alpha_snaps, dtype=np.float32)
    if alpha_r_snaps.size == 0:
        alpha_r_snaps = np.zeros_like(alpha_c_snaps, dtype=np.float32)
    if alpha_snaps.size == 0:
        alpha_snaps = np.clip(alpha_c_snaps - alpha_r_snaps, 0.0, 1.0).astype(np.float32, copy=False)
    if alpha_unstable_snaps.size == 0:
        alpha_unstable_snaps = np.clip(alpha_c_snaps - alpha_snaps, 0.0, 1.0).astype(np.float32, copy=False)

    heat_source_snaps = _to_numpy(payload.get("heat_source_snaps", payload.get("q_source_snaps")), np.float32)
    if heat_source_snaps.size == 0:
        heat_source_snaps = np.zeros_like(alpha_snaps, dtype=np.float32)

    process_state_over_time_raw = payload.get("process_state_over_time")
    if process_state_over_time_raw is None:
        process_state_over_time = np.full((len(times),), "HEATING", dtype="<U16")
    else:
        process_state_over_time = np.asarray(process_state_over_time_raw, dtype="<U16").reshape(-1)
        if len(times) and len(process_state_over_time) != len(times):
            if len(process_state_over_time) < len(times):
                pad_count = len(times) - len(process_state_over_time)
                pad_value = process_state_over_time[-1] if len(process_state_over_time) else "HEATING"
                process_state_over_time = np.concatenate(
                    [process_state_over_time, np.full((pad_count,), str(pad_value), dtype="<U16")]
                )
            else:
                process_state_over_time = process_state_over_time[: len(times)]

    process_state_final = str(
        payload.get("process_state_final")
        or payload.get("process_state")
        or (process_state_over_time[-1] if len(process_state_over_time) else "HEATING")
    )
    demold_time = _coerce_demold_time(payload.get("demold_time"))

    geometry_type = _infer_geometry_type(payload)
    coordinates = _build_coordinates_metadata(payload, geometry_type)
    geometry_metadata = _build_geometry_metadata(payload, geometry_type)
    metrics = dict(payload.get("metrics") or {})
    for metric_key, metric_value in _compute_basic_metrics(
        t_snaps=t_snaps,
        alpha_snaps=alpha_snaps,
        times=times,
        demold_time=demold_time,
    ).items():
        metrics.setdefault(metric_key, metric_value)

    clip_events_count = int(
        _safe_float(payload.get("clip_events_count"), default=_safe_float(metrics.get("clip_events_count"), default=0.0))
        or 0
    )
    nan_recovery_events = int(
        _safe_float(
            payload.get("nan_recovery_events"),
            default=_safe_float(metrics.get("nan_recovery_events"), default=0.0),
        )
        or 0
    )
    numerical_warnings_raw = payload.get("numerical_warnings", metrics.get("numerical_warnings", []))
    if isinstance(numerical_warnings_raw, (list, tuple, np.ndarray)):
        numerical_warnings = [item for item in list(numerical_warnings_raw)]
    elif numerical_warnings_raw is None:
        numerical_warnings = []
    else:
        numerical_warnings = [numerical_warnings_raw]

    induction_confidence = str(
        payload.get("induction_confidence")
        or metrics.get("induction_confidence")
        or "low"
    )
    induction_source = str(
        payload.get("induction_source")
        or metrics.get("induction_source")
        or "safe_default"
    )
    induction_fit_quality = str(
        payload.get("induction_fit_quality")
        or metrics.get("induction_fit_quality")
        or "not_calibrated"
    )
    temperature_validity_range_raw = payload.get("temperature_validity_range", metrics.get("temperature_validity_range"))
    if isinstance(temperature_validity_range_raw, dict):
        temperature_validity_range = dict(temperature_validity_range_raw)
    else:
        temperature_validity_range = {}
    induction_extrapolation_warning = bool(
        payload.get("induction_extrapolation_warning", metrics.get("induction_extrapolation_warning", False))
    )
    induction_model_regime = str(
        payload.get("induction_model_regime")
        or metrics.get("induction_model_regime")
        or "single_arrhenius"
    )
    quality_status = str(
        payload.get("quality_status")
        or metrics.get("quality_status")
        or "healthy"
    )

    metrics.setdefault("clip_events_count", clip_events_count)
    metrics.setdefault("nan_recovery_events", nan_recovery_events)
    metrics.setdefault("numerical_warning_count", int(len(numerical_warnings)))
    metrics.setdefault("numerical_warnings", list(numerical_warnings))
    metrics.setdefault("induction_confidence", induction_confidence)
    metrics.setdefault("induction_source", induction_source)
    metrics.setdefault("induction_fit_quality", induction_fit_quality)
    metrics.setdefault("temperature_validity_range", dict(temperature_validity_range))
    metrics.setdefault("induction_extrapolation_warning", bool(induction_extrapolation_warning))
    metrics.setdefault("induction_model_regime", induction_model_regime)
    metrics.setdefault("quality_status", quality_status)

    shape = _to_int_tuple(payload.get("shape"))
    full_shape = _to_int_tuple(payload.get("full_shape")) or shape
    dim = int(_safe_float(payload.get("dim"), default=len(full_shape) or 0) or 0)

    normalized.update(
        {
            "schema_version": SIMULATION_SCHEMA_VERSION,
            "engine": engine_key,
            "engine_status": engine_status,
            "geometry_type": geometry_type,
            "coordinates": coordinates,
            "geometry_metadata": geometry_metadata,
            "dim": dim,
            "shape": shape,
            "full_shape": full_shape,
            "times": times,
            "t_snaps": t_snaps,
            "temperature_snapshots": t_snaps,
            "alpha_c_snaps": alpha_c_snaps,
            "alpha_r_snaps": alpha_r_snaps,
            "alpha_unstable_snaps": alpha_unstable_snaps,
            "alpha_snaps": alpha_snaps,
            "heat_source_snaps": heat_source_snaps,
            "alpha_c": alpha_c_snaps,
            "alpha_r": alpha_r_snaps,
            "alpha_unstable": alpha_unstable_snaps,
            "alpha": alpha_snaps,
            "heat_source": heat_source_snaps,
            "metrics": metrics,
            "demold_time": demold_time,
            "process_state": process_state_final,
            "process_state_over_time": process_state_over_time,
            "process_state_final": process_state_final,
            "induction_confidence": induction_confidence,
            "induction_source": induction_source,
            "induction_fit_quality": induction_fit_quality,
            "temperature_validity_range": dict(temperature_validity_range),
            "induction_extrapolation_warning": bool(induction_extrapolation_warning),
            "induction_model_regime": induction_model_regime,
            "quality_status": quality_status,
            "clip_events_count": clip_events_count,
            "nan_recovery_events": nan_recovery_events,
            "numerical_warnings": list(numerical_warnings),
        }
    )
    return normalized


def get_schema_contract():
    return {
        "schema_version": SIMULATION_SCHEMA_VERSION,
        "required_fields": list(REQUIRED_FIELDS),
        "optional_fields": list(OPTIONAL_FIELDS),
    }
