import json
import os
import sys
import types
from pathlib import Path

import numpy as np
from flask import Blueprint, Flask

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.modules.setdefault("pyodbc", types.SimpleNamespace(connect=lambda *args, **kwargs: None))

from reoscore.webapp.routes import reometria as rr
from reoscore.webapp.extensions import login_manager


def _create_test_app():
    project_root = Path(__file__).resolve().parents[1]
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LOGIN_DISABLED=True,
    )
    login_manager.init_app(app)

    @app.route("/dashboard", endpoint="dashboard_home")
    def _dashboard_home():
        return "ok"

    @app.route("/controle", endpoint="controle_qualidade")
    def _controle_qualidade():
        return "ok"

    @app.route("/tendencias", endpoint="analise_tendencias")
    def _analise_tendencias():
        return "ok"

    @app.route("/relatorios", endpoint="pagina_relatorios")
    def _pagina_relatorios():
        return "ok"

    @app.route("/formulas", endpoint="lista_formulas")
    def _lista_formulas():
        return "ok"

    @app.route("/simulador", endpoint="simulador")
    def _simulador():
        return "ok"

    @app.route("/contratos-api", endpoint="pagina_contratos_api")
    def _pagina_contratos_api():
        return "ok"

    admin_bp = Blueprint("admin", __name__)

    @admin_bp.route("/admin/auditoria", endpoint="pagina_auditoria")
    def _pagina_auditoria():
        return "ok"

    @admin_bp.route("/admin/config", endpoint="pagina_config")
    def _pagina_config():
        return "ok"

    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/auth/logout", endpoint="logout")
    def _logout():
        return "ok"

    @auth_bp.route("/auth/login", endpoint="login")
    def _login():
        return "ok"

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(rr.reometria_bp)
    return app


def _base_payload():
    return {
        "fit_id": "fit_test",
        "success": True,
        "model_family": "pinheiro_sigmoidal_teq_v1",
        "model_version": "v1",
        "fit_method": "least_squares",
        "reference_temperature_K": 433.15,
        "cure_completion_rule": "alpha_practical_0_99",
        "k_ref": 1e-4,
        "k0": 1e6,
        "Ea": 80000.0,
        "n": 1.2,
        "rmse_alpha": 0.12345,
        "ensaios": [{"COD_ENSAIO": 123, "T_C": 170.0}],
        "curves": [
            {
                "COD_ENSAIO": 123,
                "T_C": 170.0,
                "tempo": [0, 10, 20],
                "torque": [1.0, 2.0, 3.0],
                "t_rel": [0, 10, 20],
                "alpha": [0.0, 0.5, 0.9],
            }
        ],
        "created_at": "2026-03-16T12:00:00+00:00",
    }


def test_reometria_fit_update_meta_success(monkeypatch):
    app = _create_test_app()
    payload = {"fit_id": "fit_meta_ok", "k0": 5.0, "metadata": {"origem": "teste"}}
    saved = {}

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: payload)

    def _save_fit_payload(updated_payload):
        saved["payload"] = updated_payload
        return "saved"

    monkeypatch.setattr(rr, "save_fit_payload", _save_fit_payload)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update_meta/fit_meta_ok",
        json={"titulo": "Estudo A", "observacoes": "Obs tecnica"},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert saved["payload"]["metadata"]["origem"] == "teste"
    assert saved["payload"]["metadata"]["titulo"] == "Estudo A"
    assert saved["payload"]["metadata"]["observacoes"] == "Obs tecnica"
    assert saved["payload"]["k0"] == 5.0


