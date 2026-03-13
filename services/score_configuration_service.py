import copy
import json
import os
from datetime import datetime

from flask import current_app, has_app_context

from models.score_versioning import ScoreVersao
from models.usuario import db

CONFIG_FILE = "config_massas.json"
REGRAS_FILE = "config_regras.json"
ACTIVE_STATUSES = {"ACTIVE", "ATIVA", "ATIVO"}

_SNAPSHOT_CACHE = None
_SNAPSHOT_CACHE_VERSION_ID = None
_SNAPSHOT_CACHE_DB_URI = None


def _safe_read_json(path, default):
    if not os.path.exists(path):
        return copy.deepcopy(default)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print(f"[WARN] Falha ao ler '{path}': {exc}")
        return copy.deepcopy(default)

    if isinstance(default, dict) and isinstance(data, dict):
        return data
    if isinstance(default, list) and isinstance(data, list):
        return data
    return copy.deepcopy(default)


def _safe_write_json(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4, ensure_ascii=False)


def _default_rules():
    return [
        {
            "id": 1,
            "nome": "MASSA PRIME",
            "min_score": 85,
            "exige_visc_real": True,
            "acao": "LIBERAR - MASSA PRIME",
            "cor": "success",
        },
        {
            "id": 2,
            "nome": "LIBERACAO PADRAO",
            "min_score": 75,
            "exige_visc_real": False,
            "acao": "LIBERAR",
            "cor": "success",
        },
        {
            "id": 3,
            "nome": "RESSALVA TECNICA",
            "min_score": 70,
            "exige_visc_real": False,
            "acao": "LIBERAR COM RESSALVA",
            "cor": "warning",
        },
        {
            "id": 4,
            "nome": "RECUPERACAO",
            "min_score": 68,
            "exige_visc_real": False,
            "acao": "CORTAR E MISTURAR",
            "cor": "dark",
        },
        {
            "id": 5,
            "nome": "REPROVACAO",
            "min_score": 0,
            "exige_visc_real": False,
            "acao": "REPROVAR",
            "cor": "danger",
        },
    ]


def _normalize_rules(rules):
    if not isinstance(rules, list):
        rules = []

    normalized = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            continue
        item = dict(rule)
        item["id"] = item.get("id") or index
        try:
            item["min_score"] = float(item.get("min_score", 0) or 0)
        except Exception:
            item["min_score"] = 0.0
        item["exige_visc_real"] = bool(item.get("exige_visc_real", False))
        item["nome"] = str(item.get("nome", f"REGRA {index}") or f"REGRA {index}").strip()
        item["acao"] = str(item.get("acao", "REPROVAR") or "REPROVAR").strip()
        item["cor"] = str(item.get("cor", "secondary") or "secondary").strip()
        normalized.append(item)

    if not normalized:
        normalized = _default_rules()

    normalized.sort(key=lambda item: item.get("min_score", 0), reverse=True)
    return normalized


def _normalize_snapshot(snapshot):
    base = snapshot if isinstance(snapshot, dict) else {}
    specs = base.get("specs")
    regras = base.get("regras")
    meta = base.get("meta")

    normalized = {
        "specs": copy.deepcopy(specs if isinstance(specs, dict) else {}),
        "regras": _normalize_rules(regras),
        "meta": copy.deepcopy(meta if isinstance(meta, dict) else {}),
    }
    return normalized


def _clone_snapshot(snapshot):
    return copy.deepcopy(_normalize_snapshot(snapshot))


def _normalize_status(value):
    return str(value or "").strip().upper()


def _invalidate_snapshot_cache():
    global _SNAPSHOT_CACHE, _SNAPSHOT_CACHE_VERSION_ID, _SNAPSHOT_CACHE_DB_URI
    _SNAPSHOT_CACHE = None
    _SNAPSHOT_CACHE_VERSION_ID = None
    _SNAPSHOT_CACHE_DB_URI = None


def _legacy_file_paths():
    config_path = CONFIG_FILE
    rules_path = REGRAS_FILE

    if has_app_context():
        config_path = current_app.config.get("SCORE_CONFIG_FILE", config_path)
        rules_path = current_app.config.get("SCORE_RULES_FILE", rules_path)

    return config_path, rules_path


def _current_database_key():
    if not has_app_context():
        return None
    return current_app.config.get("SQLALCHEMY_DATABASE_URI")


