import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np

from services.kinetic_model_service import (
    ALPHA_METRIC_TARGETS_DEFAULT,
    AUTO_COMPARE_MODEL_FAMILIES,
    MODEL_FAMILY_EDO_ORDER_N_V1,
    MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
    MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
    compare_model_families,
    compute_equivalent_time as compute_equivalent_time_core,
    compute_txx_errors,
    fit_model_parameters,
    normalize_model_family,
    predict_alpha,
    predict_alpha_nonisothermal,
    torque_from_alpha_kamal_sourour_expanded,
    torque_from_alpha,
)
from services.reometry_dataset_service import load_reometry_dataset

R_GAS = 8.314462618
REPORT_DEFAULT_PATH = Path("reports/kinetic_fit_report.html")
BACKTEST_CV_ALERT_LIMITS = {
    "Ea": 0.25,
    "n": 0.20,
    "k": 0.35,
}


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def estimate_ml_mh(torque: Sequence[float]):
    torque_vec = np.asarray(torque, dtype=float)
    torque_vec = torque_vec[np.isfinite(torque_vec)]
    if torque_vec.size < 3:
        raise ValueError("Curva invalida: torque com menos de 3 pontos validos.")

    head_count = int(np.clip(round(torque_vec.size * 0.10), 5, 40))
    tail_count = int(np.clip(round(torque_vec.size * 0.20), 5, 60))
    head = torque_vec[:head_count]
    tail = torque_vec[-tail_count:]

    ml = float(np.median([np.mean(head), np.percentile(torque_vec, 5.0)]))
    mh = float(np.median([np.mean(tail), np.percentile(torque_vec, 95.0)]))

    t_min = float(np.min(torque_vec))
    t_max = float(np.max(torque_vec))
    ml = float(np.clip(ml, t_min, t_max))
    mh = float(np.clip(mh, t_min, t_max))
    if mh <= ml + 1e-9:
        mh = max(t_max, ml + 1e-6)
    return ml, mh


def _alpha_from_torque_with_bounds(torque: Sequence[float], ml: float, mh: float):
    torque_vec = np.asarray(torque, dtype=float)
    delta = float(mh) - float(ml)
    if abs(delta) < 1e-12:
        alpha = np.zeros_like(torque_vec, dtype=float)
    else:
        alpha = (torque_vec - float(ml)) / delta
    return np.clip(alpha, 0.0, 1.0)


def compute_alpha_from_torque(torque, ml, mh):
    return _alpha_from_torque_with_bounds(torque, ml=ml, mh=mh)


def compute_equivalent_time(time_vector, temperature_vector, Ea, T_ref):
    return compute_equivalent_time_core(time_vector, temperature_vector, Ea, T_ref)


def _normalize_dataset_curve(raw_curve: Dict[str, Any], idx: int):
    temp_k = _safe_float(raw_curve.get("temperature_K"), default=None)
    temp_c = _safe_float(raw_curve.get("temperature_C"), default=None)
    if temp_k is None and temp_c is not None:
        temp_k = temp_c + 273.15
    if temp_c is None and temp_k is not None:
        temp_c = temp_k - 273.15
    if temp_k is None:
        raise ValueError(f"Curva {idx}: temperatura ausente.")

    time_vec = np.asarray(raw_curve.get("time") or [], dtype=float)
    torque_vec = np.asarray(raw_curve.get("torque") or [], dtype=float)
    size = min(time_vec.size, torque_vec.size)
    if size < 3:
        raise ValueError(f"Curva {idx}: pontos insuficientes.")

    time_vec = np.maximum.accumulate(np.maximum(time_vec[:size], 0.0))
    torque_vec = torque_vec[:size]

    keep = np.concatenate(([True], np.diff(time_vec) > 1e-12))
    time_vec = time_vec[keep]
    torque_vec = torque_vec[keep]
    if time_vec.size < 3:
        raise ValueError(f"Curva {idx}: tempos nao estritamente crescentes.")

    ml, mh = estimate_ml_mh(torque_vec)
    alpha_vec = _alpha_from_torque_with_bounds(torque_vec, ml=ml, mh=mh)

    return {
        "curve_id": raw_curve.get("curve_id", f"curve_{idx:03d}"),
        "temperature_C": float(temp_c),
        "temperature_K": float(temp_k),
        "time": time_vec,
        "torque": torque_vec,
        "ML": float(ml),
        "MH": float(mh),
        "alpha_real": alpha_vec,
    }


