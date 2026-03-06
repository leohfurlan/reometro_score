import re
from datetime import datetime

from sqlalchemy import func, or_

from models.consolidado import EnsaioConsolidado
from models.usuario import db
from services.etl_service import extrair_lote_da_string
from services.learning_service import carregar_aprendizado_mapa, ensinar_lote

INVALID_MASS_VALUES = {"NONE", "NULL", "NULO", "SELECIONE...", "SELECIONE"}


def normalize_learning_key(value):
    return str(value or "").strip().upper()


def compact_learning_key(value):
    return "".join(char for char in normalize_learning_key(value) if char.isalnum())


def apply_learning_overlay(records):
    try:
        correction_map = carregar_aprendizado_mapa()
        if not correction_map:
            return records

        count = 0
        for record in records:
            original_lot = normalize_learning_key(getattr(record, "lote_original", ""))
            original_material = normalize_learning_key(getattr(record, "material_original", ""))

            rule = (
                correction_map.get(original_lot)
                or correction_map.get(compact_learning_key(original_lot))
                or correction_map.get(original_material)
                or correction_map.get(compact_learning_key(original_material))
            )
            if not rule:
                continue

            record.lote = rule.get("lote_real") or record.lote
            corrected_mass = rule.get("massa")
            if corrected_mass:
                record.massa_descricao = corrected_mass

            record.metodo_identificacao = "MANUAL"
            count += 1

        print(f"Sobrescrita de aprendizado: {count} registros corrigidos em memoria via SQLAlchemy.")
        return records
    except Exception as exc:
        print(f"[WARN] Erro ao aplicar correcoes de aprendizado: {exc}")
        return records


def apply_persisted_corrections_to_consolidated(keys_to_apply=None):
    correction_map = carregar_aprendizado_mapa()
    if not correction_map:
        return 0

    if keys_to_apply:
        keys = {normalize_learning_key(key) for key in keys_to_apply if normalize_learning_key(key)}
    else:
        keys = set(correction_map.keys())

    if not keys:
        return 0

    compact_keys = {compact_learning_key(key) for key in keys if compact_learning_key(key)}
    original_lot_compact = func.upper(
        func.replace(
            func.replace(
                func.replace(
                    func.replace(EnsaioConsolidado.lote_original, " ", ""),
                    "-",
                    "",
                ),
                "/",
                "",
            ),
            ".",
            "",
        )
    )

    rows = (
        EnsaioConsolidado.query
        .filter(
            or_(
                func.upper(EnsaioConsolidado.lote_original).in_(list(keys)),
                original_lot_compact.in_(list(compact_keys)),
            )
        )
        .all()
    )

    changed = 0
    for record in rows:
        key = normalize_learning_key(record.lote_original)
        rule = correction_map.get(key) or correction_map.get(compact_learning_key(key))
        if not rule:
            continue

        new_lot = normalize_learning_key(rule.get("lote_real")) or record.lote
        new_mass = normalize_learning_key(rule.get("massa")) or record.massa_descricao

        was_changed = False
        if new_lot and record.lote != new_lot:
            record.lote = new_lot
            was_changed = True

        if new_mass and normalize_learning_key(record.massa_descricao) != new_mass:
            record.massa_descricao = new_mass
            was_changed = True

        if record.metodo_identificacao != "MANUAL":
            record.metodo_identificacao = "MANUAL"
            was_changed = True

        if was_changed:
            record.updated_at = datetime.now()
            changed += 1

    if changed:
        db.session.commit()

    return changed


def _score_extracted_lot(candidate, source):
    text = str(candidate or "").strip()
    if not text:
        return (99, 99, 99, 99)
    source_rank = 0 if source in {"Asterisco", "Exato", "Regex"} else 1
    size_rank = 0 if 4 <= len(text) <= 7 else (1 if len(text) <= 10 else 2)
    trailing_zeros = 1 if re.search(r"0{3,}$", text) else 0
    return (source_rank, size_rank, trailing_zeros, len(text))


def apply_lot_cleanup_to_consolidated():
    rows = (
        EnsaioConsolidado.query
        .filter(
            EnsaioConsolidado.lote_original.isnot(None),
            EnsaioConsolidado.metodo_identificacao.in_(["TEXTO", "FANTASMA"]),
        )
        .all()
    )

    changed = 0
    for record in rows:
        candidate_original, source_original = extrair_lote_da_string(record.lote_original or record.lote)
        candidate_material, source_material = extrair_lote_da_string(record.material_original)

        if candidate_original and candidate_material:
            cleaned_lot = (
                candidate_original
                if _score_extracted_lot(candidate_original, source_original)
                <= _score_extracted_lot(candidate_material, source_material)
                else candidate_material
            )
        else:
            cleaned_lot = candidate_original or candidate_material

        if not cleaned_lot:
            continue

        cleaned_lot = normalize_learning_key(cleaned_lot)
        if cleaned_lot and normalize_learning_key(record.lote) != cleaned_lot:
            record.lote = cleaned_lot
            record.updated_at = datetime.now()
            changed += 1

    if changed:
        db.session.commit()

    return changed


def save_manual_learning_correction(
    original_text,
    corrected_lot,
    corrected_mass,
    username,
    refresh_learning_fn,
    refresh_cache_fn,
):
    if not original_text or not corrected_lot:
        return "warning", "Dados incompletos para salvar."

    mass_raw = str(corrected_mass or "").strip()
    if not mass_raw or mass_raw.upper() in INVALID_MASS_VALUES:
        return "warning", "Selecione uma massa valida antes de salvar."

    original_key = normalize_learning_key(original_text)
    lot_clean = normalize_learning_key(corrected_lot)
    mass_clean = mass_raw.upper()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        ensinar_lote(original_key, lot_clean, mass_clean, usuario=username)
        refresh_learning_fn()
        adjusted_total = apply_persisted_corrections_to_consolidated([original_key])
        refresh_cache_fn()
    except Exception as exc:
        db.session.rollback()
        print(f"[ERRO] Erro ao salvar no SQLite: {exc}")
        return "danger", "Erro ao salvar regra localmente."

    return (
        "success",
        f"Regra salva e aplicada! Ajustados: {adjusted_total} - {username} as {timestamp}.",
    )
