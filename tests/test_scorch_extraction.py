import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.scorch_extraction import (
    DEFAULT_TIME_FLOOR_S,
    R_GAS,
    extract_curve_scorch,
    extract_scorch_points,
    fit_arrhenius_from_scorch,
)


def test_extract_curve_scorch_ignores_spurious_first_point_before_5s():
    time_s = np.array([0.6, 1.2, 4.8, 5.0, 6.0, 7.0], dtype=float)
    torque = np.array([0.5, 8.0, 6.0, 4.2, 3.1, 3.4], dtype=float)

    got = extract_curve_scorch(time_s, torque, time_floor=DEFAULT_TIME_FLOOR_S)

    assert got["valid"] is True
    assert np.isclose(got["t_scorch"], 6.0)
    assert np.isclose(got["torque_min"], 3.1)
    assert got["index"] == 4
    assert np.isclose(got["used_time_floor"], 5.0)


def test_extract_curve_scorch_finds_real_minimum_after_5s():
    time_s = np.array([0.0, 2.0, 4.0, 5.0, 6.0, 8.0, 10.0], dtype=float)
    torque = np.array([7.0, 6.0, 5.0, 3.8, 3.2, 2.9, 3.1], dtype=float)

    got = extract_curve_scorch(time_s, torque, time_floor=DEFAULT_TIME_FLOOR_S)

    assert got["valid"] is True
    assert got["index"] == 5
    assert np.isclose(got["t_scorch"], 8.0)
    assert np.isclose(got["torque_min"], 2.9)


def test_extract_curve_scorch_marks_curve_invalid_if_no_points_after_5s():
    time_s = np.array([0.6, 1.2, 4.9], dtype=float)
    torque = np.array([0.2, 5.0, 4.8], dtype=float)

    got = extract_curve_scorch(time_s, torque, time_floor=DEFAULT_TIME_FLOOR_S)

    assert got["valid"] is False
    assert got["t_scorch"] is None
    assert got["torque_min"] is None
    assert got["index"] is None
    assert got["reason"] == "insufficient_points_after_time_floor"
    assert np.isclose(got["used_time_floor"], 5.0)


def test_fit_arrhenius_from_scorch_returns_expected_parameters():
    intercept_true = -2.0
    slope_true = 1200.0
    temps_k = np.array([430.0, 440.0, 450.0], dtype=float)

    points = []
    for idx, temp_k in enumerate(temps_k):
        t_scorch = math.exp(intercept_true + slope_true * (1.0 / temp_k))
        points.append(
            {
                "COD_ENSAIO": f"E{idx + 1}",
                "T_K": float(temp_k),
                "valid": True,
                "t_scorch": float(t_scorch),
            }
        )

    got = fit_arrhenius_from_scorch(points)

    assert got["success"] is True
    assert np.isclose(got["intercept"], intercept_true, rtol=0.0, atol=1e-9)
    assert np.isclose(got["slope"], slope_true, rtol=0.0, atol=1e-9)
    assert np.isclose(got["A_ind"], math.exp(-intercept_true), rtol=0.0, atol=1e-9)
    assert np.isclose(got["E_ind"], slope_true * R_GAS, rtol=0.0, atol=1e-8)
    assert np.isclose(got["R2"], 1.0, rtol=0.0, atol=1e-12)
    assert len(got["points_used"]) == 3
    assert got["induction_model_regime"] == "single_arrhenius"
    assert got["temperature_validity_range"]["span_c"] > 0.0


def test_fit_arrhenius_from_scorch_reports_used_and_discarded_points_table():
    points = [
        {"COD_ENSAIO": "E1", "T_C": 155.0, "T_K": 428.15, "valid": True, "t_scorch": 48.0, "torque_min": 6.0},
        {"COD_ENSAIO": "E2", "T_C": 165.0, "T_K": 438.15, "valid": True, "t_scorch": 31.0, "torque_min": 6.3},
        {"COD_ENSAIO": "E3", "T_C": 175.0, "T_K": 448.15, "valid": True, "t_scorch": 22.0, "torque_min": 6.7},
        {
            "COD_ENSAIO": "E4",
            "T_C": 175.0,
            "T_K": 448.15,
            "valid": False,
            "t_scorch": None,
            "torque_min": None,
            "reason": "insufficient_points_after_time_floor",
        },
        {"COD_ENSAIO": "E5", "T_C": None, "T_K": None, "valid": True, "t_scorch": 25.0, "torque_min": 6.9},
    ]

    got = fit_arrhenius_from_scorch(points)

    assert got["success"] is True
    assert got["valid_points"] == 3
    assert got["discarded_points"] == 2
    assert got["total_points"] == 5
    assert got["temperature_range_k"]["span"] > 0.0
    assert got["temperature_range_c"]["span"] > 0.0

    table = got["points_table"]
    required_cols = {"T_C", "T_K", "t_scorch", "torque_min", "ln_t_scorch", "inv_T", "status", "discard_reason"}
    assert len(table) == 5
    for row in table:
        assert required_cols.issubset(set(row.keys()))

    discarded = [row for row in table if row["status"] == "discarded"]
    assert len(discarded) == 2
    assert any(row["discard_reason"] == "insufficient_points_after_time_floor" for row in discarded)
    assert any(row["discard_reason"] == "missing_or_nonfinite_temperature" for row in discarded)