def _prepare_dataset(dataset: Iterable[Dict[str, Any]]):
    prepared = []
    for idx, curve in enumerate(dataset, start=1):
        prepared.append(_normalize_dataset_curve(curve, idx=idx))
    if len(prepared) < 2:
        raise ValueError("Dataset deve conter pelo menos 2 curvas.")
    unique_temps = {round(float(c["temperature_K"]), 6) for c in prepared}
    if len(unique_temps) < 2:
        raise ValueError("Dataset deve conter multiplas temperaturas.")
    return prepared


def _to_fit_curves(prepared_dataset):
    fit_curves = []
    for curve in prepared_dataset:
        time_vec = np.asarray(curve["time"], dtype=float)
        t_rel = np.maximum(time_vec - float(time_vec[0]), 0.0)
        fit_curves.append(
            {
                "COD_ENSAIO": curve["curve_id"],
                "T_C": float(curve["temperature_C"]),
                "T_K": float(curve["temperature_K"]),
                "ML": float(curve["ML"]),
                "MH": float(curve["MH"]),
                "TMINTEMPO": float(time_vec[0]),
                "tempo": time_vec,
                "torque": np.asarray(curve["torque"], dtype=float),
                "t_rel": t_rel,
                "alpha": np.asarray(curve["alpha_real"], dtype=float),
                "temp_k_profile": np.full((len(t_rel),), fill_value=float(curve["temperature_K"]), dtype=float),
            }
        )
    return fit_curves


def _predict_family_on_prepared_curve(curve, params, model_family, reference_temperature_k):
    time = np.asarray(curve["time"], dtype=float)
    t_rel = np.maximum(time - float(time[0]), 0.0)
    temp_profile = np.full((len(t_rel),), fill_value=float(curve["temperature_K"]), dtype=float)
    return predict_alpha_nonisothermal(
        time_s=t_rel,
        temperature_k=temp_profile,
        params=params,
        model_family=model_family,
        reference_temperature_k=reference_temperature_k,
    )


def predict_alpha_pinheiro(params, dataset, reference_temperature_k=None):
    prepared = _prepare_dataset(dataset)
    if reference_temperature_k is None:
        reference_temperature_k = float(np.median([c["temperature_K"] for c in prepared]))

    out = []
    for curve in prepared:
        alpha_model = _predict_family_on_prepared_curve(
            curve,
            params=params,
            model_family=MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
            reference_temperature_k=reference_temperature_k,
        )
        out.append(
            {
                "curve_id": curve["curve_id"],
                "temperature_K": curve["temperature_K"],
                "time": curve["time"].tolist(),
                "alpha_model": np.clip(np.asarray(alpha_model, dtype=float), 0.0, 1.0).tolist(),
            }
        )
    return out


def predict_alpha_edo(params, dataset, reference_temperature_k=None):
    prepared = _prepare_dataset(dataset)
    if reference_temperature_k is None:
        reference_temperature_k = float(np.median([c["temperature_K"] for c in prepared]))

    out = []
    for curve in prepared:
        alpha_model = _predict_family_on_prepared_curve(
            curve,
            params=params,
            model_family=MODEL_FAMILY_EDO_ORDER_N_V1,
            reference_temperature_k=reference_temperature_k,
        )
        out.append(
            {
                "curve_id": curve["curve_id"],
                "temperature_K": curve["temperature_K"],
                "time": curve["time"].tolist(),
                "alpha_model": np.clip(np.asarray(alpha_model, dtype=float), 0.0, 1.0).tolist(),
            }
        )
    return out


