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


def _fit_payload_with_validity(min_c=150.0, max_c=190.0, confidence="high"):
    payload = _fit_payload()
    payload.update(
        {
            "A_ind": 3.0e5,
            "E_ind": 5.0e4,
            "induction_confidence": confidence,
            "induction_fit_quality": "high",
            "temperature_validity_range": {
                "min_c": float(min_c),
                "max_c": float(max_c),
                "span_c": float(max_c - min_c),
                "min_k": float(min_c + 273.15),
                "max_k": float(max_c + 273.15),
                "span_k": float(max_c - min_c),
            },
            "induction_model_regime": "single_arrhenius",
        }
    )
    return payload


def _run_case(
    tmp_path,
    monkeypatch,
    *,
    fit_payload=None,
    kinetics_params=None,
    exotherm_enabled=True,
    qv=13000.0,
    t_end=180.0,
    A_ind=None,
    E_ind=None,
    h_air=11.4,
    mode="prensa",
    dim=1,
    shape_raw="31",
    dx=0.001,
    dt=1.0,
    mold_temp_c=190.0,
    init_temp_c=25.0,
    thermal_integration_mode="imex",
):
    monkeypatch.setattr(vs2, "OUT_DIR", tmp_path)
    sim_id, _ = vs2.run_simulation_v2(
        fit_payload=(fit_payload or _fit_payload()),
        mode=mode,
        dim=dim,
        shape_raw=shape_raw,
        dx=dx,
        dt=dt,
        t_end=t_end,
        mold_temp_c=mold_temp_c,
        init_temp_c=init_temp_c,
        ramp_rate=0.0,
        snapshot_every=2,
        kinetics_params=kinetics_params,
        exotherm_enabled=exotherm_enabled,
        qv=qv,
        A_ind=A_ind,
        E_ind=E_ind,
        h_air=h_air,
        thermal_integration_mode=thermal_integration_mode,
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
        t_end=600.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
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
        t_end=600.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
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
        t_end=600.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
    )

    state_trace = np.asarray(sim["process_state_over_time"], dtype=str)
    alpha_c_mean_ts = np.mean(np.asarray(sim["alpha_c_snaps"], dtype=float), axis=1)
    cooling_indices = np.where(state_trace == "COOLING")[0]
    assert len(cooling_indices) > 0

    i0 = int(cooling_indices[0])
    i1 = min(i0 + 2, len(alpha_c_mean_ts) - 1)
    assert float(alpha_c_mean_ts[i1]) >= float(alpha_c_mean_ts[i0])


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


def test_default_induction_fallback_is_not_instant_and_is_flagged_low_confidence(tmp_path, monkeypatch):
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
        t_end=20.0,
    )

    alpha_c = np.asarray(sim["alpha_c_snaps"], dtype=float)
    omega = np.asarray(sim["induction_progress_snaps"], dtype=float)

    assert float(np.mean(alpha_c[-1])) < 0.15
    assert float(np.mean(omega[-1])) < 1.0
    assert sim["induction_confidence"] == "low"
    assert sim["induction_source"] == "safe_default"
    assert sim["induction_fit_quality"] in {"not_calibrated", "blocked_dangerous_default"}


def test_dangerous_near_instant_override_is_blocked_by_induction_policy(tmp_path, monkeypatch):
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
        t_end=20.0,
        A_ind=1.0e12,
        E_ind=0.0,
    )

    assert np.isclose(sim["A_ind"], vs2.DEFAULT_A_IND, rtol=0.0, atol=1e-9)
    assert np.isclose(sim["E_ind"], vs2.DEFAULT_E_IND, rtol=0.0, atol=1e-9)
    assert sim["induction_source"] == "safe_default"
    assert sim["induction_fit_quality"] == "blocked_dangerous_default"
    warnings = sim.get("numerical_warnings") or []
    assert any("dangerous_near_instant_induction_blocked" in str(w) for w in warnings)


def test_cooling_is_sensitive_to_h_air_with_new_convective_boundary(tmp_path, monkeypatch):
    params = {
        "Ac": 1.2e8,
        "Eac": 65000.0,
        "Ar": 0.0,
        "Kn": 1.0,
        "Nn": 1.2,
        "alpha_c0": 0.88,
        "alpha_r0": 0.0,
    }
    sim_low_h = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params=params,
        exotherm_enabled=False,
        t_end=600.0,
        h_air=3.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
    )
    sim_high_h = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params=params,
        exotherm_enabled=False,
        t_end=600.0,
        h_air=25.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
    )

    def _cooling_drop(sim):
        trace = np.asarray(sim["process_state_over_time"], dtype=str)
        temp_mean = np.mean(np.asarray(sim["t_snaps"], dtype=float), axis=1)
        cool_idx = np.where(trace == "COOLING")[0]
        if len(cool_idx) == 0:
            return 0.0
        i0 = int(cool_idx[0])
        tail = temp_mean[i0 + 1 : i0 + 8]
        if len(tail) == 0:
            return 0.0
        return float(temp_mean[i0] - np.min(tail))

    assert _cooling_drop(sim_high_h) > _cooling_drop(sim_low_h)