def load_legacy_seed_snapshot():
    config_path, rules_path = _legacy_file_paths()
    snapshot = {
        "specs": _safe_read_json(config_path, {}),
        "regras": _normalize_rules(_safe_read_json(rules_path, [])),
        "meta": {
            "source_of_truth": "legacy_json_seed",
            "seed_files": [config_path, rules_path],
            "loaded_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    return _normalize_snapshot(snapshot)


def export_snapshot_to_legacy_json(snapshot=None):
    config_path, rules_path = _legacy_file_paths()
    effective = _clone_snapshot(snapshot or get_active_score_snapshot(create_from_legacy=False))
    _safe_write_json(config_path, effective.get("specs", {}))
    _safe_write_json(rules_path, effective.get("regras", []))


def get_active_score_version(create_from_legacy=True):
    if not has_app_context():
        return None

    try:
        versions = ScoreVersao.query.order_by(ScoreVersao.id.desc()).all()
    except Exception as exc:
        print(f"[WARN] Falha ao consultar ScoreVersao: {exc}")
        return None

    active_versions = [item for item in versions if _normalize_status(item.status) in ACTIVE_STATUSES]

    if active_versions:
        current = active_versions[0]
        changed = False
        normalized_snapshot = _normalize_snapshot(current.config_snapshot)

        if current.status != "ACTIVE":
            current.status = "ACTIVE"
            changed = True
        if not current.ativado_em:
            current.ativado_em = datetime.now()
            changed = True
        if normalized_snapshot.get("meta", {}).get("source_of_truth") != "database":
            normalized_snapshot.setdefault("meta", {})["source_of_truth"] = "database"
            current.config_snapshot = normalized_snapshot
            changed = True

        for extra in active_versions[1:]:
            if extra.status != "ARCHIVED":
                extra.status = "ARCHIVED"
                changed = True

        if changed:
            db.session.commit()

        return current

    if not create_from_legacy:
        return None

    print("[WARN] Nenhuma ScoreVersao ACTIVE encontrada. Criando bootstrap a partir dos arquivos JSON legados.")
    snapshot = load_legacy_seed_snapshot()
    snapshot["meta"].update(
        {
            "source_of_truth": "database",
            "bootstrap_origin": "legacy_json_seed",
            "descricao": "Bootstrap automatico por ausencia de versao ativa",
        }
    )
    return create_score_version(
        snapshot=snapshot,
        nome=f"Bootstrap {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        descricao="Bootstrap automatico por ausencia de versao ativa",
        source="legacy_bootstrap",
        activate=True,
        sync_legacy_export=True,
    )


def get_active_score_snapshot(create_from_legacy=True):
    global _SNAPSHOT_CACHE, _SNAPSHOT_CACHE_VERSION_ID, _SNAPSHOT_CACHE_DB_URI

    active_version = get_active_score_version(create_from_legacy=create_from_legacy)
    if active_version is None:
        if create_from_legacy:
            return load_legacy_seed_snapshot()
        return _normalize_snapshot({})

    database_key = _current_database_key()
    if (
        _SNAPSHOT_CACHE_VERSION_ID == active_version.id
        and _SNAPSHOT_CACHE_DB_URI == database_key
        and _SNAPSHOT_CACHE is not None
    ):
        return copy.deepcopy(_SNAPSHOT_CACHE)

    snapshot = _normalize_snapshot(active_version.config_snapshot)
    snapshot.setdefault("meta", {})["source_of_truth"] = snapshot.get("meta", {}).get("source_of_truth") or "database"
    _SNAPSHOT_CACHE = copy.deepcopy(snapshot)
    _SNAPSHOT_CACHE_VERSION_ID = active_version.id
    _SNAPSHOT_CACHE_DB_URI = database_key
    return copy.deepcopy(snapshot)


def get_score_specs(create_from_legacy=True):
    return copy.deepcopy(get_active_score_snapshot(create_from_legacy=create_from_legacy).get("specs", {}))


def get_score_rules(create_from_legacy=True):
    return copy.deepcopy(get_active_score_snapshot(create_from_legacy=create_from_legacy).get("regras", []))


def create_score_version(
    snapshot,
    nome,
    descricao=None,
    source="manual_update",
    activate=True,
    sync_legacy_export=True,
):
    normalized_snapshot = _clone_snapshot(snapshot)
    now = datetime.now()
    previous_active = get_active_score_version(create_from_legacy=False)

    meta = normalized_snapshot.setdefault("meta", {})
    meta["source_of_truth"] = "database"
    meta["updated_from"] = source
    meta["updated_at"] = now.isoformat(timespec="seconds")
    if descricao:
        meta["descricao"] = descricao
    if previous_active:
        meta["previous_active_version_id"] = previous_active.id

    if activate:
        for version in ScoreVersao.query.all():
            if _normalize_status(version.status) in ACTIVE_STATUSES:
                version.status = "ARCHIVED"

    new_version = ScoreVersao(
        nome=nome,
        status="ACTIVE" if activate else "DRAFT",
        config_snapshot=normalized_snapshot,
        ativado_em=now if activate else None,
    )
    db.session.add(new_version)
    db.session.commit()

    _invalidate_snapshot_cache()
    if sync_legacy_export:
        export_snapshot_to_legacy_json(normalized_snapshot)

    return new_version


def update_material_specs(cod_sankhya, specs, descricao=None):
    snapshot = get_active_score_snapshot(create_from_legacy=True)
    snapshot["specs"][str(cod_sankhya)] = copy.deepcopy(specs if isinstance(specs, dict) else {})
    return create_score_version(
        snapshot=snapshot,
        nome=f"Config produto {cod_sankhya} {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        descricao=descricao or f"Atualizacao das specs do produto {cod_sankhya}",
        source="admin_ui_material_config",
        activate=True,
        sync_legacy_export=True,
    )


def update_action_rules(rules, descricao=None):
    snapshot = get_active_score_snapshot(create_from_legacy=True)
    snapshot["regras"] = _normalize_rules(rules)
    return create_score_version(
        snapshot=snapshot,
        nome=f"Regras score {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        descricao=descricao or "Atualizacao das regras de acao do score",
        source="admin_ui_action_rules",
        activate=True,
        sync_legacy_export=True,
    )