def _evaluate_candidate(prepared_dataset, candidate, reference_temperature_k):
    fit_result = dict(candidate.get("fit_result") or {})
    params = dict(fit_result.get("model_parameters") or {})
    family = normalize_model_family(candidate.get("family"))
    if not fit_result.get("success"):
        return {
            "success": False,
            "metrics": {
                "RMSE_alpha": float("inf"),
                "RMSE_torque": float("inf"),
                "T90_MAE_s": float("inf"),
            },
            "curves": [],
        }

    alpha_residuals = []
    torque_residuals = []
    t90_errors = []
    curves_eval = []

    for curve in prepared_dataset:
        alpha_model = np.clip(
            np.asarray(
                _predict_family_on_prepared_curve(
                    curve,
                    params=params,
                    model_family=family,
                    reference_temperature_k=reference_temperature_k,
                ),
                dtype=float,
            ),
            0.0,
            1.0,
        )

        alpha_real = np.asarray(curve["alpha_real"], dtype=float)
        time = np.asarray(curve["time"], dtype=float)
        torque_real = np.asarray(curve["torque"], dtype=float)
        size = min(alpha_model.size, alpha_real.size, time.size, torque_real.size)
        if size == 0:
            continue

        alpha_model = alpha_model[:size]
        alpha_real = alpha_real[:size]
        time = time[:size]
        torque_real = torque_real[:size]
        t_rel = np.maximum(time - float(time[0]), 0.0)

        if family == MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1:
            t_rel_for_torque = np.maximum(time - float(time[0]), 0.0)
            torque_model = np.asarray(
                torque_from_alpha_kamal_sourour_expanded(
                    alpha=alpha_model,
                    time_s=t_rel_for_torque,
                    m_min=curve["ML"],
                    m_max=curve["MH"],
                    params=params,
                ),
                dtype=float,
            )
        else:
            torque_model = np.asarray(
                torque_from_alpha(alpha_model, curve["ML"], curve["MH"]),
                dtype=float,
            )
        torque_model = torque_model[:size]

        alpha_err = alpha_model - alpha_real
        torque_err = torque_model - torque_real
        alpha_residuals.append(alpha_err)
        torque_residuals.append(torque_err)

        txx = compute_txx_errors(t_rel, alpha_real, alpha_model, targets=ALPHA_METRIC_TARGETS_DEFAULT)
        t90_err = None
        for row in txx:
            if abs(float(row.get("alpha_target", 0.0)) - 0.90) <= 1e-9:
                t90_err = row.get("error_s")
                break
        if t90_err is not None and np.isfinite(float(t90_err)):
            t90_errors.append(float(t90_err))

        curves_eval.append(
            {
                "curve_id": curve["curve_id"],
                "temperature_K": float(curve["temperature_K"]),
                "temperature_C": float(curve["temperature_C"]),
                "time": time.tolist(),
                "t_rel": t_rel.tolist(),
                "alpha_real": alpha_real.tolist(),
                "alpha_model": alpha_model.tolist(),
                "torque_real": torque_real.tolist(),
                "torque_model": torque_model.tolist(),
                "txx": txx,
                "RMSE_alpha": float(np.sqrt(np.mean(np.square(alpha_err)))) if alpha_err.size else None,
                "RMSE_torque": float(np.sqrt(np.mean(np.square(torque_err)))) if torque_err.size else None,
            }
        )

    alpha_vec = np.concatenate(alpha_residuals) if alpha_residuals else np.asarray([], dtype=float)
    torque_vec = np.concatenate(torque_residuals) if torque_residuals else np.asarray([], dtype=float)
    t90_arr = np.asarray(t90_errors, dtype=float) if t90_errors else np.asarray([], dtype=float)

    rmse_alpha = float(np.sqrt(np.mean(np.square(alpha_vec)))) if alpha_vec.size else float("inf")
    rmse_torque = float(np.sqrt(np.mean(np.square(torque_vec)))) if torque_vec.size else float("inf")
    t90_mae = float(np.mean(np.abs(t90_arr))) if t90_arr.size else float("inf")

    return {
        "success": True,
        "metrics": {
            "RMSE_alpha": rmse_alpha,
            "RMSE_torque": rmse_torque,
            "T90_MAE_s": t90_mae,
            "T90_BIAS_s": float(np.mean(t90_arr)) if t90_arr.size else None,
            "T90_count": int(t90_arr.size),
        },
        "curves": curves_eval,
    }


