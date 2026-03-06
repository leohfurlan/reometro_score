import json

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
from services.vulcanization_service import load_simulation, run_simulation

from reoscore.webapp.utils import parse_float_locale, parse_int_locale

reometria_bp = Blueprint("reometria", __name__)


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
    payload = load_fit_payload(fit_id)
    if not payload:
        flash("Fit nao encontrado.", "danger")
        return redirect(url_for("reometria.reometria_fit"))
    return render_template("reometria/sim_form.html", fit_id=fit_id, mode=mode)


@reometria_bp.route("/reometria/simulate/run/<fit_id>", methods=["POST"])
@login_required
def reometria_simulate_run(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        flash("Fit nao encontrado.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    mode = request.form.get("mode", "prensa")
    if mode not in ("prensa", "autoclave"):
        mode = "prensa"

    dim = parse_int_locale(request.form.get("dim", 1), default=1, min_value=1)
    if dim not in (1, 2, 3):
        dim = 1

    shape = str(request.form.get("shape", "200") or "200").strip()
    shape = shape.replace(";", ",").replace("x", ",").replace("X", ",")

    dx = parse_float_locale(request.form.get("dx", 0.001), default=0.001)
    dt = parse_float_locale(request.form.get("dt", 0.5), default=0.5)
    t_end = parse_float_locale(request.form.get("t_end", 600), default=600.0)
    mold_temp_c = parse_float_locale(request.form.get("mold_temp_c", 170), default=170.0)
    init_temp_c = parse_float_locale(request.form.get("init_temp_c", 25), default=25.0)
    ramp_rate = parse_float_locale(request.form.get("ramp_rate", 0), default=0.0)
    snapshot_every = parse_int_locale(request.form.get("snapshot_every", 20), default=20, min_value=1)

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

    try:
        sim_id, _ = run_simulation(
            payload,
            mode,
            dim,
            shape,
            dx,
            dt,
            t_end,
            mold_temp_c,
            init_temp_c,
            ramp_rate,
            snapshot_every,
        )
    except Exception as exc:
        flash(f"Falha ao executar simulacao: {exc}", "danger")
        return redirect(url_for("reometria.reometria_simulate_form", fit_id=fit_id, mode=mode))
    return redirect(url_for("reometria.reometria_simulate_view", sim_id=sim_id))


@reometria_bp.route("/reometria/simulate/view/<sim_id>")
@login_required
def reometria_simulate_view(sim_id):
    sim = load_simulation(sim_id)
    if not sim:
        flash("Simulacao nao encontrada.", "danger")
        return redirect(url_for("reometria.reometria_fit"))

    serializable = {
        **sim,
        "times": sim["times"].tolist(),
        "t_snaps": sim["t_snaps"].tolist(),
        "alpha_snaps": sim["alpha_snaps"].tolist(),
    }
    return render_template("reometria/sim_view.html", sim=sim, sim_json=json.dumps(serializable))
