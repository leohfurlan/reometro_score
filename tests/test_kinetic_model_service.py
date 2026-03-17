import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
pytest.importorskip("scipy")

from services import kinetic_model_service as kms
from services import kinetics_service as ks


def _build_synthetic_curves(rng, params, reference_temperature_k):
    curves = []
    temps_c = [155.0, 170.0, 185.0]
    for idx, temp_c in enumerate(temps_c, start=1):
        time = np.linspace(0.0, 600.0, 301, dtype=float)
        temp_k = np.full_like(time, fill_value=temp_c + 273.15)
        alpha_clean = kms.predict_alpha_isothermal(
            time_s=time,
            temperature_k=temp_k,
            params=params,
            model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
            reference_temperature_k=reference_temperature_k,
        )
        alpha_noisy = np.clip(alpha_clean + rng.normal(0.0, 0.006, size=alpha_clean.shape), 0.0, 1.0)
        curves.append(
            {
                "COD_ENSAIO": idx,
                "T_C": float(temp_c),
                "T_K": float(temp_c + 273.15),
                "t_rel": time,
                "alpha": alpha_noisy,
            }
        )
    return curves


def test_default_model_family_is_edo_order_n_v1():
    assert kms.DEFAULT_MODEL_FAMILY == kms.MODEL_FAMILY_EDO_ORDER_N_V1


def test_torque_alpha_roundtrip():
    torque = np.asarray([2.0, 2.5, 3.0, 4.0, 5.0], dtype=float)
    ml = 2.0
    mh = 5.0

    alpha = kms.alpha_from_torque(torque, ml, mh)
    torque_back = kms.torque_from_alpha(alpha, ml, mh)

    assert np.all(alpha >= 0.0)
    assert np.all(alpha <= 1.0)
    assert np.allclose(torque_back, torque, atol=1e-12)


def test_arrhenius_reference_factor_direction_vs_reference_temperature():
    ea = 70000.0
    t_ref = 433.15
    t_high = 453.15
    t_low = 413.15

    factor_ref = float(kms.arrhenius_reference_factor(np.asarray([t_ref], dtype=float), ea, t_ref)[0])
    factor_high = float(kms.arrhenius_reference_factor(np.asarray([t_high], dtype=float), ea, t_ref)[0])
    factor_low = float(kms.arrhenius_reference_factor(np.asarray([t_low], dtype=float), ea, t_ref)[0])

    assert factor_ref == pytest.approx(1.0, rel=1e-12, abs=1e-12)
    assert factor_high > factor_ref
    assert factor_low < factor_ref


def test_fit_synthetic_isothermal_pinheiro_sigmoidal_teq():
    rng = np.random.default_rng(12345)
    reference_temperature_k = 433.15
    true_params = {"k_ref": 1.6e-4, "Ea": 82000.0, "n": 1.85}
    curves = _build_synthetic_curves(rng, true_params, reference_temperature_k)

    fit = kms.fit_model_parameters(
        curves,
        model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
        reference_temperature_k=reference_temperature_k,
    )

    assert fit["success"] is True
    assert fit["model_family"] == kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1
    assert fit["fit_method"] == "least_squares"
    assert fit["k_ref"] > 0.0
    assert abs(fit["n"] - true_params["n"]) < 0.35


def test_nonisothermal_equivalent_time_and_alpha_are_monotonic():
    time = np.linspace(0.0, 600.0, 601, dtype=float)
    temp_k = np.linspace(403.15, 463.15, 601, dtype=float)
    params = {"k_ref": 1.8e-4, "Ea": 78000.0, "n": 1.6}

    t_eq = kms.compute_equivalent_time(time, temp_k, params["Ea"], 433.15)
    alpha = kms.predict_alpha_nonisothermal(
        time_s=time,
        temperature_k=temp_k,
        params=params,
        model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
        reference_temperature_k=433.15,
    )

    assert np.all(np.diff(t_eq) >= -1e-12)
    assert np.all(np.diff(alpha) >= -1e-9)
    assert float(alpha.min()) >= 0.0
    assert float(alpha.max()) <= 1.0


