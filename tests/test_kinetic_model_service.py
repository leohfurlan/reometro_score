import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _build_kamal_synthetic_curves(rng, params, reference_temperature_k):
    curves = []
    temps_c = [155.0, 170.0, 185.0]
    for idx, temp_c in enumerate(temps_c, start=1):
        time = np.linspace(0.0, 900.0, 451, dtype=float)
        temp_k = np.full_like(time, fill_value=temp_c + 273.15)
        alpha_clean = kms.predict_alpha_nonisothermal(
            time_s=time,
            temperature_k=temp_k,
            params=params,
            model_family=kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
            reference_temperature_k=reference_temperature_k,
        )
        alpha_noisy = np.clip(alpha_clean + rng.normal(0.0, 0.003, size=alpha_clean.shape), 0.0, 1.0)
        ml = 2.0
        mh = 6.4
        torque_clean = kms.torque_from_alpha_kamal_sourour_expanded(
            alpha=alpha_noisy,
            time_s=time,
            m_min=ml,
            m_max=mh,
            params=params,
        )
        torque_noisy = torque_clean + rng.normal(0.0, 0.01, size=torque_clean.shape)

        curves.append(
            {
                "COD_ENSAIO": idx,
                "T_C": float(temp_c),
                "T_K": float(temp_c + 273.15),
                "t_rel": time,
                "tempo": time,
                "torque": torque_noisy,
                "ML": float(ml),
                "MH": float(mh),
                "alpha": kms.alpha_from_torque(torque_noisy, ml, mh),
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


def test_normalize_model_family_accepts_kamal_alias():
    assert kms.normalize_model_family("kamal_sourour_expanded") == kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1


def test_kamal_nonisothermal_prediction_is_bounded_and_monotonic():
    params = {
        "k1": 0.0025,
        "k2": 0.006,
        "Ea": 76000.0,
        "m": 1.2,
        "n": 1.45,
        "k_march": 0.03,
        "k_rev": 0.04,
        "beta_rev": 0.012,
    }
    time = np.linspace(0.0, 600.0, 601, dtype=float)
    temp_k = np.linspace(413.15, 453.15, 601, dtype=float)
    alpha = kms.predict_alpha_nonisothermal(
        time_s=time,
        temperature_k=temp_k,
        params=params,
        model_family=kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
        reference_temperature_k=433.15,
    )
    assert np.isfinite(alpha).all()
    assert float(np.min(alpha)) >= 0.0
    assert float(np.max(alpha)) <= 1.0
    assert np.all(np.diff(alpha) >= -1e-10)


def test_kamal_torque_model_applies_marching_and_reversion():
    params = {
        "k1": 0.002,
        "k2": 0.006,
        "Ea": 78000.0,
        "m": 1.0,
        "n": 1.3,
        "k_march": 0.05,
        "k_rev": 0.20,
        "beta_rev": 0.01,
    }
    time = np.linspace(0.0, 400.0, 401, dtype=float)
    alpha = np.linspace(0.0, 0.95, 401, dtype=float)
    torque = kms.torque_from_alpha_kamal_sourour_expanded(alpha, time, 2.0, 6.0, params)
    linear_only = kms.torque_from_alpha(alpha, 2.0, 6.0)
    assert torque.shape == linear_only.shape
    assert float(torque[-1]) != pytest.approx(float(linear_only[-1]), rel=1e-9, abs=1e-9)


def test_kamal_induction_shift_blocks_alpha_before_t_ind():
    params = {
        "k1": 0.0025,
        "k2": 0.0065,
        "Ea": 76000.0,
        "m": 1.1,
        "n": 1.4,
        "A_ind": 8000.0,
        "E_ind": 50000.0,
    }
    time = np.linspace(0.0, 350.0, 351, dtype=float)
    temp_k = np.full_like(time, 443.15, dtype=float)

    t_ind = kms.KamalSourourExpandedEngine.induction_time_s(float(temp_k[0]), params)
    alpha = kms.predict_alpha_nonisothermal(
        time_s=time,
        temperature_k=temp_k,
        params=params,
        model_family=kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
        reference_temperature_k=433.15,
    )

    pre_mask = time < max(t_ind - 1e-6, 0.0)
    assert t_ind > 0.0
    assert np.max(alpha[pre_mask]) <= 1e-9
    assert float(alpha[-1]) > 0.0


def test_kamal_torque_uses_t_rev_onset_equation():
    params = {
        "k1": 0.002,
        "k2": 0.006,
        "Ea": 78000.0,
        "m": 1.0,
        "n": 1.3,
        "k_march": 0.04,
        "k_rev": 0.20,
        "beta_rev": 0.05,
        "t_rev": 120.0,
    }
    time = np.linspace(0.0, 220.0, 221, dtype=float)
    alpha = np.full_like(time, fill_value=0.8, dtype=float)
    torque = kms.torque_from_alpha_kamal_sourour_expanded(alpha, time, 2.0, 6.0, params)

    base = kms.torque_from_alpha(alpha, 2.0, 6.0)
    expected = base + (params["k_march"] * alpha * time) - (
        params["k_rev"] * (1.0 - np.exp(-params["beta_rev"] * np.maximum(time - params["t_rev"], 0.0)))
    )
    assert np.allclose(torque, expected, atol=1e-12)

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


def test_fit_synthetic_kamal_sourour_expanded():
    rng = np.random.default_rng(2026031701)
    reference_temperature_k = 433.15
    true_params = {
        "k1": 0.0028,
        "k2": 0.0075,
        "Ea": 78000.0,
        "m": 1.20,
        "n": 1.55,
        "k_march": 0.035,
        "k_rev": 0.040,
        "beta_rev": 0.01,
    }
    curves = _build_kamal_synthetic_curves(rng, true_params, reference_temperature_k)

    fit = kms.fit_model_parameters(
        curves,
        model_family=kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
        reference_temperature_k=reference_temperature_k,
    )

    assert fit["success"] is True
    assert fit["model_family"] == kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1
    assert fit["fit_method"] == "least_squares"
    assert fit["k1"] > 0.0
    assert fit["k2"] > 0.0
    assert 0.1 <= float(fit["m"]) <= 3.0
    assert 0.5 <= float(fit["n"]) <= 3.0
    assert 0.0 <= float(fit["k_march"]) <= 0.5


def test_fit_auto_selects_best_model_for_pinheiro_synthetic():
    rng = np.random.default_rng(20260317)
    reference_temperature_k = 433.15
    true_params = {"k_ref": 1.5e-4, "Ea": 76000.0, "n": 2.0}
    curves = _build_synthetic_curves(rng, true_params, reference_temperature_k)

    fit = kms.fit_model_parameters(
        curves,
        model_family=kms.MODEL_FAMILY_AUTO,
        reference_temperature_k=reference_temperature_k,
    )

    assert fit["success"] is True
    assert fit["requested_model_family"] == kms.MODEL_FAMILY_AUTO
    assert fit["model_family"] == kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1
    assert fit["auto_selected_model_family"] == kms.MODEL_FAMILY_PINHEIRO_SIGMOIDAL_TEQ_V1
    assert "auto_model_selection" in fit


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


def _build_leroy_synthetic_curves():
    params = {
        "Av1": 0.010,
        "Av2": 0.045,
        "Ev": 90000.0,
        "X": 0.68,
        "Ar": 0.25,
        "Er": 135000.0,
    }
    curves = []
    for idx, temp_c in enumerate([155.0, 170.0, 185.0], start=1):
        time = np.linspace(0.0, 900.0, 361, dtype=float)
        temp_k = np.full_like(time, fill_value=temp_c + 273.15, dtype=float)
        states = kms.integrate_leroy2013_states(time, temp_k, params)
        alpha = np.asarray(states["alpha_total"], dtype=float)
        curves.append(
            {
                "COD_ENSAIO": idx,
                "T_C": float(temp_c),
                "T_K": float(temp_c + 273.15),
                "t_rel": time,
                "alpha": np.clip(alpha + 0.002 * np.sin(time / 40.0), 0.0, 1.0),
            }
        )
    return curves


def test_leroy2013_integration_monotonic_and_bounded():
    params = {
        "Av1": 0.012,
        "Av2": 0.040,
        "Ev": 90000.0,
        "X": 0.65,
        "Ar": 0.15,
        "Er": 130000.0,
    }
    time = np.linspace(0.0, 1200.0, 601, dtype=float)
    temp = np.full_like(time, fill_value=453.15, dtype=float)
    states = kms.integrate_leroy2013_states(time, temp, params)

    alpha_v = np.asarray(states["alpha_v"], dtype=float)
    alpha_total = np.asarray(states["alpha_total"], dtype=float)
    assert np.all(np.diff(alpha_v) >= -1e-10)
    assert float(np.min(alpha_total)) >= 0.0
    assert float(np.max(alpha_total)) <= 1.0


def test_leroy2013_reversion_possible_at_high_temperature():
    params = {
        "Av1": 5000.0,
        "Av2": 7000.0,
        "Ev": 42000.0,
        "X": 0.40,
        "Ar": 1.0e6,
        "Er": 85000.0,
    }
    time = np.linspace(0.0, 1800.0, 901, dtype=float)
    temp = np.full_like(time, fill_value=483.15, dtype=float)
    states = kms.integrate_leroy2013_states(time, temp, params)
    alpha_total = np.asarray(states["alpha_total"], dtype=float)

    assert float(np.max(alpha_total)) > float(alpha_total[-1]) + 1e-3


def test_leroy2013_hierarchical_seed_stage1_stage2_pipeline():
    curves = _build_leroy_synthetic_curves()
    seed = kms.fit_leroy2013_isothermal_seed(curves)
    assert seed["success"] is True
    assert len(seed["seed_rows"]) == len(curves)

    stage1 = kms.fit_leroy2013_global_stage1(curves, seed_statistics=seed["seed_statistics"])
    assert stage1["success"] is True
    assert stage1["calibration_stage"] == "stage1"

    stage2 = kms.fit_leroy2013_global_stage2(curves, stage1_result=stage1)
    assert stage2["calibration_stage_selected"] in {"stage1", "stage2_ev", "stage2_ev_er"}

    fit = kms.fit_model_parameters(curves, model_family=kms.MODEL_FAMILY_LEROY2013_CONTINUOUS_V1)
    assert fit["success"] is True
    assert fit["calibration_strategy"] == "hierarchical_v1"
    assert fit["calibration_stage_selected"] in {"stage1", "stage2_ev", "stage2_ev_er"}
    assert "identifiability" in fit


def test_leroy2013_stage2_fallback_with_insufficient_temperature_span():
    curves = _build_leroy_synthetic_curves()[:2]
    seed = kms.fit_leroy2013_isothermal_seed(curves)
    stage1 = kms.fit_leroy2013_global_stage1(curves, seed_statistics=seed["seed_statistics"])
    stage2 = kms.fit_leroy2013_global_stage2(curves, stage1_result=stage1)

    assert stage2["calibration_stage_selected"] == "stage1"


def test_identifiability_flags_high_correlation_and_bounds():
    jac_col = np.linspace(0.1, 1.0, 40)
    jac = np.column_stack([jac_col, jac_col * 0.999999])
    lsq = SimpleNamespace(x=np.asarray([0.999, 0.001], dtype=float), jac=jac)
    ident = kms.evaluate_parameter_identifiability(
        lsq,
        ["p1", "p2"],
        bounds=(np.asarray([0.0, 0.0]), np.asarray([1.0, 1.0])),
    )

    assert ident["status"] in {"warning", "poor"}
    assert len(ident["high_correlation_pairs"]) >= 1
    assert len(ident["parameters_near_bounds"]) >= 1


def test_leroy_av2_bound_extended_to_40000():
    params = kms.extract_model_parameters(
        {
            "model_family": kms.MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
            "model_parameters": {
                "Av1": 1.0,
                "Av2": 999999.0,
                "Ev": 90000.0,
                "X": 0.6,
                "Ar": 0.1,
                "Er": 140000.0,
            },
        },
        model_family=kms.MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
    )
    assert abs(float(params["Av2"]) - 40000.0) < 1e-9


def test_extract_and_update_kamal_parameters():
    payload = {
        "model_family": kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1,
        "model_parameters": {
            "k1": 0.002,
            "k2": 0.005,
            "Ea": 77000.0,
            "m": 1.1,
            "n": 1.4,
            "k_march": 0.02,
            "k_rev": 0.03,
            "beta_rev": 0.01,
        },
    }

    extracted = kms.extract_model_parameters(payload, model_family=kms.MODEL_FAMILY_KAMAL_SOUROUR_EXPANDED_V1)
    assert extracted["k1"] > 0.0
    assert extracted["k2"] > 0.0
    assert 0.1 <= extracted["m"] <= 3.0
    assert 0.5 <= extracted["n"] <= 3.0
    assert 0.0 <= extracted["k_march"] <= 0.5

    updated = kms.update_model_parameters(
        {
            **payload,
            "fit_id": "fit_kamal_update",
        },
        parameter_updates={"k_march": 0.04, "m": 1.3},
    )
    assert updated["model_parameters"]["k_march"] == pytest.approx(0.04, rel=1e-9, abs=1e-9)
    assert updated["model_parameters"]["m"] == pytest.approx(1.3, rel=1e-9, abs=1e-9)


def test_saved_leroy_fit_payload_round_trip_preserves_hierarchical_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "OUT_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    payload = {
        "fit_id": "fit_leroy_roundtrip",
        "created_at": "2026-03-17T12:00:00",
        "success": True,
        "message": "ok",
        "model_family": "leroy2013_continuous_v1",
        "model_version": "v1",
        "fit_method": "least_squares",
        "reference_temperature_K": 433.15,
        "model_parameters": {
            "Av1": 0.012,
            "Av2": 0.042,
            "Ev": 90000.0,
            "X": 0.66,
            "Ar": 0.25,
            "Er": 130000.0,
        },
        "Av1": 0.012,
        "Av2": 0.042,
        "Ev": 90000.0,
        "X": 0.66,
        "Ar": 0.25,
        "Er": 130000.0,
        "calibration_strategy": "hierarchical_v1",
        "calibration_stage_selected": "stage2_ev",
        "seed_statistics": {"Av1_median": 0.011, "Av2_median": 0.041, "X_median": 0.65, "Ar_median": 0.2},
        "identifiability": {
            "status": "warning",
            "parameters_near_bounds": [],
            "high_correlation_pairs": [],
            "condition_indicator": 1e8,
        },
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
    loaded = ks.load_fit_payload("fit_leroy_roundtrip")

    assert loaded["model_family"] == "leroy2013_continuous_v1"
    assert loaded["calibration_strategy"] == "hierarchical_v1"
    assert loaded["calibration_stage_selected"] == "stage2_ev"
    assert "seed_statistics" in loaded
    assert "identifiability" in loaded
    assert "alpha_v_model" in loaded["curves"][0]
    assert "alpha_unstable_model" in loaded["curves"][0]
