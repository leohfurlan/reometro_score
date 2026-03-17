import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import kinetics_service as ks


def _mock_curve(cod_ensaio, temp_c, scorch_time_s):
    tempo = np.linspace(0.0, 40.0, 81, dtype=float)
    # Quadratic valley around the desired scorch point.
    torque = 6.0 + 0.01 * np.square(tempo - float(scorch_time_s))
    t_rel = np.maximum(tempo - 2.0, 0.0)
    alpha = np.clip((tempo / np.max(tempo)), 0.0, 1.0)
    return {
        "COD_ENSAIO": int(cod_ensaio),
        "T_C": float(temp_c),
        "T_K": float(temp_c) + 273.15,
        "ML": float(np.min(torque)),
        "MH": float(np.max(torque)),
        "TMINTEMPO": 2.0,
        "tempo": tempo.tolist(),
        "torque": torque.tolist(),
        "t_rel": np.asarray(t_rel, dtype=float),
        "alpha": np.asarray(alpha, dtype=float),
    }


def test_run_fit_populates_induction_payload_when_arrhenius_is_reliable(monkeypatch):
    curves = [
        _mock_curve(101, 150.0, scorch_time_s=22.0),
        _mock_curve(102, 165.0, scorch_time_s=15.0),
        _mock_curve(103, 180.0, scorch_time_s=10.0),
    ]

    monkeypatch.setattr(ks, "get_curve_points", lambda _ids: [])
    monkeypatch.setattr(ks, "_group_curves", lambda _rows: curves)
    monkeypatch.setattr(
        ks,
        "fit_model_parameters",
        lambda **_kwargs: {
            "success": True,
            "message": "kinetic fit mocked",
            "model_family": "pinheiro_sigmoidal_teq_v1",
            "model_version": "v1",
            "fit_method": "least_squares",
            "reference_temperature_K": 433.15,
            "model_parameters": {
                "k_ref": 1.0e-3,
                "k": 1.0e-3,
                "Ea": 8.0e4,
                "n": 1.2,
            },
            "k_ref": 1.0e-3,
            "Ea": 8.0e4,
            "n": 1.2,
            "rmse_alpha": 0.1,
        },
    )

    payload = ks.run_fit([101, 102, 103])

    assert payload["model_family"] == "pinheiro_sigmoidal_teq_v1"
    assert payload["model_version"] == "v1"
    assert payload["fit_method"] == "least_squares"
    assert payload["reference_temperature_K"] > 0.0
    assert payload["cure_completion_rule"] == "alpha_practical_0_99"
    assert payload["induction_model_version"] == ks.INDUCTION_MODEL_VERSION
    assert payload["induction"]["source"] == ks.INDUCTION_SOURCE
    assert payload["induction"]["enabled"] is True
    assert payload["A_ind"] is not None
    assert payload["E_ind"] is not None
    assert payload["induction_confidence"] in {"high", "medium", "low"}
    assert payload["induction_fit_quality"] in {"high", "medium", "low"}
    assert payload["temperature_validity_range"]["span_c"] > 0.0
    assert payload["induction_model_regime"] == "single_arrhenius"

    samples = payload["induction"]["samples"]
    assert len(samples) == 3
    required_fields = {"T_C", "T_K", "t_scorch", "torque_min", "ln_t_scorch", "inv_T", "status"}
    for row in samples:
        assert required_fields.issubset(set(row.keys()))


def test_run_fit_marks_low_confidence_when_induction_points_are_insufficient(monkeypatch):
    curves = [
        _mock_curve(201, 150.0, scorch_time_s=22.0),
        _mock_curve(202, 165.0, scorch_time_s=15.0),
    ]

    monkeypatch.setattr(ks, "get_curve_points", lambda _ids: [])
    monkeypatch.setattr(ks, "_group_curves", lambda _rows: curves)
    monkeypatch.setattr(
        ks,
        "fit_model_parameters",
        lambda **_kwargs: {
            "success": False,
            "message": "kinetic fit mocked",
            "model_family": "pinheiro_sigmoidal_teq_v1",
            "model_version": "v1",
            "fit_method": "least_squares",
            "reference_temperature_K": 433.15,
            "model_parameters": {
                "k_ref": 1.0e-3,
                "k": 1.0e-3,
                "Ea": 8.0e4,
                "n": 1.2,
            },
            "k_ref": 1.0e-3,
            "Ea": 8.0e4,
            "n": 1.2,
            "rmse_alpha": 0.1,
        },
    )

    payload = ks.run_fit([201, 202])

    assert payload["induction"]["enabled"] is False
    assert payload["A_ind"] is None
    assert payload["E_ind"] is None
    assert payload["induction_confidence"] == "low"
    assert payload["induction_fit_quality"] == "insufficient_data"
    assert payload["temperature_validity_range"] is None
    assert payload["induction_model_regime"] == "unavailable"


