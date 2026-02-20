import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from models.formulation_v2 import (
    Formulation,
    FormulationIngredient,
    MeasuredProperties,
    ProcessParameters,
)
from models.usuario import db


LOGGER = logging.getLogger(__name__)


TARGET_COLUMNS = ("hardness", "tensile", "elongation", "abrasion", "ts2", "t90")


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class FormulationDatasetService:
    """
    Responsavel por carregar datasets de formulacao para treino/inferencia.
    """

    def load_training_records(self, include_legacy: bool = True) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        records.extend(self._load_v2_records())

        if include_legacy:
            records.extend(self._load_legacy_records())

        normalized = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            measured = record.get("measured_properties") or {}
            if not isinstance(measured, Mapping):
                continue

            target_values = {col: _to_float(measured.get(col)) for col in TARGET_COLUMNS}
            if any(v is None for v in target_values.values()):
                continue

            cloned = {
                "formulation": record.get("formulation") or {},
                "ingredients": record.get("ingredients") or [],
                "process_parameters": record.get("process_parameters") or {},
                "measured_properties": target_values,
                "metadata": record.get("metadata") or {},
            }
            normalized.append(cloned)

        return normalized

    def load_seed_formulations(self, limit: int = 40) -> List[Dict[str, Any]]:
        seeds: List[Dict[str, Any]] = []
        seeds.extend(self._load_v2_records(only_with_targets=False))
        seeds.extend(self._load_legacy_records(only_with_targets=False))

        out = []
        seen = set()
        for rec in seeds:
            formulation = rec.get("formulation") or {}
            ext = str(formulation.get("external_code") or "").strip()
            signature = (
                ext,
                tuple(
                    sorted(
                        (
                            str(item.get("material_code")),
                            round(float(item.get("phr") or 0.0), 4),
                        )
                        for item in (rec.get("ingredients") or [])
                    )
                ),
            )
            if signature in seen:
                continue
            seen.add(signature)
            out.append(rec)
            if len(out) >= max(1, int(limit)):
                break
        return out

    def _load_v2_records(self, only_with_targets: bool = True) -> List[Dict[str, Any]]:
        try:
            formulations = (
                Formulation.query.options(
                    selectinload(Formulation.ingredients),
                    selectinload(Formulation.process_parameters),
                    selectinload(Formulation.measured_properties),
                )
                .order_by(Formulation.updated_at.desc())
                .all()
            )
        except SQLAlchemyError as exc:
            LOGGER.warning("Falha ao carregar formulacoes v2: %s", exc)
            return []
        except Exception as exc:
            LOGGER.warning("Falha inesperada ao carregar formulacoes v2: %s", exc)
            return []

        records: List[Dict[str, Any]] = []
        for formulation in formulations:
            ingredients = self._serialize_ingredients(formulation.ingredients or [])
            process = self._serialize_process(formulation.process_parameters)
            measured = self._serialize_measured(formulation.measured_properties)

            if only_with_targets:
                if not measured or any(_to_float(measured.get(col)) is None for col in TARGET_COLUMNS):
                    continue

            records.append(
                {
                    "formulation": {
                        "id": formulation.id,
                        "external_code": formulation.external_code,
                        "name": formulation.name,
                        "version": formulation.version,
                        "source": formulation.source,
                        "batch_reference": formulation.batch_reference,
                        "metadata_json": formulation.metadata_json or {},
                    },
                    "ingredients": ingredients,
                    "process_parameters": process,
                    "measured_properties": measured,
                    "metadata": {"origin": "v2"},
                }
            )

        return records

    def _load_legacy_records(self, only_with_targets: bool = True) -> List[Dict[str, Any]]:
        try:
            df_items = pd.read_sql(
                """
                SELECT
                    fi.cd_produto,
                    f.ds_composto,
                    fi.cd_materia_prima,
                    fi.ds_materia_prima,
                    fi.qt_phr
                FROM tb_formula_item fi
                LEFT JOIN tb_formula f
                    ON f.cd_produto = fi.cd_produto
                WHERE fi.qt_phr IS NOT NULL
                """,
                db.engine,
            )
        except Exception as exc:
            LOGGER.warning("Falha ao carregar itens de formula legado: %s", exc)
            return []

        try:
            df_props = pd.read_sql(
                """
                SELECT
                    cod_sankhya AS external_code,
                    AVG(temp_plato) AS curing_temp_c,
                    AVG(dureza) AS hardness,
                    AVG(tensao_ruptura) AS tensile,
                    AVG(alongamento) AS elongation,
                    AVG(abrasao) AS abrasion,
                    AVG(ts2) AS ts2,
                    AVG(t90) AS t90
                FROM ensaio_consolidado
                GROUP BY cod_sankhya
                """,
                db.engine,
            )
        except Exception as exc:
            LOGGER.warning("Falha ao carregar propriedades legadas: %s", exc)
            df_props = pd.DataFrame(
                columns=[
                    "external_code",
                    "curing_temp_c",
                    "hardness",
                    "tensile",
                    "elongation",
                    "abrasion",
                    "ts2",
                    "t90",
                ]
            )

        props_by_code: Dict[str, Dict[str, Any]] = {}
        for row in df_props.to_dict(orient="records"):
            code = str(row.get("external_code") or "").strip()
            if not code:
                continue
            props_by_code[code] = {
                "process_parameters": {
                    "curing_temp_c": _to_float(row.get("curing_temp_c")),
                },
                "measured_properties": {
                    "hardness": _to_float(row.get("hardness")),
                    "tensile": _to_float(row.get("tensile")),
                    "elongation": _to_float(row.get("elongation")),
                    "abrasion": _to_float(row.get("abrasion")),
                    "ts2": _to_float(row.get("ts2")),
                    "t90": _to_float(row.get("t90")),
                },
            }

        records: List[Dict[str, Any]] = []
        for product_code, group_df in df_items.groupby("cd_produto"):
            code = str(product_code).strip()
            if not code:
                continue

            ingredients = []
            for item in group_df.to_dict(orient="records"):
                phr = _to_float(item.get("qt_phr"))
                if phr is None or phr <= 0:
                    continue
                ingredients.append(
                    {
                        "material_code": str(item.get("cd_materia_prima") or "").strip(),
                        "material_name": item.get("ds_materia_prima"),
                        "phr": float(phr),
                    }
                )

            if not ingredients:
                continue

            row_props = props_by_code.get(code, {})
            measured = row_props.get("measured_properties") or {}
            if only_with_targets and any(_to_float(measured.get(col)) is None for col in TARGET_COLUMNS):
                continue

            name = None
            if "ds_composto" in group_df.columns:
                name = next(
                    (
                        str(v).strip()
                        for v in group_df["ds_composto"].tolist()
                        if str(v or "").strip()
                    ),
                    None,
                )

            records.append(
                {
                    "formulation": {
                        "external_code": code,
                        "name": name,
                        "source": "legacy",
                    },
                    "ingredients": ingredients,
                    "process_parameters": row_props.get("process_parameters") or {},
                    "measured_properties": measured,
                    "metadata": {"origin": "legacy"},
                }
            )

        return records

    def _serialize_ingredients(
        self, ingredients: Sequence[FormulationIngredient]
    ) -> List[Dict[str, Any]]:
        out = []
        for item in ingredients:
            out.append(
                {
                    "material_code": str(item.material_code),
                    "material_name": item.material_name,
                    "phr": float(item.phr),
                    "ingredient_type": item.ingredient_type,
                    "unit_cost_per_kg": _to_float(item.unit_cost_per_kg),
                    "reinforcement_index": _to_float(item.reinforcement_index),
                    "metadata_json": item.metadata_json or {},
                }
            )
        return out

    def _serialize_process(self, process: Optional[ProcessParameters]) -> Dict[str, Any]:
        if process is None:
            return {}
        return {
            "mixing_temp_c": _to_float(process.mixing_temp_c),
            "mixing_time_min": _to_float(process.mixing_time_min),
            "curing_temp_c": _to_float(process.curing_temp_c),
            "curing_time_min": _to_float(process.curing_time_min),
            "rotor_speed_rpm": _to_float(process.rotor_speed_rpm),
            "pressure_bar": _to_float(process.pressure_bar),
            "dump_temp_c": _to_float(process.dump_temp_c),
            "preheat_temp_c": _to_float(process.preheat_temp_c),
            "ambient_humidity_pct": _to_float(process.ambient_humidity_pct),
            "metadata_json": process.metadata_json or {},
        }

    def _serialize_measured(self, measured: Optional[MeasuredProperties]) -> Dict[str, Any]:
        if measured is None:
            return {}
        return {
            "hardness": _to_float(measured.hardness),
            "tensile": _to_float(measured.tensile),
            "elongation": _to_float(measured.elongation),
            "abrasion": _to_float(measured.abrasion),
            "ts2": _to_float(measured.ts2),
            "t90": _to_float(measured.t90),
            "risk_score": _to_float(measured.risk_score),
            "metadata_json": measured.metadata_json or {},
        }

