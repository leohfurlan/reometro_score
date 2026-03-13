import os
import sys

import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import vulcanization_service_v2 as vs2


def _fit_payload():
    return {
        "fit_id": "fit_v2_mechanistic",
        "k0": 2.0e7,
        "Ea": 76000.0,
        "n": 1.2,
    }


def _run_case(
    tmp_path,
    monkeypatch,
    *,
    kinetics_params=None,
    exotherm_enabled=True,
    qv=13000.0,
    t_end=180.0,
    A_ind=None,
    E_ind=None,
):
    monkeypatch.setattr(vs2, "OUT_DIR", tmp_path)
    sim_id, _ = vs2.run_simulation_v2(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=1,
        shape_raw="31",
        dx=0.001,
        dt=1.0,
        t_end=t_end,
        mold_temp_c=190.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=2,
        kinetics_params=kinetics_params,
        exotherm_enabled=exotherm_enabled,
        qv=qv,
        A_ind=A_ind,
        E_ind=E_ind,
    )
    return vs2.load_simulation_v2(sim_id)


def test_alpha_c_grows_over_time(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 5.0e7,
            "Eac": 68000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
    )
    alpha_c = np.asarray(sim["alpha_c_snaps"], dtype=float)

    assert float(np.mean(alpha_c[-1])) > float(np.mean(alpha_c[0]))


def test_alpha_r_is_non_negative(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 3.0e7,
            "Eac": 70000.0,
            "Ar": 1.0e9,
            "Ear": 72000.0,
            "Kx": 1.0,
            "Nx": 1.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
    )
    alpha_r = np.asarray(sim["alpha_r_snaps"], dtype=float)

    assert np.isfinite(alpha_r).all()
    assert float(np.min(alpha_r)) >= 0.0


def test_final_alpha_can_fall_when_reversion_dominates(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 1.0e7,
            "Eac": 72000.0,
            "Ar": 5.0e7,
            "Ear": 65000.0,
            "Kx": 1.2,
            "Nx": 1.0,
            "alpha_c0": 0.35,
            "alpha_r0": 0.0,
        },
    )
    alpha = np.asarray(sim["alpha_snaps"], dtype=float)
    alpha_mean_ts = np.mean(alpha, axis=1)

    assert float(np.max(alpha_mean_ts)) > float(alpha_mean_ts[-1])


def test_temperature_and_kinetics_are_coupled(tmp_path, monkeypatch):
    kinetics_params = {
        "Ac": 2.5e7,
        "Eac": 69000.0,
        "Ar": 0.0,
        "alpha_c0": 0.0,
        "alpha_r0": 0.0,
    }
    sim_no_source = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params=kinetics_params,
        exotherm_enabled=False,
        qv=1_000_000.0,
    )
    sim_with_source = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params=kinetics_params,
        exotherm_enabled=True,
        qv=1_000_000.0,
    )

    tmax_no_source = float(np.max(np.asarray(sim_no_source["t_snaps"], dtype=float)))
    tmax_with_source = float(np.max(np.asarray(sim_with_source["t_snaps"], dtype=float)))

    alpha_c_end_no_source = float(np.mean(np.asarray(sim_no_source["alpha_c_snaps"][-1], dtype=float)))
    alpha_c_end_with_source = float(np.mean(np.asarray(sim_with_source["alpha_c_snaps"][-1], dtype=float)))

    assert tmax_with_source > tmax_no_source
    assert alpha_c_end_with_source >= alpha_c_end_no_source


def test_mean_alpha_threshold_triggers_cooling(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 1.2e8,
            "Eac": 65000.0,
            "Ar": 0.0,
            "Kn": 1.0,
            "Nn": 1.2,
            "alpha_c0": 0.88,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
    )

    state_trace = np.asarray(sim["process_state_over_time"], dtype=str)
    alpha_mean_ts = np.mean(np.asarray(sim["alpha_snaps"], dtype=float), axis=1)
    cooling_indices = np.where(state_trace == "COOLING")[0]

    assert sim["demold_time"] is not None
    assert len(cooling_indices) > 0
    assert float(alpha_mean_ts[int(cooling_indices[0])]) >= 0.9


