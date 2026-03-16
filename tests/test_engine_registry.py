import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import engine_registry as registry


def test_default_engine_remains_legacy_when_env_points_to_experimental(monkeypatch):
    monkeypatch.setenv("REOMETRIA_DEFAULT_ENGINE", registry.ENGINE_THERMO_KINETIC_V2)
    assert registry.get_default_engine() == registry.ENGINE_EMPIRICAL_V1


def test_v2_requires_explicit_selection(monkeypatch):
    monkeypatch.setenv("REOMETRIA_ENABLE_THERMO_KINETIC_V2", "1")

    implicit = registry.resolve_engine_for_execution(
        registry.ENGINE_THERMO_KINETIC_V2,
        explicit_selection=False,
    )
    explicit = registry.resolve_engine_for_execution(
        registry.ENGINE_THERMO_KINETIC_V2,
        explicit_selection=True,
    )

    assert implicit == registry.ENGINE_EMPIRICAL_V1
    assert explicit == registry.ENGINE_THERMO_KINETIC_V2


def test_axisymmetric_requires_explicit_feature_flag(monkeypatch):
    monkeypatch.delenv("REOMETRIA_ENABLE_AXISYMMETRIC_FIPY", raising=False)
    disabled = registry.resolve_engine_for_execution(
        registry.ENGINE_AXISYMMETRIC_FIPY,
        explicit_selection=True,
    )
    assert disabled == registry.ENGINE_EMPIRICAL_V1

    monkeypatch.setenv("REOMETRIA_ENABLE_AXISYMMETRIC_FIPY", "1")
    enabled = registry.resolve_engine_for_execution(
        registry.ENGINE_AXISYMMETRIC_FIPY,
        explicit_selection=True,
    )
    assert enabled == registry.ENGINE_AXISYMMETRIC_FIPY


def test_registry_catalog_exposes_expected_statuses():
    catalog = registry.list_engine_catalog(include_disabled=True)
    by_engine = {item["engine"]: item for item in catalog}

    assert by_engine[registry.ENGINE_EMPIRICAL_V1]["status"] == registry.ENGINE_STATUS_STABLE
    assert by_engine[registry.ENGINE_THERMO_KINETIC_V2]["status"] == registry.ENGINE_STATUS_EXPERIMENTAL
    assert by_engine[registry.ENGINE_AXISYMMETRIC_FIPY]["status"] == registry.ENGINE_STATUS_PROTOTYPE

