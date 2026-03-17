import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import vulcanization_service as vs


def _fit_payload():
    return {"fit_id": "fit_test", "k0": 1e6, "Ea": 80000.0, "n": 1.2}


def test_simulation_initial_snapshot_is_not_advanced(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    sim_id, _ = vs.run_simulation(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=1,
        shape_raw="20",
        dx=0.001,
        dt=5.0,
        t_end=20.0,
        mold_temp_c=170.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=1,
    )
    sim = vs.load_simulation(sim_id)

    assert np.isclose(float(sim["times"][0]), 0.0)
    t0 = np.asarray(sim["t_snaps"][0], dtype=float)
    a0 = np.asarray(sim["alpha_snaps"][0], dtype=float)

    assert np.isclose(t0[0], 170.0 + 273.15)
    assert np.isclose(t0[-1], 170.0 + 273.15)
    assert np.allclose(t0[1:-1], 25.0 + 273.15)
    assert np.allclose(a0, 0.0)


def test_simulation_auto_substeps_prevent_temperature_blowup(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    sim_id, _ = vs.run_simulation(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=1,
        shape_raw="20",
        dx=0.001,
        dt=5.0,
        t_end=300.0,
        mold_temp_c=170.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=20,
    )
    sim = vs.load_simulation(sim_id)

    t_snaps = np.asarray(sim["t_snaps"], dtype=float)
    assert np.isclose(float(sim["times"][-1]), 300.0)
    assert np.isfinite(t_snaps).all()
    assert float(t_snaps.min()) > 250.0
    assert float(t_snaps.max()) < 500.0


def test_cure_profile_does_not_saturate_at_100s_for_reference_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    fit_payload = {
        "fit_id": "fit_reference",
        "k0": 0.1,
        "Ea": 67887.56366072825,
        "n": 4.0,
    }
    sim_id, _ = vs.run_simulation(
        fit_payload=fit_payload,
        mode="prensa",
        dim=1,
        shape_raw="20",
        dx=0.001,
        dt=5.0,
        t_end=100.0,
        mold_temp_c=160.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=20,
    )
    sim = vs.load_simulation(sim_id)

    alpha_100s = np.asarray(sim["alpha_snaps"][-1], dtype=float)
    assert np.isclose(float(sim["times"][-1]), 100.0)
    assert float(alpha_100s.max()) < 0.2
    assert float(alpha_100s.mean()) < 0.05


def test_press_2d_applies_heating_on_selected_platen_axis_only(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    sim_id, _ = vs.run_simulation(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=2,
        shape_raw="11,11",
        dx=0.001,
        dt=1.0,
        t_end=1.0,
        mold_temp_c=170.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=1,
        platen_axis=1,
    )
    sim = vs.load_simulation(sim_id)
    t0 = np.asarray(sim["t_snaps"][0], dtype=float)

    hot_k = 170.0 + 273.15
    cold_k = 25.0 + 273.15

    assert sim["platen_axis"] == 1
    assert np.allclose(t0[:, 0], hot_k)
    assert np.allclose(t0[:, -1], hot_k)
    assert np.allclose(t0[0, 1:-1], cold_k)
    assert np.allclose(t0[-1, 1:-1], cold_k)


def test_simulation_can_be_cancelled_via_checker(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)

    progress_events = []
    calls = {"count": 0}

    def _progress_callback(event):
        progress_events.append(dict(event))

    def _cancel_checker():
        calls["count"] += 1
        return calls["count"] > 2

    try:
        vs.run_simulation(
            fit_payload=_fit_payload(),
            mode="prensa",
            dim=3,
            shape_raw="16,16,16",
            dx=0.001,
            dt=1.0,
            t_end=200.0,
            mold_temp_c=170.0,
            init_temp_c=25.0,
            ramp_rate=0.0,
            snapshot_every=10,
            platen_axis=0,
            progress_callback=_progress_callback,
            cancel_checker=_cancel_checker,
        )
        assert False, "Expected cancellation exception"
    except RuntimeError as exc:
        assert str(exc) == "SIMULATION_CANCELLED"

    assert progress_events


def test_snapshot_plan_limits_memory_for_large_3d_case():
    shape = (16, 501, 1501)
    steps = 1500
    plan = vs._snapshot_plan(shape=shape, dim=3, steps=steps, snapshot_every=20)

    assert plan["downsampled"] is True
    assert plan["spatial_stride"] >= 2
    assert int(plan["estimated_bytes"]) <= int(vs.MAX_SNAPSHOT_BYTES)
    assert int(np.prod(plan["stored_shape"])) < int(np.prod(shape))
    assert plan["store_indices"][0] == 0
    assert plan["store_indices"][-1] == steps


def test_load_simulation_exposes_stride_scaled_dx(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(vs, "MAX_SNAPSHOT_CELLS", 20)
    monkeypatch.setattr(vs, "MAX_SNAPSHOT_BYTES", 512 * 1024 * 1024)

    sim_id, _ = vs.run_simulation(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=2,
        shape_raw="11,11",
        dx=0.001,
        dt=1.0,
        t_end=10.0,
        mold_temp_c=170.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=1,
        platen_axis=0,
    )
    sim = vs.load_simulation(sim_id)

    assert sim["store_stride"] > 1
    assert np.isclose(sim["dx"], sim["dx_compute"] * sim["store_stride"])
    assert int(np.prod(sim["shape"])) < int(np.prod(sim["full_shape"]))


def test_load_simulation_normalized_exposes_unified_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    sim_id, _ = vs.run_simulation(
        fit_payload=_fit_payload(),
        mode="prensa",
        dim=1,
        shape_raw="20",
        dx=0.001,
        dt=1.0,
        t_end=2.0,
        mold_temp_c=170.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=1,
    )

    sim = vs.load_simulation_normalized(sim_id)

    assert sim["engine"] == "empirical_v1"
    assert sim["engine_status"] == "stable"
    assert "temperature_snapshots" in sim
    assert "alpha_c" in sim
    assert "alpha_r" in sim
    assert "heat_source" in sim


def test_leroy_model_simulation_exposes_alpha_v_and_unstable_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "OUT_DIR", tmp_path)
    fit_payload = {
        "fit_id": "fit_leroy",
        "model_family": "leroy2013_continuous_v1",
        "model_parameters": {
            "Av1": 0.015,
            "Av2": 0.050,
            "Ev": 90000.0,
            "X": 0.62,
            "Ar": 1.0,
            "Er": 125000.0,
        },
    }
    sim_id, _ = vs.run_simulation(
        fit_payload=fit_payload,
        mode="prensa",
        dim=1,
        shape_raw="24",
        dx=0.001,
        dt=2.0,
        t_end=300.0,
        mold_temp_c=185.0,
        init_temp_c=25.0,
        ramp_rate=0.0,
        snapshot_every=10,
    )
    sim = vs.load_simulation(sim_id)

    alpha_v = np.asarray(sim["alpha_v_snaps"], dtype=float)
    alpha_unstable = np.asarray(sim["alpha_unstable_snaps"], dtype=float)
    alpha_total = np.asarray(sim["alpha_snaps"], dtype=float)

    assert alpha_v.shape == alpha_total.shape
    assert alpha_unstable.shape == alpha_total.shape
    assert np.all(np.diff(np.mean(alpha_v, axis=1)) >= -1e-8)
    assert float(np.min(alpha_total)) >= 0.0
    assert float(np.max(alpha_total)) <= 1.0