def test_numeric_stability_old_and_new_families():
    time = np.linspace(0.0, 900.0, 901, dtype=float)
    temp_k = np.linspace(380.0, 470.0, 901, dtype=float)

    alpha_old = kms.predict_alpha_nonisothermal(
        time_s=time,
        temperature_k=temp_k,
        params={"k0": 2.5e6, "Ea": 90000.0, "n": 1.2},
        model_family=kms.MODEL_FAMILY_EDO_ORDER_N_V1,
        reference_temperature_k=433.15,
    )
    alpha_new = kms.predict_alpha_nonisothermal(
        time_s=time,
        temperature_k=temp_k,
        params={"k_ref": 2.5e-4, "Ea": 90000.0, "n": 1.8},
        model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
        reference_temperature_k=433.15,
    )

    assert np.isfinite(alpha_old).all()
    assert np.isfinite(alpha_new).all()
    assert float(np.min(alpha_old)) >= 0.0
    assert float(np.max(alpha_old)) <= 1.0
    assert float(np.min(alpha_new)) >= 0.0
    assert float(np.max(alpha_new)) <= 1.0


def test_fit_with_realistic_fixture_smoke():
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "scorch_curves_realistic.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    curves = []
    for idx, raw_curve in enumerate((data.get("curves") or [])[:3], start=1):
        tempo = np.asarray(raw_curve.get("tempo") or [], dtype=float)
        torque = np.asarray(raw_curve.get("torque") or [], dtype=float)
        if len(tempo) < 10 or len(torque) < 10:
            continue

        ml = float(np.min(torque[: max(10, len(torque) // 10)]))
        mh = float(np.max(torque))
        alpha = kms.alpha_from_torque(torque, ml, mh)
        t_rel = np.maximum(tempo - float(tempo[0]), 0.0)

        curves.append(
            {
                "COD_ENSAIO": int(raw_curve.get("COD_ENSAIO") or idx),
                "T_C": float(raw_curve.get("T_C") or 170.0),
                "T_K": float(raw_curve.get("T_K") or (float(raw_curve.get("T_C") or 170.0) + 273.15)),
                "t_rel": t_rel,
                "alpha": alpha,
            }
        )

    fit = kms.fit_model_parameters(
        curves,
        model_family=kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1,
        reference_temperature_k=433.15,
    )

    assert "success" in fit
    assert "message" in fit
    assert fit["model_family"] == kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1


def test_saved_fit_payload_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "OUT_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    fit_id = "fit_repro_test"
    payload = {
        "fit_id": fit_id,
        "created_at": "2026-03-16T10:00:00",
        "success": True,
        "message": "ok",
        "model_family": "pinheiro_sigmoidal_teq_v1",
        "model_version": "v1",
        "fit_method": "least_squares",
        "reference_temperature_K": 433.15,
        "cure_completion_rule": "alpha_practical_0_99",
        "model_parameters": {
            "k_ref": 1.2e-4,
            "k": 1.2e-4,
            "Ea": 82000.0,
            "n": 1.9,
        },
        "k_ref": 1.2e-4,
        "k0": 1.2e-4,
        "Ea": 82000.0,
        "n": 1.9,
        "ensaios": [{"COD_ENSAIO": 1, "T_C": 170.0, "T_K": 443.15}],
        "curves": [
            {
                "COD_ENSAIO": 1,
                "T_C": 170.0,
                "T_K": 443.15,
                "ML": 1.0,
                "MH": 5.0,
                "tempo": np.linspace(0.0, 300.0, 61).tolist(),
                "torque": np.linspace(1.0, 4.8, 61).tolist(),
                "t_rel": np.linspace(0.0, 300.0, 61).tolist(),
                "alpha": np.linspace(0.0, 0.95, 61).tolist(),
            }
        ],
    }

    ks.save_fit_payload(payload)
    loaded_1 = ks.load_fit_payload(fit_id)
    loaded_2 = ks.load_fit_payload(fit_id)

    assert loaded_1["model_family"] == loaded_2["model_family"]
    assert loaded_1["model_parameters"] == loaded_2["model_parameters"]
    assert loaded_1["fit_method"] == loaded_2["fit_method"]
    assert loaded_1["reference_temperature_K"] == loaded_2["reference_temperature_K"]
    assert loaded_1["cure_completion_rule"] == loaded_2["cure_completion_rule"]
    assert np.allclose(
        np.asarray(loaded_1["curves"][0]["alpha_model"], dtype=float),
        np.asarray(loaded_2["curves"][0]["alpha_model"], dtype=float),
        atol=1e-12,
    )


def test_txx_metrics_include_t30_t50_t90_t95_t99():
    time = np.linspace(0.0, 500.0, 201, dtype=float)
    alpha_real = np.clip(time / 500.0, 0.0, 1.0)
    alpha_model = np.clip((time - 20.0) / 500.0, 0.0, 1.0)

    rows = kms.compute_txx_errors(time, alpha_real, alpha_model)
    targets = {round(float(row["alpha_target"]), 2) for row in rows}

    assert {0.30, 0.50, 0.90, 0.95, 0.99}.issubset(targets)
    assert any(row["error_s"] is not None for row in rows)