def compare_models(dataset, reference_temperature_k=None, fit_method="least_squares"):
    prepared = _prepare_dataset(dataset)
    fit_curves = _to_fit_curves(prepared)
    if reference_temperature_k is None:
        reference_temperature_k = float(np.median([c["temperature_K"] for c in prepared]))

    base_comparison = compare_model_families(
        curves=fit_curves,
        reference_temperature_k=reference_temperature_k,
        fit_method=fit_method,
        candidate_families=AUTO_COMPARE_MODEL_FAMILIES,
    )

    candidates = {}
    for family, candidate in (base_comparison.get("candidates") or {}).items():
        eval_payload = _evaluate_candidate(prepared, candidate, reference_temperature_k=reference_temperature_k)
        merged = dict(candidate)
        merged["evaluation"] = eval_payload
        merged["metrics"] = dict(eval_payload.get("metrics") or {})
        candidates[family] = merged

    best_family = base_comparison.get("best_model_family")
    if best_family not in candidates and candidates:
        best_family = min(
            candidates.keys(),
            key=lambda fam: (
                float(candidates[fam]["metrics"].get("RMSE_alpha", float("inf"))),
                float(candidates[fam]["metrics"].get("T90_MAE_s", float("inf"))),
                float(candidates[fam].get("parameter_stability_penalty", float("inf"))),
            ),
        )

    return {
        "success": bool(base_comparison.get("success")),
        "message": base_comparison.get("message"),
        "fit_method": fit_method,
        "reference_temperature_K": float(reference_temperature_k),
        "dataset_curves": len(prepared),
        "best_model_family": best_family,
        "candidates": candidates,
        "selection_criteria": base_comparison.get("selection_criteria") or [],
        "prepared_dataset": prepared,
    }


def run_backtest(dataset, model_family, reference_temperature_k=None, fit_method="least_squares"):
    prepared = _prepare_dataset(dataset)
    fit_curves = _to_fit_curves(prepared)
    family = normalize_model_family(model_family)
    if reference_temperature_k is None:
        reference_temperature_k = float(np.median([c["temperature_K"] for c in prepared]))

    params_rows = []
    for curve in fit_curves:
        fit = fit_model_parameters(
            curves=[curve],
            model_family=family,
            reference_temperature_k=reference_temperature_k,
            fit_method=fit_method,
        )
        if not fit.get("success"):
            continue
        params = dict(fit.get("model_parameters") or {})
        k_val = _safe_float(params.get("k_ref", params.get("k0")), default=None)
        ea_val = _safe_float(params.get("Ea"), default=None)
        n_val = _safe_float(params.get("n"), default=None)
        if k_val is None or ea_val is None or n_val is None:
            continue
        params_rows.append({"curve_id": curve["COD_ENSAIO"], "k": k_val, "Ea": ea_val, "n": n_val})

    summary = {}
    alerts = []
    for key in ("k", "Ea", "n"):
        vals = np.asarray([row[key] for row in params_rows], dtype=float) if params_rows else np.asarray([], dtype=float)
        if vals.size == 0:
            summary[key] = {"count": 0, "mean": None, "std": None, "cv": None}
            continue

        mean = float(np.mean(vals))
        std = float(np.std(vals, ddof=1)) if vals.size > 1 else 0.0
        cv = float(std / max(abs(mean), 1e-12))
        summary[key] = {"count": int(vals.size), "mean": mean, "std": std, "cv": cv}

        limit = BACKTEST_CV_ALERT_LIMITS.get(key)
        if limit is not None and cv > float(limit):
            alerts.append(f"Alta dispersao de {key}: CV={cv:.3f} > {limit:.3f}")

    if len(params_rows) < 2:
        alerts.append("Backtest com poucas curvas calibradas (<2).")

    return {
        "model_family": family,
        "curves_calibrated": int(len(params_rows)),
        "parameters_by_curve": params_rows,
        "dispersion_summary": summary,
        "alerts": alerts,
    }


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return str(value)


