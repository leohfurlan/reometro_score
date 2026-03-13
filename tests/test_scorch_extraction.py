import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.scorch_extraction import (
    DEFAULT_TIME_FLOOR_S,
    R_GAS,
    extract_curve_scorch,
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