def test_temperature_starts_to_drop_after_cooling(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 1.2e8,
            "Eac": 65000.0,
            "Ar": 0.0,
            "Kn": 1.0,
            "Nn": 1.2,
            "alpha_c0": 0.88,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
    )

    state_trace = np.asarray(sim["process_state_over_time"], dtype=str)
    temp_mean_ts = np.mean(np.asarray(sim["t_snaps"], dtype=float), axis=1)
    cooling_indices = np.where(state_trace == "COOLING")[0]
    assert len(cooling_indices) > 0

    i0 = int(cooling_indices[0])
    tail = temp_mean_ts[i0 + 1 : i0 + 8]
    assert len(tail) > 0
    assert float(np.min(tail)) < float(temp_mean_ts[i0])


def test_cure_keeps_growing_briefly_after_demold(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 1.2e8,
            "Eac": 65000.0,
            "Ar": 0.0,
            "Kn": 1.0,
            "Nn": 1.2,
            "alpha_c0": 0.88,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
    )

    state_trace = np.asarray(sim["process_state_over_time"], dtype=str)
    alpha_c_mean_ts = np.mean(np.asarray(sim["alpha_c_snaps"], dtype=float), axis=1)
    cooling_indices = np.where(state_trace == "COOLING")[0]
    assert len(cooling_indices) > 0

    i0 = int(cooling_indices[0])
    i1 = min(i0 + 2, len(alpha_c_mean_ts) - 1)
    assert float(alpha_c_mean_ts[i1]) > float(alpha_c_mean_ts[i0])


def test_induction_clock_keeps_alpha_near_zero_before_threshold(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 8.0e7,
            "Eac": 65000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
        t_end=120.0,
        A_ind=3.0e3,
        E_ind=5.0e4,
    )

    alpha_c = np.asarray(sim["alpha_c_snaps"], dtype=float)
    omega = np.asarray(sim["induction_progress_snaps"], dtype=float)

    assert float(np.mean(alpha_c[-1])) <= 1e-6
    assert float(np.max(omega[-1])) < 1.0


def test_cure_starts_after_induction_threshold_is_reached(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 8.0e7,
            "Eac": 65000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
        t_end=320.0,
        A_ind=3.0e5,
        E_ind=5.0e4,
    )

    alpha_c_mean_ts = np.mean(np.asarray(sim["alpha_c_snaps"], dtype=float), axis=1)
    omega_mean_ts = np.mean(np.asarray(sim["induction_progress_snaps"], dtype=float), axis=1)

    unlocked = np.where(omega_mean_ts >= 1.0)[0]
    assert len(unlocked) > 0

    i_unlock = int(unlocked[0])
    i_late = min(i_unlock + 10, len(alpha_c_mean_ts) - 1)
    assert float(alpha_c_mean_ts[i_unlock]) >= 0.0
    assert float(alpha_c_mean_ts[i_late]) > float(alpha_c_mean_ts[i_unlock])


