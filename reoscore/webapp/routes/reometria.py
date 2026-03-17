import json
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from services.kinetics_service import (
    build_alpha_time_comparison,
    get_preview_curve,
    list_ensaios_for_fit,
    load_fit_payload,
    run_fit,
    save_fit_payload,
    update_fit_parameters,
)
from services.kinetic_model_service import (
    DEFAULT_MODEL_FAMILY as DEFAULT_KINETIC_MODEL_FAMILY,
    DEFAULT_REFERENCE_TEMPERATURE_K,
    DEFAULT_CURE_COMPLETION_RULE,
    SUPPORTED_MODEL_FAMILIES,
    SUPPORTED_CURE_COMPLETION_RULES,
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
)
from services.engine_registry import (
    ENGINE_AXISYMMETRIC_FIPY as ENGINE_AXISYMMETRIC_FIPY_REGISTRY,
    ENGINE_EMPIRICAL_V1 as ENGINE_EMPIRICAL_V1_REGISTRY,
    ENGINE_THERMO_KINETIC_V2 as ENGINE_THERMO_KINETIC_V2_REGISTRY,
    get_engine_status,
    list_engine_catalog,
    normalize_engine as normalize_engine_registry,
    resolve_engine_for_execution,
)
from services.simulation_schema import normalize_simulation_output
from services.vulcanization_service import (
    load_simulation as load_simulation_v1,
    load_simulation_normalized as load_simulation_v1_normalized,
    run_simulation as run_simulation_v1,
)
from services.vulcanization_service_v2 import (
    load_simulation_v2,
    load_simulation_v2_normalized,
    run_simulation_v2,
)

from reoscore.webapp.utils import parse_float_locale, parse_int_locale

reometria_bp = Blueprint("reometria", __name__)
_SIM_JOBS = {}
_SIM_JOBS_LOCK = threading.Lock()
_SIM_JOB_TTL_SEC = 6 * 3600
ENGINE_EMPIRICAL_V1 = ENGINE_EMPIRICAL_V1_REGISTRY
ENGINE_THERMO_KINETIC_V2 = ENGINE_THERMO_KINETIC_V2_REGISTRY
ENGINE_AXISYMMETRIC_FIPY = ENGINE_AXISYMMETRIC_FIPY_REGISTRY
ENGINE_OPTIONS = (
    ENGINE_EMPIRICAL_V1,
    ENGINE_THERMO_KINETIC_V2,
    ENGINE_AXISYMMETRIC_FIPY,
)
_REOMETRIA_OUT_DIR = Path("data/out")


def _safe_float(value, default=None):
    if isinstance(value, np.ndarray):
        if value.size != 1:
            return default
        value = value.reshape(-1)[0]
    elif isinstance(value, (list, tuple)):
        if len(value) != 1:
            return default
        value = value[0]

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    finite_flag = np.isfinite(numeric)
    if not bool(finite_flag):
        return default
    return float(numeric)


