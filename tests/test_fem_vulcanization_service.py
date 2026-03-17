import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytest.importorskip("fipy")

from services.fem_vulcanization_service import run_fem_simulation, run_fem_simulation_normalized


def _base_config():
    return {
        "nr": 8,
        "nz": 8,
        "radius_m": 0.008,
        "height_m": 0.008,
        "dt": 0.25,
        "t_end": 8.0,
        "snapshot_every": 2,
        "mold_temp_c": 190.0,
        "init_temp_c": 25.0,
        "ramp_rate_c_per_s": 0.0,
        "demold_alpha_mean_threshold": 0.2,
        "kinetics_params": {
            "Ac": 2.0e9,
            "Eac": 5.5e4,
            "Ar": 2.0e8,
            "Ear": 6.5e4,
            "Kn": 1.0,
            "Nn": 1.2,
            "Kx": 1.0,
            "Nx": 1.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
    }


def test_run_fem_simulation_returns_consistent_outputs():
    sim = run_fem_simulation(_base_config())

    assert sim["solver_family"] == "fipy_axisymmetric_2d"
    assert sim["geometry"] == "axisymmetric_cylindrical_section"
    assert np.asarray(sim["times"]).ndim == 1
    assert np.asarray(sim["t_snaps"]).ndim == 2
    assert np.asarray(sim["alpha_c_snaps"]).shape == np.asarray(sim["alpha_r_snaps"]).shape
    assert np.asarray(sim["alpha_c_snaps"]).shape == np.asarray(sim["alpha_snaps"]).shape
    assert np.asarray(sim["t_snaps"]).shape[1] == sim["mesh"]["n_cells"]
    assert np.isfinite(np.asarray(sim["t_snaps"], dtype=float)).all()
    assert np.isfinite(np.asarray(sim["alpha_snaps"], dtype=float)).all()


def test_exotherm_increases_peak_temperature():
    cfg = _base_config()
    cfg["qv"] = 1_000_000.0
    cfg["exotherm_enabled"] = False
    sim_no_source = run_fem_simulation(cfg)

    cfg = _base_config()
    cfg["qv"] = 1_000_000.0
    cfg["exotherm_enabled"] = True
    sim_with_source = run_fem_simulation(cfg)

    tmax_no_source = float(np.max(np.asarray(sim_no_source["t_snaps"], dtype=float)))
    tmax_with_source = float(np.max(np.asarray(sim_with_source["t_snaps"], dtype=float)))

    assert tmax_with_source > tmax_no_source


def test_run_fem_simulation_normalized_exposes_engine_and_schema():
    sim = run_fem_simulation_normalized(_base_config())

    assert sim["engine"] == "axisymmetric_fipy"
    assert sim["engine_status"] == "prototype"
    assert sim["geometry_type"] == "axisymmetric_cylindrical_section"
    assert "temperature_snapshots" in sim


def test_run_fem_simulation_with_leroy_payload_exposes_alpha_unstable():
    cfg = _base_config()
    cfg["kinetics_params"] = {}
    cfg["fit_payload"] = {
        "model_family": "leroy2013_continuous_v1",
        "model_parameters": {
            "Av1": 0.012,
            "Av2": 0.040,
            "Ev": 90000.0,
            "X": 0.65,
            "Ar": 0.20,
            "Er": 130000.0,
        },
    }

    sim = run_fem_simulation(cfg)

    assert sim["model_family"] == "leroy2013_continuous_v1"
    assert "alpha_unstable_snaps" in sim
    assert np.asarray(sim["alpha_unstable_snaps"]).shape == np.asarray(sim["alpha_snaps"]).shape
