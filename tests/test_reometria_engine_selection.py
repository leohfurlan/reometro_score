import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.modules.setdefault("pyodbc", types.SimpleNamespace(connect=lambda *args, **kwargs: None))

from reoscore.webapp.routes import reometria as rr


def _fit_payload():
    return {"fit_id": "fit_test", "k0": 1e6, "Ea": 80000.0, "n": 1.2}


def _base_sim_params():
    return {
        "mode": "prensa",
        "dim": 1,
        "shape_raw": "20",
        "dx": 0.001,
        "dt": 1.0,
        "t_end": 20.0,
        "mold_temp_c": 170.0,
        "init_temp_c": 25.0,
        "ramp_rate": 0.0,
        "snapshot_every": 1,
        "platen_axis": None,
    }


def test_dispatch_defaults_to_v1_engine(monkeypatch):
    calls = {"v1": 0, "v2": 0}

    def _run_v1(*args, **kwargs):
        calls["v1"] += 1
        return "sim_v1", "path_v1"

    def _run_v2(*args, **kwargs):
        calls["v2"] += 1
        return "sim_v2", "path_v2"

    monkeypatch.setattr(rr, "run_simulation_v1", _run_v1)
    monkeypatch.setattr(rr, "run_simulation_v2", _run_v2)

    sim_id, _ = rr._run_simulation_with_engine(_fit_payload(), _base_sim_params())

    assert sim_id == "sim_v1"
    assert calls["v1"] == 1
    assert calls["v2"] == 0


def test_dispatch_can_trigger_v2_engine(monkeypatch):
    calls = {"v1": 0, "v2": 0}

    def _run_v1(*args, **kwargs):
        calls["v1"] += 1
        return "sim_v1", "path_v1"

    def _run_v2(*args, **kwargs):
        calls["v2"] += 1
        return "sim_v2", "path_v2"

    monkeypatch.setattr(rr, "run_simulation_v1", _run_v1)
    monkeypatch.setattr(rr, "run_simulation_v2", _run_v2)
    monkeypatch.setenv("REOMETRIA_ENABLE_THERMO_KINETIC_V2", "1")

    params = _base_sim_params()
    params["engine"] = rr.ENGINE_THERMO_KINETIC_V2
    sim_id, _ = rr._run_simulation_with_engine(_fit_payload(), params)

    assert sim_id == "sim_v2"
    assert calls["v1"] == 0
    assert calls["v2"] == 1


def test_load_simulation_with_engine_falls_back_to_v2(monkeypatch):
    def _load_v1(_sim_id):
        return None

    def _load_v2(_sim_id):
        return {"sim_id": _sim_id, "source": "v2"}

    monkeypatch.setattr(rr, "load_simulation_v1", _load_v1)
    monkeypatch.setattr(rr, "load_simulation_v2", _load_v2)

    sim, engine = rr._load_simulation_with_engine("abc123", engine_hint=rr.ENGINE_EMPIRICAL_V1)

    assert sim is not None
    assert sim["source"] == "v2"
    assert engine == rr.ENGINE_THERMO_KINETIC_V2


def test_engine_resolution_keeps_v2_out_of_default_path(monkeypatch):
    monkeypatch.setenv("REOMETRIA_ENABLE_THERMO_KINETIC_V2", "1")

    implicit = rr._resolve_engine(rr.ENGINE_THERMO_KINETIC_V2, explicit_selection=False)
    explicit = rr._resolve_engine(rr.ENGINE_THERMO_KINETIC_V2, explicit_selection=True)

    assert implicit == rr.ENGINE_EMPIRICAL_V1
    assert explicit == rr.ENGINE_THERMO_KINETIC_V2


def test_axisymmetric_engine_requires_feature_flag(monkeypatch):
    monkeypatch.delenv("REOMETRIA_ENABLE_AXISYMMETRIC_FIPY", raising=False)
    disabled = rr._resolve_engine(rr.ENGINE_AXISYMMETRIC_FIPY, explicit_selection=True)
    assert disabled == rr.ENGINE_EMPIRICAL_V1

    monkeypatch.setenv("REOMETRIA_ENABLE_AXISYMMETRIC_FIPY", "1")
    enabled = rr._resolve_engine(rr.ENGINE_AXISYMMETRIC_FIPY, explicit_selection=True)
    assert enabled == rr.ENGINE_AXISYMMETRIC_FIPY


def test_axisymmetric_engine_is_blocked_in_web_execution_path(monkeypatch):
    monkeypatch.setenv("REOMETRIA_ENABLE_AXISYMMETRIC_FIPY", "1")
    params = _base_sim_params()
    params["engine"] = rr.ENGINE_AXISYMMETRIC_FIPY

    try:
        rr._run_simulation_with_engine(_fit_payload(), params)
        assert False, "Expected prototype guard rail for axisymmetric engine"
    except RuntimeError as exc:
        assert "prototype" in str(exc).lower()