def test_reometria_fit_update_meta_returns_404_when_fit_missing(monkeypatch):
    app = _create_test_app()
    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: None)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update_meta/fit_inexistente",
        json={"titulo": "x", "observacoes": "y"},
    )

    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_reometria_fit_update_meta_returns_400_for_invalid_json(monkeypatch):
    app = _create_test_app()
    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: {"fit_id": _fit_id})

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update_meta/fit_meta_bad",
        data='{"titulo": ',
        content_type="application/json",
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_reometria_fit_update_accepts_k_ref_for_pinheiro(monkeypatch):
    app = _create_test_app()
    current_payload = {
        "fit_id": "fit_update_pinheiro",
        "model_family": "pinheiro_sigmoidal_teq_v1",
        "k_ref": 1.0e-4,
        "k0": 1.0e-4,
        "Ea": 8.0e4,
        "n": 1.2,
    }

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(current_payload))

    def _update_fit_parameters(_fit_id, k_value, ea, n):
        out = dict(current_payload)
        out.update({"k_ref": float(k_value), "k0": float(k_value), "Ea": float(ea), "n": float(n)})
        return out

    monkeypatch.setattr(rr, "update_fit_parameters", _update_fit_parameters)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update/fit_update_pinheiro",
        json={"k_ref": 2.5e-4, "Ea": 85000.0, "n": 1.5},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert abs(float(data["k_ref"]) - 2.5e-4) < 1e-12
    assert abs(float(data["k0"]) - 2.5e-4) < 1e-12
    assert abs(float(data["Ea"]) - 85000.0) < 1e-9
    assert abs(float(data["n"]) - 1.5) < 1e-9


def test_reometria_fit_update_accepts_k0_for_edo(monkeypatch):
    app = _create_test_app()
    current_payload = {
        "fit_id": "fit_update_edo",
        "model_family": "edo_order_n_v1",
        "k0": 3.0e-3,
        "Ea": 7.0e4,
        "n": 1.0,
    }

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(current_payload))

    def _update_fit_parameters(_fit_id, k_value, ea, n):
        out = dict(current_payload)
        out.update({"k0": float(k_value), "Ea": float(ea), "n": float(n)})
        return out

    monkeypatch.setattr(rr, "update_fit_parameters", _update_fit_parameters)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update/fit_update_edo",
        json={"k0": 9.0e-3, "Ea": 72000.0, "n": 1.3},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert abs(float(data["k0"]) - 9.0e-3) < 1e-12
    assert abs(float(data["Ea"]) - 72000.0) < 1e-9
    assert abs(float(data["n"]) - 1.3) < 1e-9