def test_group_curves_uses_robust_ml_mh_and_clamps_alpha():
    tempo = np.linspace(0.0, 120.0, 121, dtype=float)
    torque = 3.0 + (5.5 / (1.0 + np.exp(-(tempo - 50.0) / 7.0)))
    torque[:8] = torque[:8] + np.linspace(-0.3, 0.2, 8)
    torque[-12:] = torque[-12:] + np.linspace(-0.15, 0.1, 12)

    rows = [
        {
            "COD_ENSAIO": 901,
            "TEMP_PLATO_INF": 170.0,
            "TMINTEMPO": 0.0,
            "TMINTORQUE": -100.0,
            "TMAXTORQUE": 1000.0,
            "TEMPO": float(t),
            "TORQUE": float(m),
        }
        for t, m in zip(tempo, torque)
    ]

    curves = ks._group_curves(rows)
    assert len(curves) == 1
    curve = curves[0]

    alpha = np.asarray(curve["alpha"], dtype=float)
    assert curve["ML_raw"] == -100.0
    assert curve["MH_raw"] == 1000.0
    assert curve["MH"] > curve["ML"]
    assert np.all(alpha >= 0.0)
    assert np.all(alpha <= 1.0)


def test_extract_experimental_markers_includes_regions_and_txx():
    curve = _mock_curve(777, 170.0, scorch_time_s=12.0)
    markers_by_curve = ks.extract_experimental_markers([curve])
    markers = markers_by_curve[curve["COD_ENSAIO"]]

    assert "T10_s" in markers
    assert "T30_s" in markers
    assert "T50_s" in markers
    assert "T90_s" in markers
    assert "T95_s" in markers
    assert "T99_s" in markers
    assert "scorch_time_s" in markers
    assert "regions" in markers
    assert "growth" in markers["regions"]
    assert "possible_reversion" in markers["regions"]


def test_run_fit_leroy_persists_hierarchical_metadata_and_states(monkeypatch):
    curves = [
        _mock_curve(801, 150.0, scorch_time_s=20.0),
        _mock_curve(802, 170.0, scorch_time_s=12.0),
        _mock_curve(803, 185.0, scorch_time_s=8.0),
    ]

    monkeypatch.setattr(ks, "get_curve_points", lambda _ids: [])
    monkeypatch.setattr(ks, "_group_curves", lambda _rows: curves)
    monkeypatch.setattr(
        ks,
        "fit_model_parameters",
        lambda **_kwargs: {
            "success": True,
            "message": "leroy fit mocked",
            "model_family": "leroy2013_continuous_v1",
            "model_version": "v1",
            "fit_method": "least_squares",
            "reference_temperature_K": 433.15,
            "model_parameters": {
                "Av1": 0.015,
                "Av2": 0.045,
                "Ev": 90000.0,
                "X": 0.67,
                "Ar": 0.22,
                "Er": 140000.0,
            },
            "Av1": 0.015,
            "Av2": 0.045,
            "Ev": 90000.0,
            "X": 0.67,
            "Ar": 0.22,
            "Er": 140000.0,
            "rmse_alpha": 0.05,
            "calibration_strategy": "hierarchical_v1",
            "calibration_stage_selected": "stage1",
            "seed_statistics": {"Av1_median": 0.014, "Av2_median": 0.042, "X_median": 0.66, "Ar_median": 0.2},
            "identifiability": {"status": "warning", "parameters_near_bounds": [], "high_correlation_pairs": [], "condition_indicator": 1e8},
        },
    )

    payload = ks.run_fit([801, 802, 803], model_family="leroy2013_continuous_v1")

    assert payload["model_family"] == "leroy2013_continuous_v1"
    assert payload["calibration_strategy"] == "hierarchical_v1"
    assert payload["calibration_stage_selected"] == "stage1"
    assert "experimental_markers" in payload
    assert "identifiability" in payload
    assert "seed_statistics" in payload
    assert "alpha_v_model" in payload["curves"][0]
    assert "alpha_unstable_model" in payload["curves"][0]


def test_run_fit_kamal_persists_marching_reversion_parameters(monkeypatch):
    curves = [
        _mock_curve(901, 150.0, scorch_time_s=20.0),
        _mock_curve(902, 170.0, scorch_time_s=12.0),
        _mock_curve(903, 185.0, scorch_time_s=8.0),
    ]

    monkeypatch.setattr(ks, "get_curve_points", lambda _ids: [])
    monkeypatch.setattr(ks, "_group_curves", lambda _rows: curves)
    monkeypatch.setattr(
        ks,
        "fit_model_parameters",
        lambda **_kwargs: {
            "success": True,
            "message": "kamal fit mocked",
            "model_family": "kamal_sourour_expanded_v1",
            "model_version": "v1",
            "fit_method": "least_squares",
            "reference_temperature_K": 433.15,
            "model_parameters": {
                "k1": 0.003,
                "k2": 0.008,
                "k_ref": 0.003,
                "k0": 0.003,
                "Ea": 79000.0,
                "m": 1.2,
                "n": 1.5,
                "k_march": 0.03,
                "k_rev": 0.02,
                "beta_rev": 0.01,
            },
            "k1": 0.003,
            "k2": 0.008,
            "Ea": 79000.0,
            "m": 1.2,
            "n": 1.5,
            "k_march": 0.03,
            "k_rev": 0.02,
            "beta_rev": 0.01,
            "rmse_alpha": 0.05,
        },
    )

    payload = ks.run_fit([901, 902, 903], model_family="kamal_sourour_expanded_v1")

    assert payload["model_family"] == "kamal_sourour_expanded_v1"
    assert abs(float(payload["k1"]) - 0.003) < 1e-12
    assert abs(float(payload["k2"]) - 0.008) < 1e-12
    assert abs(float(payload["k_march"]) - 0.03) < 1e-12
    assert abs(float(payload["k_rev"]) - 0.02) < 1e-12
    assert "torque_model" in payload["curves"][0]
