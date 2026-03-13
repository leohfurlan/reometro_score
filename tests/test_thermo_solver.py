import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.thermo_solver import (
    CP_RUBBER,
    LAMBDA_RUBBER,
    QV_VULCANIZATION,
    RHO_RUBBER,
    ThermalMaterial,
    explicit_heat_step,
    reaction_heat_source,
    stable_dt_limit,
    substep_config,
    thermal_increment_from_source,
)


def test_reaction_heat_source_matches_definition():
    alpha_rate = np.array([0.0, 0.1, 0.5], dtype=float)
    q_field = reaction_heat_source(alpha_rate)
    expected = RHO_RUBBER * QV_VULCANIZATION * alpha_rate

    assert np.allclose(q_field, expected)


def test_thermal_increment_from_source_matches_energy_balance():
    q_source = np.array([0.0, 1.0e6, 2.5e6], dtype=float)
    dt = 0.5
    got = thermal_increment_from_source(q_source, dt)
    expected = (q_source * dt) / (RHO_RUBBER * CP_RUBBER)

    assert np.allclose(got, expected)


def test_thermal_increment_from_source_rejects_non_positive_material_properties():
    try:
        thermal_increment_from_source(q_source=1.0e5, dt=1.0, rho=0.0, cp=CP_RUBBER)
        assert False, "Expected ValueError for non-positive rho"
    except ValueError:
        pass

    try:
        thermal_increment_from_source(q_source=1.0e5, dt=1.0, rho=RHO_RUBBER, cp=0.0)
        assert False, "Expected ValueError for non-positive cp"
    except ValueError:
        pass


def test_physical_constants_defaults_are_expected():
    assert RHO_RUBBER == 1020.0
    assert CP_RUBBER == 1820.0
    assert QV_VULCANIZATION == 13000.0
    assert LAMBDA_RUBBER > 0.0


def test_explicit_step_with_exotherm_heats_core_and_preserves_dirichlet_faces():
    material = ThermalMaterial()
    field = np.full((21,), 443.15, dtype=float)
    heat = np.zeros_like(field)
    heat[10] = 2.0e6

    updated = explicit_heat_step(
        field,
        dt=0.25,
        dx=0.002,
        material=material,
        internal_heat_w_m3=heat,
        boundary_mode="dirichlet",
        boundary_value_k=443.15,
        dirichlet_axes=(0,),
    )

    assert updated[10] > field[10]
    assert np.isclose(updated[0], 443.15)
    assert np.isclose(updated[-1], 443.15)


def test_convective_step_cools_surfaces():
    material = ThermalMaterial()
    field = np.full((21,), 473.15, dtype=float)
    heat = np.zeros_like(field)

    updated = explicit_heat_step(
        field,
        dt=1.0,
        dx=0.002,
        material=material,
        internal_heat_w_m3=heat,
        boundary_mode="convective",
        h_conv=25.0,
        ambient_k=298.15,
        convective_axes=(0,),
    )

    assert updated[0] < field[0]
    assert updated[-1] < field[-1]


def test_substep_config_splits_unstable_timestep():
    material = ThermalMaterial()
    dt_limit = stable_dt_limit(material.thermal_diffusivity, dx=0.001, dim=1)
    substeps, dt_inner = substep_config(dt=dt_limit * 3.0, alpha_diff=material.thermal_diffusivity, dx=0.001, dim=1)

    assert substeps >= 3
    assert dt_inner <= dt_limit
