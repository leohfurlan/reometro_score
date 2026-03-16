import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.engine_registry import (
    ENGINE_AXISYMMETRIC_FIPY,
    ENGINE_EMPIRICAL_V1,
    ENGINE_THERMO_KINETIC_V2,
)
from services.simulation_schema import REQUIRED_FIELDS, get_schema_contract, normalize_simulation_output


def _legacy_payload():
    return {
        "sim_id": "legacy_001",
        "fit_id": "fit_legacy",
        "mode": "prensa",
        "dim": 2,
        "shape": (3, 3),
        "full_shape": (3, 3),
        "dx": 0.001,
        "dt": 1.0,
        "times": np.asarray([0.0, 1.0], dtype=float),
        "t_snaps": np.full((2, 3, 3), 298.15, dtype=np.float32),
        "alpha_snaps": np.asarray(
            [
                np.zeros((3, 3), dtype=np.float32),
                np.full((3, 3), 0.4, dtype=np.float32),
            ]
        ),
    }


def _v2_payload():
    alpha_c = np.asarray([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
    alpha_r = np.asarray([[0.01, 0.02], [0.03, 0.04]], dtype=np.float32)
    alpha = alpha_c - alpha_r
    return {
        "sim_id": "v2_001",
        "fit_id": "fit_v2",
        "mode": "autoclave",
        "dim": 1,
        "shape": (2,),
        "full_shape": (2,),
        "dx": 0.001,
        "dx_compute": 0.001,
        "times": np.asarray([0.0, 5.0], dtype=float),
        "t_snaps": np.asarray([[298.15, 299.0], [310.0, 311.0]], dtype=np.float32),
        "alpha_c_snaps": np.asarray([np.zeros((2,), dtype=np.float32), alpha_c[0]], dtype=np.float32),
        "alpha_r_snaps": np.asarray([np.zeros((2,), dtype=np.float32), alpha_r[0]], dtype=np.float32),
        "alpha_snaps": np.asarray([np.zeros((2,), dtype=np.float32), alpha[0]], dtype=np.float32),
        "heat_source_snaps": np.asarray([[0.0, 0.0], [8.0, 9.0]], dtype=np.float32),
        "process_state_over_time": np.asarray(["HEATING", "COOLING"]),
        "process_state_final": "COOLING",
        "demold_time": 5.0,
        "induction_confidence": "medium",
        "induction_source": "fit_payload",
        "induction_fit_quality": "medium",
        "temperature_validity_range": {"min_c": 150.0, "max_c": 190.0, "span_c": 40.0},
        "induction_extrapolation_warning": False,
        "induction_model_regime": "single_arrhenius",
        "quality_status": "healthy",
        "clip_events_count": 3,
        "nan_recovery_events": 1,
        "numerical_warnings": [{"code": "test_warning"}],
    }


def _axisymmetric_payload():
    alpha_c = np.asarray([[0.0, 0.0, 0.0, 0.0], [0.3, 0.4, 0.5, 0.6]], dtype=np.float32)
    alpha_r = np.asarray([[0.0, 0.0, 0.0, 0.0], [0.02, 0.02, 0.03, 0.03]], dtype=np.float32)
    return {
        "solver_family": "fipy_axisymmetric_2d",
        "geometry": "axisymmetric_cylindrical_section",
        "times": np.asarray([0.0, 1.0], dtype=float),
        "t_snaps": np.asarray([[298.15, 298.15, 298.15, 298.15], [320.0, 321.0, 322.0, 323.0]], dtype=np.float32),
        "alpha_c_snaps": alpha_c,
        "alpha_r_snaps": alpha_r,
        "alpha_snaps": np.clip(alpha_c - alpha_r, 0.0, 1.0),
        "q_source_snaps": np.asarray([[0.0, 0.0, 0.0, 0.0], [2.0, 2.5, 3.0, 3.5]], dtype=np.float32),
        "mesh": {"nr": 2, "nz": 2, "dr": 0.001, "dz": 0.001},
    }


def test_schema_contract_exposes_required_fields():
    contract = get_schema_contract()
    assert contract["required_fields"] == list(REQUIRED_FIELDS)


def test_legacy_payload_is_normalized_with_backward_compatible_defaults():
    normalized = normalize_simulation_output(_legacy_payload(), engine=ENGINE_EMPIRICAL_V1)

    for key in REQUIRED_FIELDS:
        assert key in normalized

    assert normalized["engine"] == ENGINE_EMPIRICAL_V1
    assert normalized["engine_status"] == "stable"
    assert np.asarray(normalized["alpha_c"]).shape == np.asarray(normalized["alpha"]).shape
    assert np.asarray(normalized["alpha_r"]).shape == np.asarray(normalized["alpha"]).shape
    assert np.asarray(normalized["heat_source"]).shape == np.asarray(normalized["alpha"]).shape
    assert normalized["geometry_type"] == "cartesian_2d"


def test_v2_payload_preserves_extended_fields_in_normalized_contract():
    normalized = normalize_simulation_output(_v2_payload(), engine=ENGINE_THERMO_KINETIC_V2)

    assert normalized["engine"] == ENGINE_THERMO_KINETIC_V2
    assert normalized["engine_status"] == "experimental"
    assert normalized["process_state"] == "COOLING"
    assert normalized["demold_time"] == 5.0
    assert np.isfinite(float(normalized["metrics"]["temperature_max_c"]))
    assert np.isfinite(float(normalized["metrics"]["alpha_mean"]))
    assert normalized["induction_confidence"] == "medium"
    assert normalized["induction_source"] == "fit_payload"
    assert normalized["induction_fit_quality"] == "medium"
    assert normalized["temperature_validity_range"]["span_c"] == 40.0
    assert normalized["induction_extrapolation_warning"] is False
    assert normalized["induction_model_regime"] == "single_arrhenius"
    assert normalized["quality_status"] == "healthy"
    assert normalized["clip_events_count"] == 3
    assert normalized["nan_recovery_events"] == 1
    assert normalized["metrics"]["numerical_warning_count"] == 1


def test_axisymmetric_payload_exposes_same_minimum_contract():
    normalized = normalize_simulation_output(_axisymmetric_payload(), engine=ENGINE_AXISYMMETRIC_FIPY)

    assert normalized["engine"] == ENGINE_AXISYMMETRIC_FIPY
    assert normalized["engine_status"] == "prototype"
    assert normalized["geometry_type"] == "axisymmetric_cylindrical_section"
    assert normalized["coordinates"]["system"] == "cylindrical_rz"
    assert np.asarray(normalized["alpha_c"]).ndim == 2
    assert np.asarray(normalized["heat_source"]).ndim == 2
