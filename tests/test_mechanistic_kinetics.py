import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.mechanistic_kinetics import MechanisticKinetics, MechanisticKineticsParams, final_alpha


def test_alpha_c_rate_increases_with_temperature():
    params = MechanisticKineticsParams(Ac=1.0e7, Eac=7.5e4, Kn=0.5, Nn=1.2)
    model = MechanisticKinetics(params)
    alpha_c = np.full((6,), 0.2, dtype=float)

    rate_low = model.cure_rate(np.full((6,), 413.15), alpha_c)
    rate_high = model.cure_rate(np.full((6,), 453.15), alpha_c)

    assert np.all(rate_high > rate_low)


def test_alpha_r_grows_only_when_reversion_is_parameterized():
    base_alpha_r = np.full((5,), 0.05, dtype=float)
    temp_k = np.full((5,), 463.15, dtype=float)

    model_off = MechanisticKinetics(
        MechanisticKineticsParams(
            Ar=0.0,
            Ear=1.0e5,
            Kx=1.0,
            Nx=1.0,
        )
    )
    model_on = MechanisticKinetics(
        MechanisticKineticsParams(
            Ar=1.0e9,
            Ear=7.0e4,
            Kx=1.0,
            Nx=1.0,
        )
    )

    rate_off = model_off.reversion_rate(temp_k, base_alpha_r)
    rate_on = model_on.reversion_rate(temp_k, base_alpha_r)

    assert np.allclose(rate_off, 0.0)
    assert np.all(rate_on > 0.0)


def test_final_alpha_matches_difference_definition():
    alpha_c = np.array([0.2, 0.6, 0.9], dtype=float)
    alpha_r = np.array([0.1, 0.2, 0.4], dtype=float)

    got = final_alpha(alpha_c, alpha_r)
    expected = alpha_c - alpha_r

    assert np.allclose(got, expected)


def test_step_explicit_is_stable_and_no_nan_for_reasonable_inputs():
    params = MechanisticKineticsParams(
        Ac=2.0e6,
        Eac=7.8e4,
        Ar=5.0e7,
        Ear=9.0e4,
        Kn=1.0,
        Nn=1.2,
        Kx=0.8,
        Nx=1.1,
        alpha_c0=0.1,
        alpha_r0=0.0,
    )
    model = MechanisticKinetics(params)
    shape = (12,)
    alpha_c = np.full(shape, params.alpha_c0, dtype=float)
    alpha_r = np.full(shape, params.alpha_r0, dtype=float)
    temp_k = np.linspace(430.0, 470.0, shape[0], dtype=float)

    for _ in range(120):
        alpha_c, alpha_r, alpha_net = model.step_explicit(temp_k, alpha_c, alpha_r, dt=0.5)

    assert np.isfinite(alpha_c).all()
    assert np.isfinite(alpha_r).all()
    assert np.isfinite(alpha_net).all()
    assert np.all((0.0 <= alpha_c) & (alpha_c <= 1.0))
    assert np.all((0.0 <= alpha_r) & (alpha_r <= 1.0))
    assert np.all(alpha_r <= alpha_c)