def test_reometria_fit_update_accepts_leroy_parameters(monkeypatch):
    app = _create_test_app()
    current_payload = {
        "fit_id": "fit_update_leroy",
        "model_family": "leroy2013_continuous_v1",
        "Av1": 0.01,
        "Av2": 0.04,
        "Ev": 90000.0,
        "X": 0.65,
        "Ar": 0.20,
        "Er": 140000.0,
        "calibration_stage_selected": "stage1",
        "identifiability": {"status": "warning"},
        "alerts": ["identifiability_warning"],
    }

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(current_payload))

    def _update_fit_parameters(_fit_id, *args, **kwargs):
        out = dict(current_payload)
        updates = dict(kwargs.get("parameter_updates") or {})
        out.update(updates)
        return out

    monkeypatch.setattr(rr, "update_fit_parameters", _update_fit_parameters)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update/fit_update_leroy",
        json={
            "Av1": 0.015,
            "Av2": 0.050,
            "Ev": 92000.0,
            "X": 0.70,
            "Ar": 0.25,
            "Er": 150000.0,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert abs(float(data["Av1"]) - 0.02) < 1e-12
    assert abs(float(data["Av2"]) - 0.050) < 1e-12
    assert abs(float(data["Ev"]) - 92000.0) < 1e-9
    assert abs(float(data["X"]) - 0.70) < 1e-12
    assert abs(float(data["Ar"]) - 0.25) < 1e-12
    assert abs(float(data["Er"]) - 150000.0) < 1e-9


def test_reometria_fit_update_accepts_kamal_parameters(monkeypatch):
    app = _create_test_app()
    current_payload = {
        "fit_id": "fit_update_kamal",
        "model_family": "kamal_sourour_expanded_v1",
        "k1": 0.002,
        "k2": 0.006,
        "Ea": 78000.0,
        "m": 1.2,
        "n": 1.4,
        "k_march": 0.02,
        "k_rev": 0.03,
        "beta_rev": 0.01,
    }

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(current_payload))

    def _update_fit_parameters(_fit_id, *args, **kwargs):
        out = dict(current_payload)
        updates = dict(kwargs.get("parameter_updates") or {})
        out.update(updates)
        return out

    monkeypatch.setattr(rr, "update_fit_parameters", _update_fit_parameters)

    client = app.test_client()
    response = client.post(
        "/reometria/fit/update/fit_update_kamal",
        json={
            "k1": 0.003,
            "k2": 0.008,
            "Ea": 79000.0,
            "m": 1.4,
            "n": 1.6,
            "k_march": 0.03,
            "k_rev": 0.02,
            "beta_rev": 0.02,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert abs(float(data["k1"]) - 0.003) < 1e-12
    assert abs(float(data["k2"]) - 0.008) < 1e-12
    assert abs(float(data["Ea"]) - 79000.0) < 1e-9
    assert abs(float(data["m"]) - 1.4) < 1e-12
    assert abs(float(data["n"]) - 1.6) < 1e-12
    assert abs(float(data["k_march"]) - 0.03) < 1e-12
    assert abs(float(data["k_rev"]) - 0.02) < 1e-12
    assert abs(float(data["beta_rev"]) - 0.02) < 1e-12


def test_reometria_fit_result_renders_leroy_without_legacy_parameters(monkeypatch):
    app = _create_test_app()
    payload = _base_payload()
    payload.update(
        {
            "fit_id": "fit_result_leroy",
            "model_family": "leroy2013_continuous_v1",
            "Av1": 0.01,
            "Av2": 0.04,
            "Ev": 90000.0,
            "X": 0.65,
            "Ar": 0.20,
            "Er": 140000.0,
            "calibration_stage_selected": "stage1",
        }
    )
    payload.pop("Ea", None)
    payload.pop("n", None)
    payload.pop("k0", None)
    payload.pop("k_ref", None)

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(payload))
    monkeypatch.setattr(rr, "build_alpha_time_comparison", lambda _payload: [])
    monkeypatch.setattr(rr, "list_simulations_by_fit", lambda _fit_id: [])

    client = app.test_client()
    response = client.get("/reometria/fit/result/fit_result_leroy")

    assert response.status_code == 200
    assert b"leroy2013_continuous_v1" in response.data


def test_list_simulations_by_fit_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)
    assert rr.list_simulations_by_fit("fit_any") == []


def test_list_simulations_by_fit_returns_only_matching_fit(tmp_path, monkeypatch):
    (tmp_path / "sim_20260310101010_aaa11111.npz").write_bytes(b"v1")
    (tmp_path / "sim_v2_20260310121212_bbb22222.npz").write_bytes(b"v2")
    (tmp_path / "sim_20260310111111_ccc33333.npz").write_bytes(b"other")
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)

    def _loader(sim_id, engine_hint=None):
        if sim_id.endswith("aaa11111"):
            return (
                {
                    "sim_id": sim_id,
                    "fit_id": "fit_target",
                    "mode": "prensa",
                    "mold_temp_c": 170.0,
                    "created_at": "2026-03-10T10:10:10+00:00",
                },
                rr.ENGINE_EMPIRICAL_V1,
            )
        if sim_id.endswith("bbb22222"):
            return (
                {
                    "sim_id": sim_id,
                    "fit_id": "fit_target",
                    "mode": "autoclave",
                    "mold_temp_c": 180.0,
                    "created_at": "2026-03-10T12:12:12+00:00",
                },
                rr.ENGINE_THERMO_KINETIC_V2,
            )
        return (
            {
                "sim_id": sim_id,
                "fit_id": "fit_other",
                "mode": "prensa",
                "mold_temp_c": 175.0,
                "created_at": "2026-03-10T11:11:11+00:00",
            },
            rr.ENGINE_EMPIRICAL_V1,
        )

    monkeypatch.setattr(rr, "_load_simulation_normalized_with_fallback", _loader)

    items = rr.list_simulations_by_fit("fit_target")
    assert len(items) == 2
    assert {item["fit_id"] for item in items} == {"fit_target"}
    assert {item["sim_id"] for item in items} == {"20260310101010_aaa11111", "20260310121212_bbb22222"}