def test_fit_arrhenius_from_scorch_rejects_insufficient_valid_points():
    points = [
        {"COD_ENSAIO": "E1", "T_K": 430.0, "valid": True, "t_scorch": 55.0},
        {"COD_ENSAIO": "E2", "T_K": 440.0, "valid": True, "t_scorch": 38.0},
    ]

    got = fit_arrhenius_from_scorch(points)

    assert got["success"] is False
    assert got["A_ind"] is None
    assert got["E_ind"] is None
    assert got["fit_quality"] == "insufficient_data"
    assert got["valid_points"] == 2
    assert got["criteria"]["min_points"] == 3
    assert got["temperature_validity_range"] is None
    assert got["induction_model_regime"] == "unavailable"


def test_fit_arrhenius_from_scorch_does_not_force_bad_regression():
    points = [
        {"COD_ENSAIO": "E1", "T_K": 430.0, "valid": True, "t_scorch": 30.0},
        {"COD_ENSAIO": "E2", "T_K": 440.0, "valid": True, "t_scorch": 22.0},
        {"COD_ENSAIO": "E3", "T_K": 450.0, "valid": True, "t_scorch": 25.0},
        {"COD_ENSAIO": "E4", "T_K": 460.0, "valid": True, "t_scorch": 18.0},
    ]

    got = fit_arrhenius_from_scorch(points, min_r2=0.90, min_temp_span_k=5.0)

    assert got["success"] is False
    assert got["fit_quality"] == "low_r2"
    assert got["A_ind"] is None
    assert got["E_ind"] is None
    assert float(got["R2"]) < 0.90


def test_extract_scorch_points_keeps_time_floor_rule_in_batch():
    curves = [
        {
            "COD_ENSAIO": 1,
            "T_C": 160.0,
            "T_K": 433.15,
            "tempo": [0.5, 1.0, 4.9, 5.0, 6.0, 7.0],
            "torque": [1.0, 9.0, 8.0, 7.0, 5.5, 5.9],
        }
    ]

    rows = extract_scorch_points(curves, time_floor=DEFAULT_TIME_FLOOR_S)

    assert len(rows) == 1
    row = rows[0]
    assert row["valid"] is True
    assert np.isclose(row["t_scorch"], 6.0)
    assert np.isclose(row["torque_min"], 5.5)
    assert row["points_before_search"] > 0
    assert row["points_after_search"] > 0


def test_fit_arrhenius_from_fixture_curves_realistic_project_profile():
    # Compact fixture captured from project-like rheometer profiles.
    curves = [
        {
            "COD_ENSAIO": 479643,
            "T_C": 195.0,
            "T_K": 468.15,
            "tempo": [0.6, 1.2, 1.8, 2.4, 3.0, 3.6, 4.2, 4.8, 5.4, 6.0, 6.6, 7.2, 7.8, 8.4, 9.0, 9.6, 10.2],
            "torque": [0.9, 1.3, 2.2, 3.6, 5.1, 6.1, 6.8, 7.3, 7.7, 8.2, 8.8, 9.2, 9.6, 9.9, 10.2, 10.4, 10.8],
        },
        {
            "COD_ENSAIO": 479928,
            "T_C": 150.0,
            "T_K": 423.15,
            "tempo": [0.6, 1.2, 1.8, 2.4, 3.0, 3.6, 4.2, 4.8, 5.4, 6.0, 6.6, 7.2, 7.8, 8.4, 9.0, 9.6, 10.2],
            "torque": [2.7, 3.1, 3.8, 4.6, 5.2, 5.7, 6.1, 6.4, 6.8, 7.1, 7.5, 7.9, 8.2, 8.6, 8.9, 9.3, 9.6],
        },
    ]

    scorch_rows = extract_scorch_points(curves, time_floor=DEFAULT_TIME_FLOOR_S)
    fit = fit_arrhenius_from_scorch(scorch_rows, min_points=2, min_temp_span_k=5.0, min_r2=-1.0)

    assert len(scorch_rows) >= 2
    assert fit["total_points"] == len(scorch_rows)
    assert fit["valid_points"] >= 2
    assert fit["temperature_range_k"]["span"] > 0.0
