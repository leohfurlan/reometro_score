import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.engine_registry import ENGINE_EMPIRICAL_V1, ENGINE_THERMO_KINETIC_V2
from services.simulation_diagnostics import build_engine_comparison, extract_simulation_diagnostics


def _base_payload():
    return {
        "times": np.asarray([0.0, 20.0, 40.0], dtype=float),
        "t_snaps": np.asarray(
            [
                [[298.15, 298.15], [298.15, 298.15]],
                [[330.15, 332.15], [331.15, 333.15]],
                [[350.15, 352.15], [351.15, 353.15]],
            ],
            dtype=np.float32,
        ),
        "alpha_snaps": np.asarray(
            [
                [[0.0, 0.0], [0.0, 0.0]],
                [[0.2, 0.3], [0.25, 0.35]],
                [[0.8, 0.85], [0.82, 0.88]],
            ],
            dtype=np.float32,
        ),
        "process_state_final": "COOLING",
        "quality_status": "healthy",
    }


def test_extract_simulation_diagnostics_for_empirical_baseline():
    diag = extract_simulation_diagnostics(_base_payload(), engine=ENGINE_EMPIRICAL_V1)

    assert diag["engine"] == ENGINE_EMPIRICAL_V1
    assert diag["engine_type"] == "operational"
    assert diag["prediction_validity"] == "operational_baseline"
    assert diag["time_to_alpha_0_1"] == 20.0
    assert diag["time_to_alpha_0_5"] == 40.0
    assert diag["time_to_alpha_0_9"] is None
    assert diag["alpha_mean_final"] is not None


def test_extract_simulation_diagnostics_flags_invalid_prediction_for_critical_v2_combo():
    payload = _base_payload()
    payload.update(
        {
            "times": np.asarray([0.0, 60.0], dtype=float),
            "alpha_snaps": np.zeros((2, 2, 2), dtype=np.float32),
            "process_state_final": "HEATING",
            "induction_confidence": "low",
            "induction_extrapolation_warning": True,
            "quality_status": "critical",
            "metrics": {"induction_extrapolation_level": "strong"},
        }
    )

    diag = extract_simulation_diagnostics(payload, engine=ENGINE_THERMO_KINETIC_V2)

    assert diag["engine"] == ENGINE_THERMO_KINETIC_V2
    assert diag["engine_type"] == "physical"
    assert diag["prediction_validity"] == "invalid_for_prediction"
    assert "critical_extrapolation" in str(diag["prediction_validity_reason"])


def test_build_engine_comparison_returns_side_by_side_rows():
    empirical = _base_payload()
    thermo = _base_payload()
    thermo["alpha_snaps"] = np.asarray(
        [
            [[0.0, 0.0], [0.0, 0.0]],
            [[0.05, 0.08], [0.06, 0.09]],
            [[0.2, 0.25], [0.22, 0.27]],
        ],
        dtype=np.float32,
    )
    thermo["induction_confidence"] = "low"
    thermo["induction_extrapolation_warning"] = True
    thermo["metrics"] = {"induction_extrapolation_level": "mild"}
    thermo["quality_status"] = "warning"

    comparison = build_engine_comparison(
        {
            ENGINE_EMPIRICAL_V1: empirical,
            ENGINE_THERMO_KINETIC_V2: thermo,
        }
    )

    assert comparison["available_engines"] == [ENGINE_EMPIRICAL_V1, ENGINE_THERMO_KINETIC_V2]
    rows_by_key = {row["key"]: row for row in comparison["rows"]}
    assert "alpha_mean_final" in rows_by_key
    assert "prediction_validity" in rows_by_key
    assert rows_by_key["alpha_mean_final"]["delta_v2_minus_v1"] is not None
