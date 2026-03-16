import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import run_v2_validation_suite as runner


def test_main_parses_cli_and_invokes_validation_suite(monkeypatch, tmp_path, capsys):
    captured_kwargs = {}

    def _fake_run_v2_validation_suite(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "quick_mode": bool(kwargs.get("quick")),
            "quality_gate_mode": str(kwargs.get("gate_mode")),
            "total_cases": 6,
            "all_required_passed": True,
            "has_critical_quality": False,
            "ci_gate_passed": True,
            "json_report_path": str(tmp_path / "report.json"),
            "csv_report_path": str(tmp_path / "report.csv"),
        }

    monkeypatch.setattr(runner, "run_v2_validation_suite", _fake_run_v2_validation_suite)

    runner.main(
        [
            "--quick",
            "--out-dir",
            str(tmp_path),
            "--sim-out-dir",
            str(tmp_path / "sims"),
            "--report-stem",
            "validation_smoke",
        ]
    )

    assert captured_kwargs["quick"] is True
    assert captured_kwargs["output_dir"] == str(tmp_path)
    assert captured_kwargs["sim_out_dir"] == str(tmp_path / "sims")
    assert captured_kwargs["report_stem"] == "validation_smoke"
    assert captured_kwargs["gate_mode"] == "informative"

    output = capsys.readouterr().out
    assert "v2_validation_suite: DONE" in output
    assert "all_required_passed: True" in output