def test_list_simulations_by_fit_ignores_corrupted_file(tmp_path, monkeypatch):
    (tmp_path / "sim_20260310101010_bad00001.npz").write_bytes(b"broken")
    (tmp_path / "sim_20260310121212_ok000002.npz").write_bytes(b"ok")
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)

    def _loader(sim_id, engine_hint=None):
        if sim_id.endswith("bad00001"):
            raise ValueError("corrupted")
        return (
            {
                "sim_id": sim_id,
                "fit_id": "fit_ok",
                "mode": "prensa",
                "mold_temp_c": 170.0,
                "created_at": "2026-03-10T12:12:12+00:00",
            },
            rr.ENGINE_EMPIRICAL_V1,
        )

    monkeypatch.setattr(rr, "_load_simulation_normalized_with_fallback", _loader)

    items = rr.list_simulations_by_fit("fit_ok")
    assert len(items) == 1
    assert items[0]["sim_id"] == "20260310121212_ok000002"


def test_list_simulations_by_fit_orders_newest_first(tmp_path, monkeypatch):
    (tmp_path / "sim_20260310101010_old11111.npz").write_bytes(b"old")
    (tmp_path / "sim_20260310151515_new22222.npz").write_bytes(b"new")
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)

    def _loader(sim_id, engine_hint=None):
        if sim_id.endswith("old11111"):
            created = "2026-03-10T10:10:10+00:00"
        else:
            created = "2026-03-10T15:15:15+00:00"
        return (
            {
                "sim_id": sim_id,
                "fit_id": "fit_sorted",
                "mode": "prensa",
                "mold_temp_c": 170.0,
                "created_at": created,
            },
            rr.ENGINE_EMPIRICAL_V1,
        )

    monkeypatch.setattr(rr, "_load_simulation_normalized_with_fallback", _loader)

    items = rr.list_simulations_by_fit("fit_sorted")
    assert len(items) == 2
    assert items[0]["sim_id"] == "20260310151515_new22222"
    assert items[1]["sim_id"] == "20260310101010_old11111"


def test_list_fit_reports_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)
    assert rr.list_fit_reports() == []


