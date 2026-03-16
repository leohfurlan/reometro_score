import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.v2_validation_suite import apply_delta_alpha_criterion, run_v2_validation_suite


def test_run_v2_validation_suite_quick_generates_reports_and_metrics(tmp_path):
    report = run_v2_validation_suite(
        output_dir=tmp_path,
        report_stem="validation_quick",
        quick=True,
        sim_out_dir=tmp_path / "sims",
    )

    assert report["suite"] == "v2_physical_validation"
    assert report["engine"] == "thermo_kinetic_v2"
    assert report["engine_status"] == "experimental"
    assert report["quick_mode"] is True
    assert report["quality_gate_mode"] == "informative"
    assert report["total_cases"] == 6
    assert os.path.exists(report["json_report_path"])
    assert os.path.exists(report["csv_report_path"])

    for row in report["cases"]:
        assert row["engine"] == "thermo_kinetic_v2"
        assert row["engine_status"] == "experimental"
        metrics = row["metrics"]
        criteria = row["criteria"]

        assert np.isfinite(float(metrics["temperature_max_c"]))
        assert np.isfinite(float(metrics["temperature_mean_c"]))
        assert np.isfinite(float(metrics["alpha_min"]))
        assert np.isfinite(float(metrics["alpha_mean"]))
        assert np.isfinite(float(metrics["alpha_max"]))
        assert np.isfinite(float(metrics["delta_alpha"]))
        assert "substeps_total" in metrics
        assert "max_cure_rate_s_inv" in metrics
        assert "max_reversion_rate_s_inv" in metrics
        assert "max_heat_source_w_m3" in metrics
        assert "max_source_to_diffusion_dT_ratio" in metrics
        assert "clip_events_count" in metrics
        assert "nan_recovery_events" in metrics
        assert "critical_step_index" in metrics
        assert "critical_step_time_s" in metrics
        assert "critical_step_reason" in metrics
        assert "quality_status" in metrics
        assert "quality_flags" in metrics
        assert "alerts" in metrics

        assert criteria["solver_no_nan"] is True
        assert criteria["temperature_drops_after_cooling"] is True
        assert criteria["mean_alpha_reaches_0_9_before_cooling"] is True
        assert criteria["delta_alpha_near_optimal"] is True
        assert "no_nan_recovery_events" in criteria
        assert "no_clip_events" in criteria
        assert "has_stiffness_alerts" in criteria
        assert "quality_not_critical" in criteria
        assert "induction_within_validity_range" in criteria

    assert report["all_required_passed"] is True
    assert report["ci_gate_passed"] is True
    assert "alert_flags_non_blocking" in report


def test_apply_delta_alpha_criterion_flags_near_optimal_correctly():
    rows = [
        {
            "case_id": "small_baseline",
            "size_class": "small",
            "profile": "baseline",
            "metrics": {"delta_alpha": 0.20},
            "criteria": {"delta_alpha_near_optimal": False},
        },
        {
            "case_id": "small_near_optimal",
            "size_class": "small",
            "profile": "near_optimal",
            "metrics": {"delta_alpha": 0.12},
            "criteria": {"delta_alpha_near_optimal": False},
        },
    ]

    updated = apply_delta_alpha_criterion(rows)
    assert updated[0]["criteria"]["delta_alpha_near_optimal"] is True
    assert updated[1]["criteria"]["delta_alpha_near_optimal"] is True


def test_run_v2_validation_suite_strict_mode_fails_on_critical_quality(monkeypatch, tmp_path):
    from services import v2_validation_suite as suite

    monkeypatch.setattr(suite.vs2, "run_simulation_v2", lambda **kwargs: ("synthetic", str(tmp_path / "sim.npz")))
    monkeypatch.setattr(
        suite.vs2,
        "load_simulation_v2",
        lambda _sim_id: {
            "times": np.asarray([0.0, 1.0], dtype=float),
            "t_snaps": np.asarray([[298.15], [320.0]], dtype=np.float32),
            "alpha_c_snaps": np.asarray([[0.0], [0.95]], dtype=np.float32),
            "alpha_r_snaps": np.asarray([[0.0], [0.0]], dtype=np.float32),
            "alpha_snaps": np.asarray([[0.0], [0.95]], dtype=np.float32),
            "process_state_over_time": np.asarray(["HEATING", "COOLING"]),
            "process_state_final": "COOLING",
            "demold_time": 1.0,
            "metrics": {
                "substeps_max_per_step": 1,
                "max_source_to_diffusion_dT_ratio": 1.0,
                "clip_events_count": 0,
                "nan_recovery_events": 0,
                "quality_status": "critical",
                "quality_flags": [{"code": "synthetic_critical", "severity": "critical"}],
            },
        },
    )
    monkeypatch.setattr(
        suite,
        "apply_delta_alpha_criterion",
        lambda rows, tolerance=1e-9: rows,
    )

    report = suite.run_v2_validation_suite(
        output_dir=tmp_path,
        report_stem="validation_strict",
        quick=True,
        sim_out_dir=tmp_path / "sims",
        gate_mode="strict",
        cases=[
            {
                "case_id": "synthetic_case",
                "size_class": "small",
                "profile": "baseline",
                "sim_kwargs": {},
            }
        ],
    )

    assert report["quality_gate_mode"] == "strict"
    assert report["has_critical_quality"] is True
    assert report["ci_gate_passed"] is False