def test_save_load_v2_round_trip_preserves_shapes_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(vs2, "OUT_DIR", tmp_path)

    times = np.array([0.0, 5.0, 10.0], dtype=float)
    t_snaps = np.array(
        [
            [298.15, 300.0, 302.0, 303.0],
            [310.0, 312.0, 313.0, 314.0],
            [320.0, 321.0, 322.0, 323.0],
        ],
        dtype=np.float32,
    )
    alpha_c_snaps = np.array(
        [
            [0.00, 0.00, 0.00, 0.00],
            [0.20, 0.22, 0.25, 0.26],
            [0.35, 0.38, 0.40, 0.41],
        ],
        dtype=np.float32,
    )
    alpha_r_snaps = np.array(
        [
            [0.00, 0.00, 0.00, 0.00],
            [0.01, 0.01, 0.01, 0.01],
            [0.03, 0.03, 0.03, 0.03],
        ],
        dtype=np.float32,
    )
    alpha_snaps = alpha_c_snaps - alpha_r_snaps
    heat_source_snaps = np.array(
        [
            [0.0, 0.0, 0.0, 0.0],
            [10.0, 12.0, 13.0, 14.0],
            [8.0, 9.0, 10.0, 11.0],
        ],
        dtype=np.float32,
    )
    induction_progress_snaps = np.array(
        [
            [0.0, 0.0, 0.0, 0.0],
            [0.2, 0.2, 0.2, 0.2],
            [1.3, 1.4, 1.5, 1.6],
        ],
        dtype=np.float32,
    )

    sim_id, out_path = vs2.save_simulation_v2(
        fit_id="fit_roundtrip",
        mode="prensa",
        dim=1,
        stored_shape=(4,),
        full_shape=(4,),
        platen_axis=0,
        heated_axes=(0,),
        store_stride=1,
        snapshot_every_source=1,
        snapshot_every_store=1,
        dx=0.001,
        dt=5.0,
        t_end=10.0,
        times=times,
        t_snaps=t_snaps,
        alpha_c_snaps=alpha_c_snaps,
        alpha_r_snaps=alpha_r_snaps,
        alpha_snaps=alpha_snaps,
        heat_source_snaps=heat_source_snaps,
        induction_progress_snaps=induction_progress_snaps,
        process_state_over_time=np.array(["HEATING", "COOLING", "FINISHED"]),
        process_state_final="FINISHED",
        demold_time=5.0,
        rho=1020.0,
        cp=1820.0,
        lambda_rubber=0.16,
        qv=13000.0,
        A_ind=2.5e3,
        E_ind=5.1e4,
        exotherm_enabled=True,
        kinetics_params={"Ac": 1.0e6, "Eac": 7.0e4},
    )

    assert Path(out_path).exists()
    loaded = vs2.load_simulation_v2(sim_id)

    assert loaded is not None
    assert loaded["shape"] == (4,)
    assert loaded["full_shape"] == (4,)
    assert loaded["fit_id"] == "fit_roundtrip"
    assert loaded["mode"] == "prensa"
    assert loaded["process_state_final"] == "FINISHED"
    assert loaded["demold_time"] == 5.0
    assert loaded["rho"] == 1020.0
    assert loaded["cp"] == 1820.0
    assert loaded["lambda_rubber"] == 0.16
    assert loaded["qv"] == 13000.0
    assert loaded["A_ind"] == 2.5e3
    assert loaded["E_ind"] == 5.1e4
    assert np.asarray(loaded["times"]).shape == (3,)
    assert np.asarray(loaded["t_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["alpha_c_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["alpha_r_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["alpha_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["heat_source_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["induction_progress_snaps"]).shape == (3, 4)


def test_load_v2_is_backward_compatible_with_missing_new_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(vs2, "OUT_DIR", tmp_path)

    sim_id = "legacy_v2_case"
    legacy_path = Path(tmp_path) / f"sim_v2_{sim_id}.npz"
    np.savez_compressed(
        legacy_path,
        sim_id=sim_id,
        solver_version=np.array(2, dtype=int),
        fit_id="fit_legacy",
        mode="prensa",
        dim=np.array(1, dtype=int),
        shape=np.array([4], dtype=int),
        full_shape=np.array([4], dtype=int),
        platen_axis=np.array(0, dtype=int),
        heated_axes=np.array([0], dtype=int),
        store_stride=np.array(1, dtype=int),
        snapshot_every_source=np.array(1, dtype=int),
        snapshot_every_store=np.array(1, dtype=int),
        dx=np.array(0.001, dtype=float),
        dt=np.array(1.0, dtype=float),
        t_end=np.array(2.0, dtype=float),
        times=np.array([0.0, 1.0, 2.0], dtype=float),
        t_snaps=np.zeros((3, 4), dtype=np.float32),
        alpha_snaps=np.zeros((3, 4), dtype=np.float32),
    )

    loaded = vs2.load_simulation_v2(sim_id)
    assert loaded is not None
    assert np.asarray(loaded["alpha_c_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["alpha_r_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["heat_source_snaps"]).shape == (3, 4)
    assert np.asarray(loaded["induction_progress_snaps"]).shape == (3, 4)
    assert loaded["process_state_final"] in ("HEATING", "COOLING", "FINISHED")
    assert loaded["demold_time"] is None
    assert loaded["A_ind"] == vs2.DEFAULT_A_IND
    assert loaded["E_ind"] == vs2.DEFAULT_E_IND
