import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
pytest.importorskip("scipy")

from services import kinetic_model_service as kms
from services.kinetic_calibration_pipeline_service import (
    compare_models,
    compute_alpha_from_torque,
    compute_equivalent_time,
    predict_alpha_pinheiro,
    run_kinetic_recalibration_pipeline,
)
from services.reometry_dataset_service import load_reometry_dataset


def _build_pinheiro_synthetic_dataset():
    params = {"k_ref": 1.4e-4, "Ea": 76000.0, "n": 2.15}
    t_ref = 433.15
    dataset = []
    for idx, temp_k in enumerate([413.15, 433.15, 453.15], start=1):
        time = np.linspace(0.0, 1600.0, 321, dtype=float)
        temp_profile = np.full_like(time, fill_value=temp_k, dtype=float)
        alpha = kms.predict_alpha_isothermal(
            time_s=time,
            temperature_k=temp_profile,
            params=params,
            model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
            reference_temperature_k=t_ref,
        )
        ml = 2.0 + (0.1 * idx)
        mh = 9.0 + (0.1 * idx)
        torque = ml + (alpha * (mh - ml))
        torque = torque + (0.01 * np.sin(time / 120.0))
        dataset.append(
            {
                "curve_id": f"C{idx}",
                "temperature_K": float(temp_k),
                "temperature_C": float(temp_k - 273.15),
                "time": time.tolist(),
                "torque": torque.tolist(),
            }
        )
    return dataset


def test_load_reometry_dataset_json_multitemperature(tmp_path):
    dataset = _build_pinheiro_synthetic_dataset()
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    loaded = load_reometry_dataset(path)
    assert len(loaded) == 3
    assert all("temperature_K" in c for c in loaded)
    assert all("time" in c for c in loaded)
    assert all("torque" in c for c in loaded)


def test_compute_alpha_from_torque_clip_bounds():
    torque = np.asarray([1.0, 2.0, 2.5, 3.0, 4.0], dtype=float)
    alpha = compute_alpha_from_torque(torque, ml=2.0, mh=3.0)
    assert np.all(alpha >= 0.0)
    assert np.all(alpha <= 1.0)
    assert alpha[0] == pytest.approx(0.0)
    assert alpha[-1] == pytest.approx(1.0)
    assert alpha[2] == pytest.approx(0.5)


def test_compute_equivalent_time_thermal_consistency():
    time = np.linspace(0.0, 100.0, 101, dtype=float)
    ea = 70000.0
    t_ref = 433.15
    t_low = np.full_like(time, 413.15)
    t_high = np.full_like(time, 453.15)

    teq_low = compute_equivalent_time(time, t_low, ea, t_ref)
    teq_ref = compute_equivalent_time(time, np.full_like(time, t_ref), ea, t_ref)
    teq_high = compute_equivalent_time(time, t_high, ea, t_ref)

    assert teq_high[-1] > teq_ref[-1]
    assert teq_ref[-1] > teq_low[-1]


def test_predict_alpha_pinheiro_monotonic_and_bounded():
    dataset = _build_pinheiro_synthetic_dataset()
    params = {"k_ref": 1.4e-4, "Ea": 76000.0, "n": 2.15}

    predicted = predict_alpha_pinheiro(params=params, dataset=dataset, reference_temperature_k=433.15)
    assert len(predicted) == len(dataset)
    for curve in predicted:
        alpha = np.asarray(curve["alpha_model"], dtype=float)
        assert np.all(alpha >= 0.0)
        assert np.all(alpha <= 1.0)
        assert np.all(np.diff(alpha) >= -1e-9)


def test_compare_models_selects_pinheiro_for_pinheiro_synthetic():
    dataset = _build_pinheiro_synthetic_dataset()
    comparison = compare_models(dataset=dataset, reference_temperature_k=433.15)

    assert comparison["best_model_family"] == kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1
    pinheiro = comparison["candidates"][kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1]["metrics"]["RMSE_alpha"]
    edo = comparison["candidates"][kms.MODEL_FAMILY_EDO_ORDER_N_V1]["metrics"]["RMSE_alpha"]
    assert pinheiro <= edo


def test_pipeline_generates_report_and_backtest(tmp_path):
    dataset = _build_pinheiro_synthetic_dataset()
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    report_path = tmp_path / "kinetic_fit_report.html"

    result = run_kinetic_recalibration_pipeline(
        dataset_path=dataset_path,
        report_path=report_path,
        reference_temperature_k=433.15,
    )

    assert Path(result["report_path"]).exists()
    html = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Relatorio Tecnico de Recalibracao Cinetica" in html
    assert "backtest" in result
    assert "dispersion_summary" in result["backtest"]
