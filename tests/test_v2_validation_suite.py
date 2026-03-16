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
    assert report["quick_mode"] is True
    assert report["total_cases"] == 6
    assert os.path.exists(report["json_report_path"])
    assert os.path.exists(report["csv_report_path"])

    for row in report["cases"]:
        metrics = row["metrics"]
        criteria = row["criteria"]

        assert np.isfinite(float(metrics["temperature_max_c"]))
        assert np.isfinite(float(metrics["temperature_mean_c"]))
        assert np.isfinite(float(metrics["alpha_min"]))
        assert np.isfinite(float(metrics["alpha_mean"]))
        assert np.isfinite(float(metrics["alpha_max"]))
        assert np.isfinite(float(metrics["delta_alpha"]))

        assert criteria["solver_no_nan"] is True
        assert criteria["temperature_drops_after_cooling"] is True
        assert criteria["mean_alpha_reaches_0_9_before_cooling"] is True
        assert criteria["delta_alpha_near_optimal"] is True

    assert report["all_required_passed"] is True


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