def test_v2_persists_numerical_metrics_and_alert_fields(tmp_path, monkeypatch):
    sim = _run_case(
        tmp_path,
        monkeypatch,
        kinetics_params={
            "Ac": 2.0e9,
            "Eac": 20000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=True,
        qv=2.0e6,
        t_end=40.0,
        A_ind=5.0e8,
        E_ind=8.5e4,
    )

    metrics = dict(sim.get("metrics") or {})
    assert "substeps_total" in metrics
    assert "substeps_max_per_step" in metrics
    assert "max_cure_rate_s_inv" in metrics
    assert "max_reversion_rate_s_inv" in metrics
    assert "max_heat_source_w_m3" in metrics
    assert "max_source_to_diffusion_dT_ratio" in metrics
    assert "clip_events_count" in metrics
    assert "nan_recovery_events" in metrics
    assert "critical_step_index" in metrics
    assert "critical_step_reason" in metrics
    assert "critical_step_context" in metrics
    assert "critical_step_metrics" in metrics
    assert "critical_steps_trace" in metrics
    assert "stiffness_alerts" in metrics
    assert "quality_flags" in metrics
    assert "quality_status" in metrics
    assert "quality_severity_breakdown" in metrics

    assert sim["clip_events_count"] == int(metrics["clip_events_count"])
    assert sim["nan_recovery_events"] == int(metrics["nan_recovery_events"])


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
        metrics={"clip_events_count": 2, "nan_recovery_events": 1, "substeps_total": 8},
        numerical_warnings=[{"code": "test_warning", "message": "synthetic"}],
        induction_confidence="medium",
        induction_source="fit_payload",
        induction_fit_quality="medium",
        temperature_validity_range={"min_c": 150.0, "max_c": 190.0, "span_c": 40.0},
        induction_extrapolation_warning=True,
        induction_model_regime="single_arrhenius",
        quality_status="warning",
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
    assert loaded["induction_confidence"] == "medium"
    assert loaded["induction_source"] == "fit_payload"
    assert loaded["induction_fit_quality"] == "medium"
    assert loaded["temperature_validity_range"]["span_c"] == 40.0
    assert loaded["induction_extrapolation_warning"] is True
    assert loaded["induction_model_regime"] == "single_arrhenius"
    assert loaded["quality_status"] == "warning"
    assert int(loaded["metrics"]["clip_events_count"]) == 2
    assert int(loaded["metrics"]["nan_recovery_events"]) == 1
    assert len(loaded["numerical_warnings"]) == 1
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
    assert loaded["induction_confidence"] == "low"
    assert loaded["induction_source"] == "safe_default"
    assert loaded["induction_fit_quality"] == "not_calibrated"
    assert loaded["temperature_validity_range"] == {}
    assert loaded["induction_extrapolation_warning"] is False
    assert loaded["induction_model_regime"] == "single_arrhenius"
    assert loaded["quality_status"] == "healthy"
    assert loaded["clip_events_count"] == 0
    assert loaded["nan_recovery_events"] == 0


def test_load_v2_normalized_exposes_unified_schema(tmp_path, monkeypatch):
    sim = _run_case(tmp_path, monkeypatch, t_end=8.0)

    normalized = vs2.load_simulation_v2_normalized(sim["sim_id"])

    assert normalized["engine"] == "thermo_kinetic_v2"
    assert normalized["engine_status"] == "experimental"
    assert normalized["process_state_final"] in ("HEATING", "COOLING", "FINISHED")
    assert "temperature_snapshots" in normalized
    assert "alpha_c" in normalized
    assert "alpha_r" in normalized
    assert "heat_source" in normalized
    assert "induction_confidence" in normalized
    assert "induction_source" in normalized
    assert "induction_fit_quality" in normalized
    assert "temperature_validity_range" in normalized
    assert "induction_extrapolation_warning" in normalized
    assert "induction_model_regime" in normalized
    assert "quality_status" in normalized
    assert "clip_events_count" in normalized
    assert "nan_recovery_events" in normalized


def test_imex_diffusion_reduces_substeps_in_aggressive_mesh(tmp_path, monkeypatch):
    fit_payload = _fit_payload_with_validity(min_c=0.0, max_c=300.0)
    kwargs = {
        "fit_payload": fit_payload,
        "kinetics_params": {
            "Ac": 2.0e7,
            "Eac": 68000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        "exotherm_enabled": False,
        "t_end": 8.0,
        "A_ind": 3.0e5,
        "E_ind": 5.0e4,
        "dx": 0.0001,
        "dt": 1.0,
        "shape_raw": "11",
    }
    sim_explicit = _run_case(tmp_path, monkeypatch, thermal_integration_mode="explicit", **kwargs)
    sim_imex = _run_case(tmp_path, monkeypatch, thermal_integration_mode="imex", **kwargs)

    m_explicit = dict(sim_explicit.get("metrics") or {})
    m_imex = dict(sim_imex.get("metrics") or {})

    assert int(m_explicit["substeps_max_per_step"]) > int(m_imex["substeps_max_per_step"])
    assert len(m_explicit["stiffness_alerts"]) > len(m_imex["stiffness_alerts"])
    assert np.isfinite(np.asarray(sim_imex["t_snaps"], dtype=float)).all()
    assert int(m_imex["nan_recovery_events"]) == 0


def test_induction_extrapolation_levels_and_quality_status(tmp_path, monkeypatch):
    common = {
        "kinetics_params": {
            "Ac": 8.0e7,
            "Eac": 65000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        "exotherm_enabled": False,
        "t_end": 30.0,
        "A_ind": 3.0e5,
        "E_ind": 5.0e4,
        "shape_raw": "11",
    }

    sim_inside = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=20.0, max_c=220.0),
        mold_temp_c=180.0,
        **common,
    )
    sim_mild = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=20.0, max_c=188.0),
        mold_temp_c=191.0,
        **common,
    )
    sim_strong = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=20.0, max_c=170.0),
        mold_temp_c=205.0,
        **common,
    )

    m_inside = dict(sim_inside.get("metrics") or {})
    m_mild = dict(sim_mild.get("metrics") or {})
    m_strong = dict(sim_strong.get("metrics") or {})

    assert m_inside["induction_extrapolation_level"] == "none"
    assert bool(m_inside["induction_extrapolation_warning"]) is False
    assert m_inside["quality_status"] in {"healthy", "info", "warning"}

    assert m_mild["induction_extrapolation_level"] == "mild"
    assert bool(m_mild["induction_extrapolation_warning"]) is True
    assert m_mild["quality_status"] in {"warning", "critical"}

    assert m_strong["induction_extrapolation_level"] == "strong"
    assert bool(m_strong["induction_extrapolation_warning"]) is True
    assert m_strong["quality_status"] == "critical"
    assert sim_strong["induction_confidence"] in {"low", "medium"}