def generate_kinetic_fit_report(calibration_result, report_path=REPORT_DEFAULT_PATH):
    report = Path(report_path).expanduser()
    report.parent.mkdir(parents=True, exist_ok=True)

    best_family = calibration_result.get("best_model_family")
    candidates = calibration_result.get("candidates") or {}
    candidate_order = list(candidates.keys())
    best_candidate = candidates.get(best_family) or {}
    curves = (best_candidate.get("evaluation") or {}).get("curves") or []

    # Arrhenius diagnostic points based on T90 (real vs best model).
    arr_x = []
    arr_y_real = []
    arr_y_model = []
    for curve in curves:
        temp_k = float(curve.get("temperature_K") or 0.0)
        if temp_k <= 0:
            continue
        t90_real = None
        t90_model = None
        for row in curve.get("txx") or []:
            if abs(float(row.get("alpha_target", 0.0)) - 0.90) <= 1e-9:
                t90_real = row.get("time_real_s")
                t90_model = row.get("time_model_s")
                break
        if t90_real is None or t90_model is None:
            continue
        if float(t90_real) <= 0.0 or float(t90_model) <= 0.0:
            continue
        arr_x.append(float(1.0 / temp_k))
        arr_y_real.append(float(np.log(float(t90_real))))
        arr_y_model.append(float(np.log(float(t90_model))))

    payload_json = json.dumps(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "best_model_family": best_family,
            "reference_temperature_K": calibration_result.get("reference_temperature_K"),
            "candidate_order": candidate_order,
            "candidates": candidates,
            "curves": curves,
            "arrhenius": {"x": arr_x, "y_real": arr_y_real, "y_model": arr_y_model},
            "backtest": calibration_result.get("backtest") or {},
        },
        ensure_ascii=False,
        default=_json_default,
    )

    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relatorio de Recalibracao Cinetica</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; color: #1f2937; }}
    h1, h2 {{ margin-bottom: 0.35rem; }}
    .muted {{ color: #6b7280; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(300px,1fr)); gap: 16px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; background: #fff; }}
    .metric-row {{ display: flex; justify-content: space-between; margin: 4px 0; }}
    .plot {{ width: 100%; height: 360px; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Relatorio Tecnico de Recalibracao Cinetica</h1>
  <p class="muted">Modelo selecionado automaticamente: <code id="bestModel"></code></p>
  <div class="grid" id="summaryCards"></div>
  <h2>Curvas reais vs modelos</h2>
  <div id="curveSections"></div>
  <h2>Diagnostico Arrhenius (ln(T90) vs 1/T)</h2>
  <div id="arrheniusPlot" class="plot"></div>
  <h2>Backtest de parametros</h2>
  <pre id="backtestBlock"></pre>
<script>
  const reportData = {payload_json};
  document.getElementById('bestModel').textContent = reportData.best_model_family || '--';
  document.getElementById('backtestBlock').textContent = JSON.stringify(reportData.backtest || {{}}, null, 2);

  const summaryEl = document.getElementById('summaryCards');
  (reportData.candidate_order || []).forEach((family) => {{
    const candidate = (reportData.candidates || {{}})[family] || {{}};
    const metrics = candidate.metrics || {{}};
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <h3>${{family}}</h3>
      <div class="metric-row"><span>RMSE_alpha</span><b>${{Number(metrics.RMSE_alpha).toFixed(6)}}</b></div>
      <div class="metric-row"><span>RMSE_torque</span><b>${{Number(metrics.RMSE_torque).toFixed(6)}}</b></div>
      <div class="metric-row"><span>T90_MAE (s)</span><b>${{Number(metrics.T90_MAE_s).toFixed(3)}}</b></div>
      <div class="metric-row"><span>Estabilidade</span><b>${{Number(candidate.parameter_stability_penalty || 0).toFixed(3)}}</b></div>
    `;
    summaryEl.appendChild(card);
  }});

  const sectionsEl = document.getElementById('curveSections');
  (reportData.curves || []).forEach((curve, idx) => {{
    const alphaId = `alpha_plot_${{idx}}`;
    const torqueId = `torque_plot_${{idx}}`;
    const block = document.createElement('div');
    block.className = 'card';
    block.innerHTML = `
      <h3>${{curve.curve_id}} - ${{Number(curve.temperature_C).toFixed(1)}} C</h3>
      <div id="${{alphaId}}" class="plot"></div>
      <div id="${{torqueId}}" class="plot"></div>
    `;
    sectionsEl.appendChild(block);

    Plotly.newPlot(alphaId, [
      {{ x: curve.t_rel, y: curve.alpha_real, mode: 'lines', name: 'alpha real', line: {{width: 2}} }},
      {{ x: curve.t_rel, y: curve.alpha_model, mode: 'lines', name: 'alpha modelo', line: {{width: 2, dash: 'dot'}} }}
    ], {{
      title: 'alpha(t)',
      xaxis: {{ title: 't relativo (s)' }},
      yaxis: {{ title: 'alpha', range: [0, 1] }}
    }}, {{ responsive: true, displaylogo: false }});

    Plotly.newPlot(torqueId, [
      {{ x: curve.t_rel, y: curve.torque_real, mode: 'lines', name: 'torque real', line: {{width: 2}} }},
      {{ x: curve.t_rel, y: curve.torque_model, mode: 'lines', name: 'torque modelo', line: {{width: 2, dash: 'dot'}} }}
    ], {{
      title: 'torque(t)',
      xaxis: {{ title: 't relativo (s)' }},
      yaxis: {{ title: 'torque' }}
    }}, {{ responsive: true, displaylogo: false }});
  }});

  Plotly.newPlot('arrheniusPlot', [
    {{ x: reportData.arrhenius.x, y: reportData.arrhenius.y_real, mode: 'markers+lines', name: 'ln(T90) real' }},
    {{ x: reportData.arrhenius.x, y: reportData.arrhenius.y_model, mode: 'markers+lines', name: 'ln(T90) modelo' }}
  ], {{
    title: 'Arrhenius diagnostico',
    xaxis: {{ title: '1/T (1/K)' }},
    yaxis: {{ title: 'ln(T90)' }}
  }}, {{ responsive: true, displaylogo: false }});
</script>
</body>
</html>
"""
    report.write_text(html, encoding="utf-8")
    return str(report.resolve())


def run_kinetic_recalibration_pipeline(
    dataset_path,
    report_path=REPORT_DEFAULT_PATH,
    reference_temperature_k=None,
    fit_method="least_squares",
):
    dataset = load_reometry_dataset(dataset_path)
    comparison = compare_models(
        dataset=dataset,
        reference_temperature_k=reference_temperature_k,
        fit_method=fit_method,
    )
    best_family = comparison.get("best_model_family")
    backtest = run_backtest(
        dataset=dataset,
        model_family=best_family or MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
        reference_temperature_k=comparison.get("reference_temperature_K"),
        fit_method=fit_method,
    )
    comparison["backtest"] = backtest
    comparison["report_path"] = generate_kinetic_fit_report(comparison, report_path=report_path)
    comparison["dataset_path"] = str(Path(dataset_path).expanduser().resolve())
    comparison["generated_at"] = datetime.now(timezone.utc).isoformat()
    return comparison


__all__ = [
    "R_GAS",
    "estimate_ml_mh",
    "compute_alpha_from_torque",
    "compute_equivalent_time",
    "predict_alpha_pinheiro",
    "predict_alpha_edo",
    "compare_models",
    "run_backtest",
    "generate_kinetic_fit_report",
    "run_kinetic_recalibration_pipeline",
]