def test_list_fit_reports_ignores_corrupted_and_orders_desc(tmp_path, monkeypatch):
    (tmp_path / "fit_20260310101010_old11111.json").write_text(
        json.dumps(
            {
                "fit_id": "20260310101010_old11111",
                "created_at": "2026-03-10T10:10:10+00:00",
                "success": True,
                "rmse_alpha": 0.111,
                "ensaios": [{"COD_ENSAIO": 101}],
                "metadata": {"titulo": "Fit antigo"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fit_20260310151515_new22222.json").write_text(
        json.dumps(
            {
                "fit_id": "20260310151515_new22222",
                "created_at": "2026-03-10T15:15:15+00:00",
                "success": False,
                "rmse_alpha": 0.222,
                "ensaios": [{"COD_ENSAIO": 202}, {"COD_ENSAIO": 203}],
                "metadata": {"titulo": "Fit novo"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fit_20260310121212_broken3333.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(rr, "_REOMETRIA_OUT_DIR", tmp_path)

    items = rr.list_fit_reports()
    assert len(items) == 2
    assert items[0]["fit_id"] == "20260310151515_new22222"
    assert items[1]["fit_id"] == "20260310101010_old11111"
    assert items[0]["status"] == "Falhou"
    assert items[1]["status"] == "OK"


def test_reometria_fit_renders_history_section(monkeypatch):
    app = _create_test_app()
    monkeypatch.setattr(rr, "list_ensaios_for_fit", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        rr,
        "list_fit_reports",
        lambda **_kwargs: [
            {
                "fit_id": "20260310151515_new22222",
                "titulo": "Estudo de Cura A",
                "created_at_display": "2026-03-10T15:15:15+00:00",
                "rmse_alpha": 0.123,
                "status": "OK",
                "success": True,
                "ensaios_count": 3,
            }
        ],
    )

    client = app.test_client()
    response = client.get("/reometria/fit")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Historico de Estudos Cineticos" in body
    assert "20260310151515_new22222" in body
    assert "Abrir Relatorio" in body


def test_reometria_fit_form_lists_leroy_model_option(monkeypatch):
    app = _create_test_app()
    monkeypatch.setattr(rr, "list_ensaios_for_fit", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rr, "list_fit_reports", lambda **_kwargs: [])

    client = app.test_client()
    response = client.get("/reometria/fit")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'value="leroy2013_continuous_v1"' in body
    assert 'value="kamal_sourour_expanded_v1"' in body


def test_fit_result_renders_empty_simulation_state(monkeypatch):
    app = _create_test_app()
    payload = _base_payload()
    alpha_comp = [{"COD_ENSAIO": 123, "T_C": 170.0, "comparisons": []}]

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: payload)
    monkeypatch.setattr(rr, "build_alpha_time_comparison", lambda _payload: alpha_comp)
    monkeypatch.setattr(rr, "list_simulations_by_fit", lambda _fit_id: [])

    client = app.test_client()
    response = client.get("/reometria/fit/result/fit_test")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Nenhuma simulacao encontrada para este fit." in body
    assert 'id="alphaComparisonWrap"' in body


def test_fit_result_renders_simulation_rows(monkeypatch):
    app = _create_test_app()
    payload = _base_payload()
    alpha_comp = [{"COD_ENSAIO": 123, "T_C": 170.0, "comparisons": []}]
    sims = [
        {
            "sim_id": "20260310151515_new22222",
            "fit_id": "fit_test",
            "engine": "thermo_kinetic_v2",
            "engine_status": "experimental",
            "mode": "prensa",
            "mold_temp_c": 170.0,
            "status": "concluida",
            "created_at_display": "2026-03-10 15:15:15+00:00",
        }
    ]

    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: payload)
    monkeypatch.setattr(rr, "build_alpha_time_comparison", lambda _payload: alpha_comp)
    monkeypatch.setattr(rr, "list_simulations_by_fit", lambda _fit_id: sims)

    client = app.test_client()
    response = client.get("/reometria/fit/result/fit_test")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "20260310151515_new22222" in body
    assert "Visualizar Resultados" in body


def _sample_simulation_payload(sim_id="20260317144831_ef9ca559", fit_id="fit_test"):
    return {
        "sim_id": sim_id,
        "fit_id": fit_id,
        "engine": rr.ENGINE_EMPIRICAL_V1,
        "engine_status": "stable",
        "mode": "prensa",
        "dim": 2,
        "shape": [3, 3],
        "dx": 0.001,
        "times": [0.0, 30.0, 60.0, 90.0, 120.0],
        "t_snaps": [
            [[300.0, 300.0, 300.0], [300.0, 300.0, 300.0], [300.0, 300.0, 300.0]],
            [[320.0, 322.0, 324.0], [321.0, 323.0, 325.0], [322.0, 324.0, 326.0]],
            [[330.0, 332.0, 334.0], [331.0, 333.0, 335.0], [332.0, 334.0, 336.0]],
            [[340.0, 342.0, 344.0], [341.0, 343.0, 345.0], [342.0, 344.0, 346.0]],
            [[350.0, 352.0, 354.0], [351.0, 353.0, 355.0], [352.0, 354.0, 356.0]],
        ],
        "alpha_snaps": [
            [[0.01, 0.02, 0.03], [0.02, 0.03, 0.04], [0.03, 0.04, 0.05]],
            [[0.10, 0.12, 0.14], [0.11, 0.13, 0.15], [0.12, 0.14, 0.16]],
            [[0.22, 0.24, 0.26], [0.23, 0.25, 0.27], [0.24, 0.26, 0.28]],
            [[0.34, 0.36, 0.38], [0.35, 0.37, 0.39], [0.36, 0.38, 0.40]],
            [[0.44, 0.46, 0.48], [0.45, 0.47, 0.49], [0.46, 0.48, 0.50]],
        ],
    }


def test_reometria_simulate_view_renders_report_actions(monkeypatch):
    app = _create_test_app()
    sim_payload = _sample_simulation_payload()

    monkeypatch.setattr(rr, "_load_simulation_with_engine", lambda _sim_id, engine_hint=None: (dict(sim_payload), rr.ENGINE_EMPIRICAL_V1))
    monkeypatch.setattr(rr, "normalize_simulation_output", lambda payload, engine: payload)

    client = app.test_client()
    response = client.get("/reometria/simulate/view/20260317144831_ef9ca559")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Salvar simulacao (JSON)" in body
    assert "Gerar relatorio com steps selecionados" in body
    assert 'id="report_step_1"' in body
    assert "/reometria/simulate/report/20260317144831_ef9ca559" in body


def test_reometria_simulate_export_returns_json_attachment(monkeypatch):
    app = _create_test_app()
    sim_payload = _sample_simulation_payload()
    fit_payload = {"fit_id": "fit_test", "model_family": "edo_order_n_v1", "Ea": 80000.0, "n": 1.2}

    monkeypatch.setattr(rr, "_load_simulation_with_engine", lambda _sim_id, engine_hint=None: (dict(sim_payload), rr.ENGINE_EMPIRICAL_V1))
    monkeypatch.setattr(rr, "normalize_simulation_output", lambda payload, engine: payload)
    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(fit_payload))

    client = app.test_client()
    response = client.get("/reometria/simulate/export/20260317144831_ef9ca559")

    assert response.status_code == 200
    assert "attachment; filename=\"reometria_sim_20260317144831_ef9ca559.json\"" in response.headers.get("Content-Disposition", "")
    exported = json.loads(response.get_data(as_text=True))
    assert exported["simulation"]["sim_id"] == "20260317144831_ef9ca559"
    assert exported["fit"]["fit_id"] == "fit_test"


def test_reometria_simulate_report_renders_selected_steps_and_fit_data(monkeypatch):
    app = _create_test_app()
    sim_payload = _sample_simulation_payload()
    fit_payload = {
        "fit_id": "fit_test",
        "model_family": "leroy2013_continuous_v1",
        "fit_method": "least_squares",
        "model_version": "v1",
        "Av1": 0.01,
        "Av2": 0.04,
        "Ev": 90000.0,
        "X": 0.65,
        "Ar": 0.20,
        "Er": 140000.0,
        "metadata": {"titulo": "Estudo Report", "observacoes": "Observacao tecnica do fit"},
        "curves": [
            {
                "COD_ENSAIO": 123,
                "T_C": 170.0,
                "tempo": [0.0, 10.0, 20.0],
                "torque": [1.0, 2.0, 3.0],
                "t_rel": [0.0, 10.0, 20.0],
                "alpha": [0.0, 0.4, 0.8],
                "alpha_model": [0.0, 0.45, 0.82],
                "ML": 1.0,
                "MH": np.asarray([4.0, 4.2], dtype=float),
            }
        ],
    }

    monkeypatch.setattr(rr, "_load_simulation_with_engine", lambda _sim_id, engine_hint=None: (dict(sim_payload), rr.ENGINE_EMPIRICAL_V1))
    monkeypatch.setattr(rr, "normalize_simulation_output", lambda payload, engine: payload)
    monkeypatch.setattr(rr, "load_fit_payload", lambda _fit_id: dict(fit_payload))

    client = app.test_client()
    response = client.get("/reometria/simulate/report/20260317144831_ef9ca559?steps=0,2,4")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Relatorio de Simulacao 20260317144831_ef9ca559" in body
    assert "Observacao tecnica do fit" in body
    assert "report_alpha_curves" in body
    assert "report_step_cure_0" in body
