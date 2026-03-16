import json
import threading
import time
import uuid

import numpy as np
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from services.kinetics_service import (
    build_alpha_time_comparison,
    get_preview_curve,
    list_ensaios_for_fit,
    load_fit_payload,
    run_fit,
    update_fit_parameters,
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
from services.vulcanization_service import load_simulation as load_simulation_v1, run_simulation as run_simulation_v1
from services.vulcanization_service_v2 import load_simulation_v2, run_simulation_v2

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
    ensaios = list_ensaios_for_fit(filters["date_start"] or None, filters["date_end"] or None, filters["q"] or None)
    return render_template("reometria/fit.html", ensaios=ensaios, filters=filters)


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

    payload = run_fit(cod_ensaios)
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
    return render_template("reometria/fit_result.html", payload=payload, alpha_time_comparison=alpha_time_comparison)


@reometria_bp.route("/reometria/fit/update/<fit_id>", methods=["POST"])
@login_required
def reometria_fit_update(fit_id):
    data = request.get_json(silent=True) or {}

    k0 = parse_float_locale(data.get("k0"), default=None)
    ea = parse_float_locale(data.get("Ea"), default=None)
    n = parse_float_locale(data.get("n"), default=None)

    if k0 is None or ea is None or n is None:
        return jsonify({"success": False, "message": "Parametros invalidos para atualizacao."}), 400
    if k0 <= 0:
        return jsonify({"success": False, "message": "k0 deve ser positivo."}), 400

    payload = update_fit_parameters(fit_id, k0, ea, n)
    if not payload:
        return jsonify({"success": False, "message": "Fit nao encontrado."}), 404

    return jsonify(
        {
            "success": True,
            "message": "Parametros atualizados com sucesso.",
            "fit_id": payload.get("fit_id"),
            "k0": float(payload.get("k0", 0.0)),
            "Ea": float(payload.get("Ea", 0.0)),
            "n": float(payload.get("n", 0.0)),
        }
    )


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
