import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
pytest.importorskip("scipy")

from services.kinetics_solver import alpha_from_timeline, alpha_model


def test_alpha_from_timeline_matches_isothermal_alpha_model():
    time = np.linspace(0.0, 300.0, 301)
    temp_k = np.full_like(time, 438.15)
    k0 = 825592.9199352342
    ea = 67887.56366072825
    n = 4.0

    alpha_timeline = alpha_from_timeline(time, temp_k, k0, ea, n)
    alpha_isothermal = alpha_model(time, temp_k, k0, ea, n)

    assert np.allclose(alpha_timeline, alpha_isothermal, rtol=1e-6, atol=1e-9)


def test_alpha_model_uses_k_times_t_power_n_form():
    time = np.array([0.0, 10.0, 100.0], dtype=float)
    temp_k = np.full_like(time, 438.15)
    k0 = 0.1
    ea = 67887.56366072825
    n = 4.0

    k_t = k0 * np.exp(-ea / (8.314462618 * temp_k))
    x = k_t * np.power(np.maximum(time, 0.0), n)
    expected = x / (1.0 + x)
    got = alpha_model(time, temp_k, k0, ea, n)

    assert np.allclose(got, expected, rtol=1e-9, atol=1e-12)