def _round_two_decimals(value):
    numeric = _safe_float(value, default=None)
    if numeric is None:
        return None
    try:
        return float(Decimal(str(numeric)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return float(numeric)


def _safe_datetime_from_iso(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_date_from_yyyy_mm_dd(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_sim_id_from_filename(filename):
    name = str(filename or "")
    if not name.endswith(".npz"):
        return None, None
    stem = name[:-4]
    if stem.startswith("sim_v2_"):
        return stem[len("sim_v2_") :], ENGINE_THERMO_KINETIC_V2
    if stem.startswith("sim_"):
        return stem[len("sim_") :], ENGINE_EMPIRICAL_V1
    return None, None


def _timestamp_from_sim_id(sim_id):
    token = str(sim_id or "").split("_", 1)[0]
    if len(token) != 14 or not token.isdigit():
        return None
    try:
        dt = datetime.strptime(token, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return float(dt.timestamp())


def _timestamp_from_created_at(value):
    numeric = _safe_float(value, default=None)
    if numeric is not None:
        return numeric

    dt = _safe_datetime_from_iso(value)
    if dt is None:
        return None
    return float(dt.timestamp())


def _to_created_at_display(created_at_raw, sort_ts):
    if created_at_raw:
        return str(created_at_raw).replace("T", " ")
    if sort_ts is None:
        return "--"
    return datetime.fromtimestamp(float(sort_ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _extract_mold_temp_c(sim_payload):
    payload = sim_payload or {}
    for key in ("mold_temp_c", "mold_temperature_c", "temp_molde_c"):
        numeric = _safe_float(payload.get(key), default=None)
        if numeric is not None:
            return numeric

    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    for key in ("mold_temp_c", "temperature_max_c"):
        numeric = _safe_float(metrics.get(key), default=None)
        if numeric is not None:
            return numeric
    return None


def _load_simulation_normalized_with_fallback(sim_id, engine_hint=None):
    engine_hint = _normalize_engine(engine_hint)
    if engine_hint == ENGINE_THERMO_KINETIC_V2:
        load_sequence = (
            (ENGINE_THERMO_KINETIC_V2, load_simulation_v2_normalized),
            (ENGINE_EMPIRICAL_V1, load_simulation_v1_normalized),
        )
    else:
        load_sequence = (
            (ENGINE_EMPIRICAL_V1, load_simulation_v1_normalized),
            (ENGINE_THERMO_KINETIC_V2, load_simulation_v2_normalized),
        )

    for engine_key, loader in load_sequence:
        try:
            simulation = loader(sim_id)
        except Exception:
            simulation = None
        if simulation:
            return simulation, engine_key

    try:
        simulation_raw, engine_used = _load_simulation_with_engine(sim_id, engine_hint=engine_hint)
    except Exception:
        return None, None
    if not simulation_raw:
        return None, None

    engine_final = engine_used or ENGINE_EMPIRICAL_V1
    try:
        simulation_norm = normalize_simulation_output(simulation_raw, engine=engine_final)
    except Exception:
        return None, None
    return simulation_norm, engine_final


def list_simulations_by_fit(fit_id):
    fit_id_target = str(fit_id or "").strip()
    if not fit_id_target:
        return []

    out_dir = _REOMETRIA_OUT_DIR
    if (not out_dir.exists()) or (not out_dir.is_dir()):
        return []

    try:
        files = sorted(out_dir.glob("sim*.npz"))
    except Exception:
        return []

    results = []
    seen_sim_ids = set()
    for file_path in files:
        sim_id, engine_hint = _extract_sim_id_from_filename(file_path.name)
        if not sim_id or sim_id in seen_sim_ids:
            continue
        seen_sim_ids.add(sim_id)

        try:
            simulation, engine_used = _load_simulation_normalized_with_fallback(sim_id, engine_hint=engine_hint)
        except Exception:
            simulation, engine_used = None, None
        if not simulation:
            continue

        sim_fit_id = str(simulation.get("fit_id") or "").strip()
        if sim_fit_id != fit_id_target:
            continue

        engine_key = _normalize_engine(simulation.get("engine") or engine_used or engine_hint)
        engine_status = str(simulation.get("engine_status") or get_engine_status(engine_key))
        mode = str(simulation.get("mode") or "--")
        status = str(simulation.get("status") or "concluida")
        mold_temp_c = _extract_mold_temp_c(simulation)

        created_at_raw = simulation.get("created_at")
        sort_ts = _timestamp_from_created_at(created_at_raw)
        if sort_ts is None:
            sort_ts = _timestamp_from_sim_id(sim_id)
        if sort_ts is None:
            try:
                sort_ts = float(file_path.stat().st_mtime)
            except Exception:
                sort_ts = 0.0

        results.append(
            {
                "sim_id": sim_id,
                "fit_id": sim_fit_id,
                "engine": engine_key,
                "engine_status": engine_status,
                "mode": mode,
                "mold_temp_c": mold_temp_c,
                "status": status,
                "created_at": created_at_raw,
                "created_at_display": _to_created_at_display(created_at_raw, sort_ts),
                "_sort_ts": float(sort_ts or 0.0),
            }
        )

    results.sort(key=lambda item: float(item.get("_sort_ts") or 0.0), reverse=True)
    for item in results:
        item.pop("_sort_ts", None)
    return results


def list_fit_reports(date_start=None, date_end=None, q=None, limit=300):
    out_dir = _REOMETRIA_OUT_DIR
    if (not out_dir.exists()) or (not out_dir.is_dir()):
        return []

    try:
        files = list(out_dir.glob("fit_*.json"))
    except Exception:
        return []

    start_date = _safe_date_from_yyyy_mm_dd(date_start)
    end_date = _safe_date_from_yyyy_mm_dd(date_end)
    q_norm = str(q or "").strip().lower()

    items = []
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        fit_id = str(payload.get("fit_id") or "").strip()
        if not fit_id:
            fit_id = file_path.stem.replace("fit_", "", 1)
        if not fit_id:
            continue

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        titulo = str(metadata.get("titulo") or "").strip()
        observacoes = str(metadata.get("observacoes") or "").strip()
        inicio_producao_raw = str(
            metadata.get("inicio_producao")
            or metadata.get("inicio_producao_datahora")
            or ""
        ).strip()
        created_at_raw = payload.get("created_at")
        created_dt = _safe_datetime_from_iso(created_at_raw)
        if created_dt is None:
            try:
                created_dt = datetime.fromtimestamp(float(file_path.stat().st_mtime), tz=timezone.utc)
            except Exception:
                created_dt = datetime.fromtimestamp(0.0, tz=timezone.utc)

        created_date = created_dt.date()
        if start_date and created_date < start_date:
            continue
        if end_date and created_date > end_date:
            continue

        if q_norm:
            searchable = " ".join(
                [
                    fit_id,
                    titulo,
                    observacoes,
                    inicio_producao_raw,
                    str(payload.get("message") or ""),
                    " ".join(str((e or {}).get("COD_ENSAIO", "")) for e in (payload.get("ensaios") or [])),
                ]
            ).lower()
            if q_norm not in searchable:
                continue

        rmse_value = _safe_float(payload.get("rmse_alpha"), default=None)
        rmse_torque = _safe_float(payload.get("rmse_torque"), default=None)
        ensaios_count = len(payload.get("ensaios") or [])
        success = bool(payload.get("success"))
        status = "OK" if success else "Falhou"
        created_at_display = created_at_raw if created_at_raw else created_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        model_family = str(payload.get("model_family") or DEFAULT_KINETIC_MODEL_FAMILY)
        model_version = str(payload.get("model_version") or "v1")
        fit_method = str(payload.get("fit_method") or "least_squares")

        items.append(
            {
                "fit_id": fit_id,
                "titulo": titulo,
                "observacoes": observacoes,
                "inicio_producao": inicio_producao_raw,
                "created_at": created_at_raw,
                "created_at_display": created_at_display,
                "rmse_alpha": rmse_value,
                "rmse_torque": rmse_torque,
                "status": status,
                "success": success,
                "ensaios_count": ensaios_count,
                "model_family": model_family,
                "model_version": model_version,
                "fit_method": fit_method,
                "_sort_ts": float(created_dt.timestamp()),
            }
        )

    items.sort(key=lambda item: float(item.get("_sort_ts") or 0.0), reverse=True)
    if limit and int(limit) > 0:
        items = items[: int(limit)]
    for item in items:
        item.pop("_sort_ts", None)
    return items


def _mm_to_cells(length_mm, dx):
    if length_mm is None or length_mm <= 0 or dx <= 0:
        return None
    length_m = float(length_mm) / 1000.0
    # We include both boundary faces in the mesh.
    cells = int(round(length_m / dx)) + 1
    return int(np.clip(cells, 2, 2000))


def _cleanup_sim_jobs():
    now = time.time()
    with _SIM_JOBS_LOCK:
        stale_ids = []
        for job_id, job in _SIM_JOBS.items():
            created_at = float(job.get("created_at") or now)
            if (now - created_at) > _SIM_JOB_TTL_SEC:
                stale_ids.append(job_id)
        for job_id in stale_ids:
            _SIM_JOBS.pop(job_id, None)


def _create_sim_job():
    _cleanup_sim_jobs()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "stage": "na_fila",
        "message": "Simulacao na fila.",
        "sim_id": None,
        "cancel_requested": False,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _SIM_JOBS_LOCK:
        _SIM_JOBS[job_id] = job
    return job_id


def _update_sim_job(job_id, **updates):
    with _SIM_JOBS_LOCK:
        job = _SIM_JOBS.get(job_id)
        if not job:
            return None
        job.update(updates)
        job["updated_at"] = time.time()
        return dict(job)


def _get_sim_job(job_id):
    with _SIM_JOBS_LOCK:
        job = _SIM_JOBS.get(job_id)
        return dict(job) if job else None


def _normalize_engine(value):
    return normalize_engine_registry(value)


def _resolve_engine(value, *, explicit_selection):
    return resolve_engine_for_execution(value, explicit_selection=bool(explicit_selection))


def _serialize_for_json(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_serialize_for_json(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _serialize_for_json(v) for k, v in value.items()}
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def _default_three_step_indices(max_idx):
    max_idx = int(max(_safe_float(max_idx, default=0) or 0, 0))
    if max_idx <= 0:
        return [0, 0, 0]
    if max_idx == 1:
        return [0, 1, 1]
    if max_idx == 2:
        return [0, 1, 2]
    return [0, max_idx // 2, max_idx]


def _parse_three_step_indices(raw_steps, max_idx):
    max_idx = int(max(_safe_float(max_idx, default=0) or 0, 0))
    tokens = []
    if raw_steps is not None:
        text = str(raw_steps).strip()
        if text:
            tokens = [part.strip() for part in text.split(",")]

    selected = []
    for token in tokens:
        if len(selected) >= 3:
            break
        try:
            idx = int(float(token))
        except (TypeError, ValueError):
            continue
        idx = int(np.clip(idx, 0, max_idx))
        if idx not in selected:
            selected.append(idx)

    defaults = _default_three_step_indices(max_idx)
    if len(selected) < 3:
        candidate_pool = defaults + list(range(max_idx + 1))
        for idx in candidate_pool:
            if idx not in selected:
                selected.append(idx)
            if len(selected) >= 3:
                break

    while len(selected) < 3:
        selected.append(defaults[min(len(selected), 2)])

    return selected[:3]


def _extract_fit_parameters_for_report(fit_payload):
    payload = fit_payload if isinstance(fit_payload, dict) else {}
    family = str(payload.get("model_family") or "").strip().lower()
    params_block = payload.get("model_parameters")
    params_block = params_block if isinstance(params_block, dict) else {}

    ordered_keys = []
    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        ordered_keys = ["Av1", "Av2", "Ev", "X", "Ar", "Er"]
    else:
        ordered_keys = ["k0", "k_ref", "Ea", "n"]

    rows = []
    seen = set()
    for key in ordered_keys:
        if key in seen:
            continue
        seen.add(key)
        if key in params_block:
            value = params_block.get(key)
            numeric = _round_two_decimals(value)
            rows.append({"name": key, "value": value if numeric is None else numeric})
            continue
        if key in payload:
            value = payload.get(key)
            numeric = _round_two_decimals(value)
            rows.append({"name": key, "value": value if numeric is None else numeric})

    if not rows:
        for key, value in params_block.items():
            numeric = _round_two_decimals(value)
            rows.append({"name": str(key), "value": value if numeric is None else numeric})
    return rows


def _extract_fit_curves_for_report(fit_payload):
    payload = fit_payload if isinstance(fit_payload, dict) else {}
    curves = payload.get("curves")
    if not isinstance(curves, list):
        return []

    rows = []
    for curve in curves:
        if not isinstance(curve, dict):
            continue
        tempo_raw = curve.get("tempo")
        torque_raw = curve.get("torque")
        torque_model_raw = curve.get("torque_model")
        t_rel_raw = curve.get("t_rel")
        alpha_raw = curve.get("alpha")
        alpha_model_raw = curve.get("alpha_model")
        rows.append(
            {
                "COD_ENSAIO": curve.get("COD_ENSAIO"),
                "T_C": _safe_float(curve.get("T_C"), default=None),
                "tempo": _serialize_for_json(np.asarray(tempo_raw if tempo_raw is not None else [], dtype=float)),
                "torque": _serialize_for_json(np.asarray(torque_raw if torque_raw is not None else [], dtype=float)),
                "torque_model": _serialize_for_json(
                    np.asarray(torque_model_raw if torque_model_raw is not None else [], dtype=float)
                ),
                "t_rel": _serialize_for_json(np.asarray(t_rel_raw if t_rel_raw is not None else [], dtype=float)),
                "alpha": _serialize_for_json(np.asarray(alpha_raw if alpha_raw is not None else [], dtype=float)),
                "alpha_model": _serialize_for_json(
                    np.asarray(alpha_model_raw if alpha_model_raw is not None else [], dtype=float)
                ),
                "ML": _safe_float(curve.get("ML"), default=None),
                "MH": _safe_float(curve.get("MH"), default=None),
            }
        )
    return rows


def _build_simulation_report_payload(sim, fit_payload, selected_steps):
    sim_safe = sim if isinstance(sim, dict) else {}
    fit_safe = fit_payload if isinstance(fit_payload, dict) else {}

    times_raw = sim_safe.get("times")
    t_snaps_raw = sim_safe.get("t_snaps")
    alpha_snaps_raw = sim_safe.get("alpha_snaps")
    times = np.asarray(times_raw if times_raw is not None else [], dtype=float).reshape(-1)
    t_snaps = np.asarray(t_snaps_raw if t_snaps_raw is not None else [], dtype=float)
    alpha_snaps = np.asarray(alpha_snaps_raw if alpha_snaps_raw is not None else [], dtype=float)

    snapshot_rows = []
    for raw_step in selected_steps:
        step_idx = int(np.clip(int(raw_step), 0, max(len(times) - 1, 0)))
        time_s = float(times[step_idx]) if len(times) else 0.0

        alpha_snapshot = np.asarray([], dtype=float)
        if alpha_snaps.ndim >= 1 and alpha_snaps.shape[0] > step_idx:
            alpha_snapshot = np.asarray(alpha_snaps[step_idx], dtype=float)

        temp_snapshot_c = np.asarray([], dtype=float)
        if t_snaps.ndim >= 1 and t_snaps.shape[0] > step_idx:
            temp_snapshot_c = np.asarray(t_snaps[step_idx], dtype=float) - 273.15

        snapshot_rows.append(
            {
                "step": int(step_idx),
                "time_s": float(time_s),
                "alpha_snapshot": _serialize_for_json(alpha_snapshot),
                "temp_snapshot_c": _serialize_for_json(temp_snapshot_c),
            }
        )

    metadata = fit_safe.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    report_payload = {
        "sim_id": str(sim_safe.get("sim_id") or ""),
        "fit_id": str(sim_safe.get("fit_id") or fit_safe.get("fit_id") or ""),
        "engine": str(sim_safe.get("engine") or "--"),
        "engine_status": str(sim_safe.get("engine_status") or "--"),
        "mode": str(sim_safe.get("mode") or "--"),
        "dim": int(_safe_float(sim_safe.get("dim"), default=0) or 0),
        "shape": _serialize_for_json(np.asarray(sim_safe.get("shape") or [], dtype=int)),
        "dx": _safe_float(sim_safe.get("dx"), default=None),
        "times": _serialize_for_json(times),
        "selected_steps": [int(item["step"]) for item in snapshot_rows],
        "snapshots": snapshot_rows,
        "fit_model_family": str(fit_safe.get("model_family") or "--"),
        "fit_model_version": str(fit_safe.get("model_version") or "--"),
        "fit_method": str(fit_safe.get("fit_method") or "--"),
        "fit_parameters": _extract_fit_parameters_for_report(fit_safe),
        "fit_curves": _extract_fit_curves_for_report(fit_safe),
        "fit_observacoes": str(metadata.get("observacoes") or ""),
        "fit_titulo": str(metadata.get("titulo") or ""),
    }
    return report_payload


def _run_simulation_with_engine(fit_payload, sim_params, progress_callback=None, cancel_checker=None):
    common_args = (
        fit_payload,
        sim_params["mode"],
        sim_params["dim"],
        sim_params["shape_raw"],
        sim_params["dx"],
        sim_params["dt"],
        sim_params["t_end"],
        sim_params["mold_temp_c"],
        sim_params["init_temp_c"],
        sim_params["ramp_rate"],
        sim_params["snapshot_every"],
        sim_params["platen_axis"],
    )
    explicit_selection = sim_params.get("engine_explicit")
    if explicit_selection is None:
        explicit_selection = sim_params.get("engine") is not None
    engine = _resolve_engine(sim_params.get("engine"), explicit_selection=explicit_selection)
    if engine == ENGINE_THERMO_KINETIC_V2:
        return run_simulation_v2(
            *common_args,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )
    if engine == ENGINE_AXISYMMETRIC_FIPY:
        raise RuntimeError(
            "Engine axisymmetric_fipy esta em status prototype e ainda nao foi integrada ao fluxo web de simulacao."
        )
    return run_simulation_v1(
        *common_args,
        progress_callback=progress_callback,
        cancel_checker=cancel_checker,
    )


def _load_simulation_with_engine(sim_id, engine_hint=None):
    engine_hint = _normalize_engine(engine_hint)

    if engine_hint == ENGINE_THERMO_KINETIC_V2:
        sim = load_simulation_v2(sim_id)
        if sim:
            return sim, ENGINE_THERMO_KINETIC_V2
        sim = load_simulation_v1(sim_id)
        if sim:
            return sim, ENGINE_EMPIRICAL_V1
        return None, None

    sim = load_simulation_v1(sim_id)
    if sim:
        return sim, ENGINE_EMPIRICAL_V1
    sim = load_simulation_v2(sim_id)
    if sim:
        return sim, ENGINE_THERMO_KINETIC_V2
    return None, None


def _parse_simulation_request(form_data):
    engine_raw = form_data.get("engine")
    engine_explicit = engine_raw is not None and str(engine_raw).strip() != ""
    engine = _resolve_engine(engine_raw, explicit_selection=engine_explicit)
    mode = str(form_data.get("mode", "prensa") or "prensa").strip().lower()
    if mode not in ("prensa", "autoclave"):
        mode = "prensa"

    dim = parse_int_locale(form_data.get("dim", 1), default=1, min_value=1)
    if dim not in (1, 2, 3):
        dim = 1

    shape = str(form_data.get("shape", "200") or "200").strip()
    shape = shape.replace(";", ",").replace("x", ",").replace("X", ",")

    dx = parse_float_locale(form_data.get("dx", 0.001), default=0.001)
    dt = parse_float_locale(form_data.get("dt", 0.5), default=0.5)
    t_end = parse_float_locale(form_data.get("t_end", 600), default=600.0)
    mold_temp_c = parse_float_locale(form_data.get("mold_temp_c", 170), default=170.0)
    init_temp_c = parse_float_locale(form_data.get("init_temp_c", 25), default=25.0)
    ramp_rate = parse_float_locale(form_data.get("ramp_rate", 0), default=0.0)
    snapshot_every = parse_int_locale(form_data.get("snapshot_every", 20), default=20, min_value=1)

    if dx is None or dx <= 0:
        dx = 0.001
    if dt is None or dt <= 0:
        dt = 0.5
    if t_end is None or t_end <= 0:
        t_end = 600.0
    if mold_temp_c is None:
        mold_temp_c = 170.0
    if init_temp_c is None:
        init_temp_c = 25.0
    if ramp_rate is None:
        ramp_rate = 0.0

    thickness_mm = parse_float_locale(form_data.get("thickness_mm"), default=None)
    width_mm = parse_float_locale(form_data.get("width_mm"), default=None)
    length_mm = parse_float_locale(form_data.get("length_mm"), default=None)

    dims_mm = [thickness_mm, width_mm, length_mm][:dim]
    shape_from_mm = [_mm_to_cells(mm_val, dx) for mm_val in dims_mm]
    if all(val is not None for val in shape_from_mm) and len(shape_from_mm) == dim:
        shape = ",".join(str(int(val)) for val in shape_from_mm)

    platen_axis = None
    if mode == "prensa" and dim in (2, 3):
        platen_axis_raw = str(form_data.get("platen_axis", "thickness") or "thickness").strip().lower()
        axis_map = {"thickness": 0, "width": 1, "length": 2}
        platen_axis = int(np.clip(axis_map.get(platen_axis_raw, 0), 0, dim - 1))

    return {
        "engine": engine,
        "engine_explicit": bool(engine_explicit),
        "mode": mode,
        "dim": int(dim),
        "shape_raw": shape,
        "dx": float(dx),
        "dt": float(dt),
        "t_end": float(t_end),
        "mold_temp_c": float(mold_temp_c),
        "init_temp_c": float(init_temp_c),
        "ramp_rate": float(ramp_rate),
        "snapshot_every": int(snapshot_every),
        "platen_axis": platen_axis,
    }


def _run_simulation_job(job_id, fit_payload, sim_params):
    def _progress_callback(event):
        _update_sim_job(
            job_id,
            status="running",
            progress=int(event.get("progress", 0)),
            stage=str(event.get("stage") or "processando"),
            message=str(event.get("message") or "Processando simulacao..."),
        )

    def _cancel_checker():
        job = _get_sim_job(job_id)
        return bool(job and job.get("cancel_requested"))

    try:
        _update_sim_job(
            job_id,
            status="running",
            progress=1,
            stage="iniciando",
            message="Iniciando simulacao...",
            engine=_normalize_engine(sim_params.get("engine")),
            engine_status=get_engine_status(sim_params.get("engine")),
        )
        sim_id, _ = _run_simulation_with_engine(
            fit_payload,
            sim_params,
            progress_callback=_progress_callback,
            cancel_checker=_cancel_checker,
        )
        _update_sim_job(
            job_id,
            status="completed",
            progress=100,
            stage="concluido",
            message="Simulacao concluida com sucesso.",
            sim_id=sim_id,
            engine=_normalize_engine(sim_params.get("engine")),
            engine_status=get_engine_status(sim_params.get("engine")),
            finished_at=time.time(),
        )
    except RuntimeError as exc:
        if str(exc) == "SIMULATION_CANCELLED":
            _update_sim_job(
                job_id,
                status="cancelled",
                progress=0,
                stage="cancelado",
                message="Operacao cancelada pelo usuario.",
                finished_at=time.time(),
            )
        else:
            _update_sim_job(
                job_id,
                status="failed",
                stage="falha",
                message=f"Falha na simulacao: {exc}",
                finished_at=time.time(),
            )
    except Exception as exc:
        _update_sim_job(
            job_id,
            status="failed",
            stage="falha",
            message=f"Falha na simulacao: {exc}",
            finished_at=time.time(),
        )


@reometria_bp.route("/reometria/fit")
@login_required
def reometria_fit():
    filters = {
        "date_start": request.args.get("date_start", ""),
        "date_end": request.args.get("date_end", ""),
        "q": request.args.get("q", ""),
    }
    fit_filters = {
        "fit_date_start": request.args.get("fit_date_start", ""),
        "fit_date_end": request.args.get("fit_date_end", ""),
        "fit_q": request.args.get("fit_q", ""),
    }
    ensaios = list_ensaios_for_fit(filters["date_start"] or None, filters["date_end"] or None, filters["q"] or None)
    fits_history = list_fit_reports(
        date_start=fit_filters["fit_date_start"] or None,
        date_end=fit_filters["fit_date_end"] or None,
        q=fit_filters["fit_q"] or None,
    )
    return render_template(
        "reometria/fit.html",
        ensaios=ensaios,
        filters=filters,
        fit_filters=fit_filters,
        fits_history=fits_history,
    )


@reometria_bp.route("/reometria/fit/preview/<int:cod_ensaio>")
@login_required
def reometria_fit_preview(cod_ensaio):
    curve = get_preview_curve(cod_ensaio)
    if not curve:
        flash("Curva nao encontrada.", "warning")
        return redirect(url_for("reometria.reometria_fit"))
    return render_template("reometria/preview.html", curve=curve)


@reometria_bp.route("/reometria/fit/run", methods=["POST"])
@login_required
def reometria_fit_run():
    cod_ensaios = [int(value) for value in request.form.getlist("cod_ensaios") if str(value).strip()]
    if len(cod_ensaios) < 2:
        flash("Selecione pelo menos 2 curvas para o ajuste.", "warning")
        return redirect(url_for("reometria.reometria_fit"))

    requested_family = str(request.form.get("model_family") or DEFAULT_KINETIC_MODEL_FAMILY).strip().lower()
    if requested_family not in SUPPORTED_MODEL_FAMILIES:
        requested_family = DEFAULT_KINETIC_MODEL_FAMILY

    ref_temp_k = parse_float_locale(
        request.form.get("reference_temperature_k"),
        default=DEFAULT_REFERENCE_TEMPERATURE_K,
    )
    if ref_temp_k is None or ref_temp_k <= 0:
        ref_temp_k = DEFAULT_REFERENCE_TEMPERATURE_K

    cure_rule = str(request.form.get("cure_completion_rule") or DEFAULT_CURE_COMPLETION_RULE).strip().lower()
    if cure_rule not in SUPPORTED_CURE_COMPLETION_RULES:
        cure_rule = DEFAULT_CURE_COMPLETION_RULE

    payload = run_fit(
        cod_ensaios,
        model_family=requested_family,
        reference_temperature_k=ref_temp_k,
        cure_completion_rule=cure_rule,
    )
    if not payload.get("success"):
        flash(payload.get("message", "Ajuste falhou."), "danger")
    else:
        flash("Ajuste executado com sucesso.", "success")
    return redirect(url_for("reometria.reometria_fit_result", fit_id=payload["fit_id"]))


@reometria_bp.route("/reometria/fit/result/<fit_id>")
@login_required
def reometria_fit_result(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        flash("Resultado de fit nao encontrado.", "danger")
        return redirect(url_for("reometria.reometria_fit"))
    alpha_time_comparison = build_alpha_time_comparison(payload)
    simulacoes_executadas = list_simulations_by_fit(fit_id)
    return render_template(
        "reometria/fit_result.html",
        payload=payload,
        alpha_time_comparison=alpha_time_comparison,
        simulacoes_executadas=simulacoes_executadas,
    )


@reometria_bp.route("/reometria/fit/update/<fit_id>", methods=["POST"])
@login_required
def reometria_fit_update(fit_id):
    data = request.get_json(silent=True) or {}
    payload_current = load_fit_payload(fit_id)
    if not payload_current:
        return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

    family = str(payload_current.get("model_family") or DEFAULT_KINETIC_MODEL_FAMILY).strip().lower()
    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        updates = {}
        for key in ("Av1", "Av2", "Ev", "X", "Ar", "Er"):
            value = parse_float_locale(data.get(key), default=None)
            if value is None:
                return jsonify({"success": False, "message": f"Parametro {key} invalido."}), 400
            rounded = _round_two_decimals(value)
            updates[key] = float(value if rounded is None else rounded)

        if updates["Av1"] <= 0 or updates["Av2"] <= 0:
            return jsonify({"success": False, "message": "Av1 e Av2 devem ser positivos."}), 400
        if not (0.0 < updates["X"] < 1.0):
            return jsonify({"success": False, "message": "X deve estar no intervalo (0, 1)."}), 400
        if updates["Ar"] < 0:
            return jsonify({"success": False, "message": "Ar nao pode ser negativo."}), 400

        payload = update_fit_parameters(fit_id, parameter_updates=updates)
        if not payload:
            return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

        return jsonify(
            {
                "success": True,
                "message": "Parametros atualizados com sucesso.",
                "fit_id": payload.get("fit_id"),
                "model_family": payload.get("model_family"),
                "model_version": payload.get("model_version"),
                "reference_temperature_K": _safe_float(payload.get("reference_temperature_K"), default=None),
                "cure_completion_rule": payload.get("cure_completion_rule"),
                "Av1": float(_round_two_decimals(payload.get("Av1")) or 0.0),
                "Av2": float(_round_two_decimals(payload.get("Av2")) or 0.0),
                "Ev": float(_round_two_decimals(payload.get("Ev")) or 0.0),
                "X": float(_round_two_decimals(payload.get("X")) or 0.0),
                "Ar": float(_round_two_decimals(payload.get("Ar")) or 0.0),
                "Er": float(_round_two_decimals(payload.get("Er")) or 0.0),
                "calibration_stage_selected": payload.get("calibration_stage_selected"),
                "identifiability": payload.get("identifiability"),
                "alerts": payload.get("alerts") or [],
            }
        )

    kinetic_key = "k0" if family == MODEL_FAMILY_EDO_ORDER_N_V1 else "k_ref"
    k_value = parse_float_locale(data.get(kinetic_key), default=None)
    if k_value is None:
        k_value = parse_float_locale(data.get("k0"), default=None)
    if k_value is None:
        k_value = parse_float_locale(data.get("k_ref"), default=None)
    ea = parse_float_locale(data.get("Ea"), default=None)
    n = parse_float_locale(data.get("n"), default=None)

    if k_value is None or ea is None or n is None:
        return jsonify({"success": False, "message": "Parametros invalidos para atualizacao."}), 400
    if k_value <= 0:
        return jsonify({"success": False, "message": f"{kinetic_key} deve ser positivo."}), 400

    payload = update_fit_parameters(fit_id, k_value, ea, n)
    if not payload:
        return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

    kinetic_value = _safe_float(payload.get(kinetic_key), default=None)
    if kinetic_value is None:
        kinetic_value = _safe_float(payload.get("k_ref"), default=None)
    if kinetic_value is None:
        kinetic_value = _safe_float(payload.get("k0"), default=0.0)

    return jsonify(
        {
            "success": True,
            "message": "Parametros atualizados com sucesso.",
            "fit_id": payload.get("fit_id"),
            "model_family": payload.get("model_family"),
            "model_version": payload.get("model_version"),
            "reference_temperature_K": _safe_float(payload.get("reference_temperature_K"), default=None),
            "cure_completion_rule": payload.get("cure_completion_rule"),
            kinetic_key: float(kinetic_value),
            "k0": float(_safe_float(payload.get("k0"), default=0.0)),
            "k_ref": float(_safe_float(payload.get("k_ref"), default=_safe_float(payload.get("k0"), default=0.0))),
            "Ea": float(payload.get("Ea", 0.0)),
            "n": float(payload.get("n", 0.0)),
        }
    )


@reometria_bp.route("/reometria/fit/update_meta/<fit_id>", methods=["POST"])
@login_required
def reometria_fit_update_meta(fit_id):
    try:
        data = request.get_json(silent=False)
    except (BadRequest, UnsupportedMediaType):
        return jsonify({"success": False, "message": "Body JSON invalido."}), 400

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "Body JSON invalido."}), 400

    payload = load_fit_payload(fit_id)
    if not payload:
        return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    titulo = str(data.get("titulo") or "").strip()
    observacoes = str(data.get("observacoes") or "").strip()
    inicio_producao = str(
        data.get("inicio_producao")
        or data.get("inicio_producao_datahora")
        or ""
    ).strip()
    if inicio_producao:
        inicio_dt = _safe_datetime_from_iso(inicio_producao)
        if inicio_dt is None:
            return jsonify({"success": False, "message": "Data/hora de inicio da producao invalida."}), 400
        inicio_producao = inicio_dt.isoformat()

    metadata["titulo"] = titulo
    metadata["observacoes"] = observacoes
    metadata["inicio_producao"] = inicio_producao
    payload["metadata"] = metadata

    try:
        save_fit_payload(payload)
    except Exception:
        return jsonify({"success": False, "message": "Falha ao salvar metadados."}), 500

    return jsonify({"success": True, "message": "Metadados atualizados"})


@reometria_bp.route("/reometria/simulate/<fit_id>")
@login_required
def reometria_simulate_form(fit_id):
    mode = request.args.get("mode", "prensa")
    if mode not in ("prensa", "autoclave"):
        mode = "prensa"
    engine_qs = request.args.get("engine")
    engine = _resolve_engine(engine_qs, explicit_selection=(engine_qs is not None and str(engine_qs).strip() != ""))
    payload = load_fit_payload(fit_id)
    if not payload:
        flash("Fit nao encontrado.", "danger")
        return redirect(url_for("reometria.reometria_fit"))
    return render_template(
        "reometria/sim_form.html",
        fit_id=fit_id,
        mode=mode,
        engine=engine,
        default_engine=_resolve_engine(None, explicit_selection=False),
        engine_catalog=list_engine_catalog(include_disabled=True),
    )


@reometria_bp.route("/reometria/simulate/run/<fit_id>", methods=["POST"])
@login_required
def reometria_simulate_run(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        flash("Fit nao encontrado.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    sim_params = _parse_simulation_request(request.form)

    try:
        sim_id, _ = _run_simulation_with_engine(payload, sim_params)
    except Exception as exc:
        flash(f"Falha ao executar simulacao: {exc}", "danger")
        return redirect(
            url_for(
                "reometria.reometria_simulate_form",
                fit_id=fit_id,
                mode=sim_params["mode"],
                engine=sim_params["engine"],
            )
        )
    return redirect(url_for("reometria.reometria_simulate_view", sim_id=sim_id, engine=sim_params["engine"]))


@reometria_bp.route("/reometria/simulate/start/<fit_id>", methods=["POST"])
@login_required
def reometria_simulate_start(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

    sim_params = _parse_simulation_request(request.form)
    job_id = _create_sim_job()
    _update_sim_job(
        job_id,
        engine=sim_params["engine"],
        engine_status=get_engine_status(sim_params["engine"]),
    )

    worker = threading.Thread(
        target=_run_simulation_job,
        args=(job_id, payload, sim_params),
        daemon=True,
    )
    worker.start()

    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status_url": url_for("reometria.reometria_simulate_job_status", job_id=job_id),
            "cancel_url": url_for("reometria.reometria_simulate_job_cancel", job_id=job_id),
        }
    )


@reometria_bp.route("/reometria/simulate/job/<job_id>", methods=["GET"])
@login_required
def reometria_simulate_job_status(job_id):
    job = _get_sim_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "Job nao encontrado."}), 404
    return jsonify({"success": True, **job})


@reometria_bp.route("/reometria/simulate/job/<job_id>/cancel", methods=["POST"])
@login_required
def reometria_simulate_job_cancel(job_id):
    job = _get_sim_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "Job nao encontrado."}), 404

    if job["status"] in ("completed", "failed", "cancelled"):
        return jsonify({"success": True, "message": "Job ja finalizado.", **job})

    updated = _update_sim_job(
        job_id,
        cancel_requested=True,
        stage="cancelando",
        message="Solicitacao de cancelamento recebida...",
    )
    return jsonify({"success": True, **(updated or job)})


@reometria_bp.route("/reometria/simulate/view/<sim_id>")
@login_required
def reometria_simulate_view(sim_id):
    engine_hint = request.args.get("engine", ENGINE_EMPIRICAL_V1)
    sim, engine_used = _load_simulation_with_engine(sim_id, engine_hint=engine_hint)
    if not sim:
        flash("Simulacao nao encontrada.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    sim = normalize_simulation_output(sim, engine=(engine_used or ENGINE_EMPIRICAL_V1))
    serializable = _serialize_for_json(sim)
    return render_template("reometria/sim_view.html", sim=sim, sim_json=json.dumps(serializable))


@reometria_bp.route("/reometria/simulate/export/<sim_id>")
@login_required
def reometria_simulate_export(sim_id):
    engine_hint = request.args.get("engine", ENGINE_EMPIRICAL_V1)
    sim, engine_used = _load_simulation_with_engine(sim_id, engine_hint=engine_hint)
    if not sim:
        flash("Simulacao nao encontrada.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    sim_norm = normalize_simulation_output(sim, engine=(engine_used or ENGINE_EMPIRICAL_V1))
    fit_payload = load_fit_payload(sim_norm.get("fit_id")) if sim_norm.get("fit_id") else None
    export_bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "simulation": _serialize_for_json(sim_norm),
        "fit": _serialize_for_json(fit_payload) if isinstance(fit_payload, dict) else None,
    }
    body = json.dumps(export_bundle, ensure_ascii=False, indent=2)
    filename = f"reometria_sim_{sim_id}.json"
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reometria_bp.route("/reometria/simulate/report/<sim_id>")
@login_required
def reometria_simulate_report(sim_id):
    engine_hint = request.args.get("engine", ENGINE_EMPIRICAL_V1)
    sim, engine_used = _load_simulation_with_engine(sim_id, engine_hint=engine_hint)
    if not sim:
        flash("Simulacao nao encontrada.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    sim_norm = normalize_simulation_output(sim, engine=(engine_used or ENGINE_EMPIRICAL_V1))
    fit_id = str(sim_norm.get("fit_id") or "").strip()
    fit_payload = load_fit_payload(fit_id) if fit_id else {}

    times_raw = sim_norm.get("times")
    times_arr = np.asarray(times_raw if times_raw is not None else [], dtype=float).reshape(-1)
    max_idx = max(int(times_arr.size - 1), 0)
    selected_steps = _parse_three_step_indices(request.args.get("steps"), max_idx=max_idx)
    report_payload = _build_simulation_report_payload(sim_norm, fit_payload, selected_steps)
    return render_template(
        "reometria/sim_report.html",
        report=report_payload,
        report_json=json.dumps(_serialize_for_json(report_payload), ensure_ascii=False),
    )