def test_critical_step_reason_tracks_dominant_mechanism(tmp_path, monkeypatch):
    sim_exothermia = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=0.0, max_c=400.0),
        kinetics_params={
            "Ac": 2.0e9,
            "Eac": 25000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=True,
        qv=1.5e6,
        t_end=14.0,
        shape_raw="11",
        A_ind=5.0e8,
        E_ind=8.5e4,
    )
    reason_exothermia = str((sim_exothermia.get("metrics") or {}).get("critical_step_reason"))
    assert reason_exothermia in {"exothermia_source_dominance", "clipping_recovery"}

    sim_substep = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=0.0, max_c=300.0),
        kinetics_params={
            "Ac": 2.0e7,
            "Eac": 68000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
        t_end=6.0,
        shape_raw="11",
        dx=0.0002,
        thermal_integration_mode="explicit",
        A_ind=3.0e5,
        E_ind=5.0e4,
    )
    assert str((sim_substep.get("metrics") or {}).get("critical_step_reason")) == "diffusion_substepping"

    sim_extrapolation = _run_case(
        tmp_path,
        monkeypatch,
        fit_payload=_fit_payload_with_validity(min_c=140.0, max_c=165.0),
        kinetics_params={
            "Ac": 8.0e7,
            "Eac": 65000.0,
            "Ar": 0.0,
            "alpha_c0": 0.0,
            "alpha_r0": 0.0,
        },
        exotherm_enabled=False,
        mold_temp_c=205.0,
        t_end=24.0,
        shape_raw="11",
        A_ind=3.0e5,
        E_ind=5.0e4,
    )
    assert str((sim_extrapolation.get("metrics") or {}).get("critical_step_reason")) == "induction_extrapolation"
