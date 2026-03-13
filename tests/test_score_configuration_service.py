import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.score_configuration_service as score_service
from models.score_versioning import ScoreVersao
from models.usuario import db
from reoscore.webapp import create_app


def _base_rules():
    return [
        {
            "id": 1,
            "nome": "LIBERAR",
            "min_score": 80,
            "exige_visc_real": False,
            "acao": "LIBERAR",
            "cor": "success",
        }
    ]


def _base_snapshot():
    return {
        "specs": {
            "100": {
                "baixa_Dureza": {
                    "min": 50,
                    "max": 60,
                    "peso": 0,
                }
            }
        },
        "regras": _base_rules(),
        "meta": {"source_of_truth": "database"},
    }


@pytest.fixture
def score_app(tmp_path):
    db_path = tmp_path / "score_rotation.db"
    config_path = tmp_path / "config_massas.json"
    rules_path = tmp_path / "config_regras.json"
    config_path.write_text("{}", encoding="utf-8")
    rules_path.write_text("[]", encoding="utf-8")

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "SCORE_CONFIG_FILE": str(config_path),
            "SCORE_RULES_FILE": str(rules_path),
        }
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        score_service._invalidate_snapshot_cache()
        yield {
            "app": app,
            "config_path": config_path,
            "rules_path": rules_path,
        }
        db.session.remove()
        db.drop_all()
        score_service._invalidate_snapshot_cache()


def test_update_action_rules_rotates_active_version_and_preserves_specs(score_app):
    initial = score_service.create_score_version(
        snapshot=_base_snapshot(),
        nome="Base",
        descricao="Versao inicial",
    )

    rotated = score_service.update_action_rules(
        [
            {
                "id": 1,
                "nome": "BLOQUEAR",
                "min_score": 90,
                "exige_visc_real": True,
                "acao": "BLOQUEAR",
                "cor": "danger",
            }
        ],
        descricao="Rotacao de regras",
    )

    db.session.refresh(initial)
    db.session.refresh(rotated)

    active_snapshot = score_service.get_active_score_snapshot(create_from_legacy=False)
    exported_rules = json.loads(score_app["rules_path"].read_text(encoding="utf-8"))

    assert initial.status == "ARCHIVED"
    assert rotated.status == "ACTIVE"
    assert rotated.config_snapshot["meta"]["previous_active_version_id"] == initial.id
    assert active_snapshot["specs"]["100"]["baixa_Dureza"]["min"] == 50
    assert active_snapshot["regras"][0]["acao"] == "BLOQUEAR"
    assert exported_rules[0]["acao"] == "BLOQUEAR"


def test_update_material_specs_rotates_active_version_and_preserves_rules(score_app):
    initial = score_service.create_score_version(
        snapshot=_base_snapshot(),
        nome="Base",
        descricao="Versao inicial",
    )

    rotated = score_service.update_material_specs(
        "200",
        {
            "alta_Ts2": {
                "min": 1.2,
                "alvo": 1.5,
                "max": 1.8,
                "peso": 3,
            }
        },
        descricao="Rotacao de specs",
    )

    db.session.refresh(initial)
    db.session.refresh(rotated)

    active_snapshot = score_service.get_active_score_snapshot(create_from_legacy=False)
    exported_specs = json.loads(score_app["config_path"].read_text(encoding="utf-8"))

    assert initial.status == "ARCHIVED"
    assert rotated.status == "ACTIVE"
    assert rotated.config_snapshot["meta"]["previous_active_version_id"] == initial.id
    assert active_snapshot["specs"]["200"]["alta_Ts2"]["alvo"] == 1.5
    assert active_snapshot["regras"][0]["acao"] == "LIBERAR"
    assert exported_specs["200"]["alta_Ts2"]["max"] == 1.8


def test_get_active_score_version_normalizes_multiple_active_statuses(score_app):
    older = ScoreVersao(
        nome="Legado",
        status="ATIVA",
        config_snapshot=_base_snapshot(),
        ativado_em=None,
    )
    newer = ScoreVersao(
        nome="Atual",
        status="ACTIVE",
        config_snapshot=_base_snapshot(),
        ativado_em=None,
    )
    db.session.add_all([older, newer])
    db.session.commit()

    active = score_service.get_active_score_version(create_from_legacy=False)

    db.session.refresh(older)
    db.session.refresh(newer)

    assert active.id == newer.id
    assert older.status == "ARCHIVED"
    assert newer.status == "ACTIVE"
    assert newer.ativado_em is not None
    assert newer.config_snapshot["meta"]["source_of_truth"] == "database"
