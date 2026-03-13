import json
import logging
import os
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from services.theory_engine import detect_elastomer_key, load_json_maps, map_filler_key


LOGGER = logging.getLogger(__name__)


DEFAULT_PROCESS_PARAMETERS = {
    "mixing_temp_c": 110.0,
    "mixing_time_min": 8.0,
    "curing_temp_c": 170.0,
    "curing_time_min": 12.0,
    "rotor_speed_rpm": 45.0,
    "pressure_bar": 8.0,
    "dump_temp_c": 125.0,
    "preheat_temp_c": 35.0,
    "ambient_humidity_pct": 55.0,
}

PROCESS_PARAMETER_ALIASES = {
    "mixing_temp_c": ("mixing_temp_c", "temperatura_mistura", "mix_temp_c"),
    "mixing_time_min": ("mixing_time_min", "tempo_mistura", "mix_time_min"),
    "curing_temp_c": ("curing_temp_c", "temperatura_cura", "temp_plato"),
    "curing_time_min": ("curing_time_min", "tempo_cura", "cura_min"),
    "rotor_speed_rpm": ("rotor_speed_rpm", "rotacao_rpm"),
    "pressure_bar": ("pressure_bar", "pressao_bar"),
    "dump_temp_c": ("dump_temp_c", "temperatura_descarga"),
    "preheat_temp_c": ("preheat_temp_c", "temperatura_pre_aquecimento"),
    "ambient_humidity_pct": ("ambient_humidity_pct", "umidade_ambiente"),
}

DEFAULT_COST_BY_TYPE = {
    "polymer": 3.8,
    "filler": 1.2,
    "oil": 1.0,
    "curative": 4.5,
    "additive": 2.1,
}

CURATIVE_KEYWORDS = (
    "ENXOFRE",
    "SULF",
    "CBS",
    "TBBS",
    "MBTS",
    "TMTD",
    "DTP",
    "ZDEC",
    "ZDBC",
    "DPG",
    "PEROX",
    "VULCAN",
    "ACELER",
    "RETARD",
    "STEARIC",
    "ESTEAR",
    "ZNO",
    "OXIDO DE ZINCO",
)

FILLER_HINTS = (
    "NEGRO FUMO",
    "SILICA",
    "CARBONATO",
    "CAULIM",
    "TALCO",
    "NANOCLAY",
    "BLACK",
)

OIL_HINTS = (
    "OLEO",
    "NAFT",
    "PARAF",
    "DOP",
    "ESTER",
    "PLASTIC",
)

POLYMER_HINTS = (
    "BORRACHA",
    "ELASTOMERO",
    "NR",
    "SBR",
    "NBR",
    "EPDM",
    "BR ",
    "IIR",
    "CR ",
    "SSBR",
)

TYPE_ALIAS = {
    "elastomer": "polymer",
    "polymer": "polymer",
    "borracha": "polymer",
    "filler": "filler",
    "carga": "filler",
    "oil": "oil",
    "oleo": "oil",
    "curative": "curative",
    "vulcanization": "curative",
    "acelerador": "curative",
    "additive": "additive",
    "aditivo": "additive",
}

ELASTOMER_KEYS = (
    "NR",
    "SBR_S1500",
    "SBR_S1700",
    "IIR",
    "CR",
    "BR",
    "NBR",
    "EPDM",
    "EPDM_OIL100",
)

FORMULATION_METADATA_KEYS = {
    "external_code",
    "name",
    "version",
    "source",
    "batch_reference",
    "metadata",
    "metadata_json",
    "id",
}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_code(raw_code: Any) -> Optional[str]:
    if raw_code is None:
        return None

    code = str(raw_code).strip()
    if not code:
        return None

    if code.lower().startswith("mp_"):
        code = code[3:]

    if re.fullmatch(r"\d+(\.0+)?", code):
        try:
            return str(int(float(code)))
        except ValueError:
            return None

    return code.upper()


def _safe_feature_suffix(raw_code: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(raw_code or "").strip())


def _normalize_ingredient_payload(raw: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if raw is None:
        return rows

    if isinstance(raw, Mapping):
        iterable = []
        for key, value in raw.items():
            if str(key).strip().lower() in FORMULATION_METADATA_KEYS:
                continue
            if isinstance(value, Mapping):
                item = dict(value)
                if "material_code" not in item and "code" not in item:
                    item["material_code"] = key
                iterable.append(item)
            else:
                iterable.append({"material_code": key, "phr": value})
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        iterable = list(raw)
    else:
        return rows

    for item in iterable:
        if not isinstance(item, Mapping):
            continue

        raw_code = (
            item.get("material_code")
            or item.get("code")
            or item.get("cd_materia_prima")
            or item.get("id")
            or item.get("mp")
        )
        code = normalize_code(raw_code)
        phr = _to_float(item.get("phr") or item.get("qt_phr") or item.get("value"))
        if not code or phr is None or phr <= 0:
            continue

        rows.append(
            {
                "material_code": code,
                "material_name": (
                    item.get("material_name")
                    or item.get("name")
                    or item.get("nome")
                    or item.get("ds_materia_prima")
                ),
                "phr": float(phr),
                "ingredient_type": item.get("ingredient_type") or item.get("type"),
                "unit_cost_per_kg": _to_float(
                    item.get("unit_cost_per_kg") or item.get("cost_per_kg")
                ),
                "reinforcement_index": _to_float(item.get("reinforcement_index")),
                "metadata_json": item.get("metadata_json") or item.get("metadata"),
            }
        )

    return rows


class FormulationFeaturePipeline:
    """
    Pipeline para transformar formulacao + processo em vetor numerico robusto.
    """

    def __init__(
        self,
        ingredient_feature_limit: int = 140,
        material_cost_path: Optional[str] = None,
    ):
        self.ingredient_feature_limit = max(20, int(ingredient_feature_limit))
        self.material_cost_path = material_cost_path or os.path.join(
            "instance", "material_costs.json"
        )
        self.ingredient_vocabulary_: List[str] = []
        self.feature_columns_: List[str] = []
        self._material_cost_map = self._load_material_costs()
        self._product_catalog_map, self._filler_table = load_json_maps()
        self._elastomer_codes = set((self._product_catalog_map.get("elastomers") or {}).keys())
        oils_block = self._product_catalog_map.get("oils") or {}
        self._oil_codes = {str(x).strip() for x in (oils_block.get("codes") or [])}
        self._filler_codes = {
            str(x).strip()
            for x in (self._product_catalog_map.get("filler_key_by_code") or {}).keys()
        }

    def _load_material_costs(self) -> Dict[str, float]:
        if not os.path.exists(self.material_cost_path):
            return {}
        try:
            with open(self.material_cost_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            normalized: Dict[str, float] = {}
            for key, value in raw.items():
                code = normalize_code(key)
                val = _to_float(value)
                if code and val is not None and val > 0:
                    normalized[code] = float(val)
            return normalized
        except Exception as exc:
            LOGGER.warning("Falha ao carregar mapa de custos (%s): %s", self.material_cost_path, exc)
            return {}

    def fit(self, records: Sequence[Mapping[str, Any]]) -> "FormulationFeaturePipeline":
        code_counter = Counter()
        for record in records or []:
            ingredients = self._resolve_ingredients(record)
            for item in ingredients:
                code = normalize_code(item.get("material_code"))
                phr = _to_float(item.get("phr"))
                if code and phr is not None and phr > 0:
                    code_counter[code] += 1

        ranked_codes = sorted(
            code_counter.items(),
            key=lambda row: (-row[1], row[0]),
        )
        self.ingredient_vocabulary_ = [
            code for code, _ in ranked_codes[: self.ingredient_feature_limit]
        ]

        feature_seed = self._build_feature_row({}, {})
        self.feature_columns_ = list(feature_seed.keys())

        for code in self.ingredient_vocabulary_:
            suffix = _safe_feature_suffix(code)
            self.feature_columns_.append(f"ingredient_phr_{suffix}")
            self.feature_columns_.append(f"ingredient_fraction_{suffix}")

        for key in ELASTOMER_KEYS:
            self.feature_columns_.append(f"dominant_elastomer_{key}")

        self.feature_columns_ = list(dict.fromkeys(self.feature_columns_))
        return self

    def fit_transform(self, records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        self.fit(records)
        return self.transform(records)

    def transform(self, records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        if not self.feature_columns_:
            raise RuntimeError("Pipeline ainda nao foi treinado. Execute fit() antes de transform().")

        rows: List[Dict[str, float]] = []
        for record in records or []:
            ingredients = self._resolve_ingredients(record)
            process = self._resolve_process(record)
            row = self._build_feature_row(ingredients, process)

            total_phr = max(1e-9, float(row.get("total_phr", 0.0)))
            phr_by_code = {
                normalize_code(item.get("material_code")): float(item.get("phr") or 0.0)
                for item in ingredients
                if normalize_code(item.get("material_code"))
            }

            for code in self.ingredient_vocabulary_:
                suffix = _safe_feature_suffix(code)
                phr = float(phr_by_code.get(code) or 0.0)
                row[f"ingredient_phr_{suffix}"] = phr
                row[f"ingredient_fraction_{suffix}"] = phr / total_phr

            dominant_elastomer_key = str(row.get("dominant_elastomer_key") or "").strip().upper()
            for key in ELASTOMER_KEYS:
                row[f"dominant_elastomer_{key}"] = 1.0 if dominant_elastomer_key == key else 0.0

            row.pop("dominant_elastomer_key", None)
            rows.append(row)

        df = pd.DataFrame(rows).fillna(0.0)
        missing_cols = [col for col in self.feature_columns_ if col not in df.columns]
        for col in missing_cols:
            df[col] = 0.0
        df = df.reindex(columns=self.feature_columns_, fill_value=0.0)
        return df.astype(float)

    def transform_one(
        self,
        formulation_payload: Mapping[str, Any],
        process_payload: Optional[Mapping[str, Any]] = None,
    ) -> pd.DataFrame:
        process_payload = process_payload or {}
        record = {
            "formulation": formulation_payload,
            "process_parameters": process_payload,
        }
        return self.transform([record])

    def to_state(self) -> Dict[str, Any]:
        return {
            "ingredient_feature_limit": self.ingredient_feature_limit,
            "material_cost_path": self.material_cost_path,
            "ingredient_vocabulary": list(self.ingredient_vocabulary_),
            "feature_columns": list(self.feature_columns_),
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "FormulationFeaturePipeline":
        obj = cls(
            ingredient_feature_limit=int(state.get("ingredient_feature_limit") or 140),
            material_cost_path=state.get("material_cost_path"),
        )
        obj.ingredient_vocabulary_ = list(state.get("ingredient_vocabulary") or [])
        obj.feature_columns_ = list(state.get("feature_columns") or [])
        return obj

    def _resolve_ingredients(self, record: Mapping[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(record, Mapping):
            return []

        if "ingredients" in record:
            return _normalize_ingredient_payload(record.get("ingredients"))

        formulation_payload = record.get("formulation")
        if isinstance(formulation_payload, Mapping):
            if "ingredients" in formulation_payload:
                return _normalize_ingredient_payload(formulation_payload.get("ingredients"))
            return _normalize_ingredient_payload(formulation_payload)

        return []

    def _resolve_process(self, record: Mapping[str, Any]) -> Dict[str, float]:
        if not isinstance(record, Mapping):
            return dict(DEFAULT_PROCESS_PARAMETERS)

        raw = record.get("process_parameters")
        if not isinstance(raw, Mapping):
            raw = record.get("process") if isinstance(record.get("process"), Mapping) else {}

        out = dict(DEFAULT_PROCESS_PARAMETERS)
        for canonical_key, aliases in PROCESS_PARAMETER_ALIASES.items():
            value = None
            for alias in aliases:
                value = _to_float(raw.get(alias))
                if value is not None:
                    break
            if value is not None:
                out[canonical_key] = float(value)
        return out

    def _build_feature_row(
        self,
        ingredients_payload: Any,
        process_payload: Optional[Mapping[str, Any]],
    ) -> Dict[str, float]:
        ingredients = _normalize_ingredient_payload(ingredients_payload)
        process = self._resolve_process({"process_parameters": process_payload or {}})

        total_phr = 0.0
        polymer_phr = 0.0
        filler_phr = 0.0
        oil_phr = 0.0
        curative_phr = 0.0
        additive_phr = 0.0
        weighted_reinf_sum = 0.0
        weighted_cost_sum = 0.0

        ingredient_map: Dict[str, float] = {}
        dominant_elastomer_key = "NR"

        for item in ingredients:
            code = normalize_code(item.get("material_code"))
            phr = _to_float(item.get("phr"))
            if not code or phr is None or phr <= 0:
                continue

            phr = float(phr)
            ingredient_map[code] = ingredient_map.get(code, 0.0) + phr

        if ingredient_map:
            try:
                dominant_elastomer_key = detect_elastomer_key(
                    ingredient_map, self._product_catalog_map
                )
            except Exception:
                dominant_elastomer_key = "NR"

        for item in ingredients:
            code = normalize_code(item.get("material_code"))
            phr = _to_float(item.get("phr"))
            if not code or phr is None or phr <= 0:
                continue

            phr = float(phr)
            total_phr += phr
            ingredient_type = self._classify_ingredient_type(
                code=code,
                name=item.get("material_name"),
                explicit_type=item.get("ingredient_type"),
            )

            if ingredient_type == "polymer":
                polymer_phr += phr
            elif ingredient_type == "filler":
                filler_phr += phr
            elif ingredient_type == "oil":
                oil_phr += phr
            elif ingredient_type == "curative":
                curative_phr += phr
            else:
                additive_phr += phr

            reinforcement_index = _to_float(item.get("reinforcement_index"))
            if reinforcement_index is None and ingredient_type == "filler":
                reinforcement_index = self._resolve_reinforcement_index(
                    material_code=code,
                    dominant_elastomer_key=dominant_elastomer_key,
                )
            if reinforcement_index is not None and ingredient_type == "filler":
                weighted_reinf_sum += phr * float(reinforcement_index)

            unit_cost = _to_float(item.get("unit_cost_per_kg"))
            if unit_cost is None or unit_cost <= 0:
                unit_cost = self._resolve_unit_cost(code, ingredient_type)
            weighted_cost_sum += phr * float(unit_cost or 0.0)

        polymer_fraction_total = polymer_phr / max(total_phr, 1e-9)
        weighted_reinforcement_index = weighted_reinf_sum / max(filler_phr, 1e-9)
        oil_to_polymer_ratio = oil_phr / max(polymer_phr, 1e-9)
        total_cost_per_kg = weighted_cost_sum / max(total_phr, 1e-9)

        curing_temp = float(process.get("curing_temp_c", DEFAULT_PROCESS_PARAMETERS["curing_temp_c"]))
        curing_time = float(process.get("curing_time_min", DEFAULT_PROCESS_PARAMETERS["curing_time_min"]))
        process_cure_factor = (curing_temp / 170.0) * np.log1p(max(curing_time, 0.0)) / np.log1p(12.0)
        process_cure_factor = max(0.2, min(3.0, float(process_cure_factor)))

        crosslink_density_proxy = (
            (curative_phr / max(polymer_phr, 1e-9))
            * (1.0 + (0.18 * weighted_reinforcement_index))
            * process_cure_factor
        )

        mixing_temp = float(process.get("mixing_temp_c", DEFAULT_PROCESS_PARAMETERS["mixing_temp_c"]))
        mixing_time = float(process.get("mixing_time_min", DEFAULT_PROCESS_PARAMETERS["mixing_time_min"]))
        rotor_speed = float(process.get("rotor_speed_rpm", DEFAULT_PROCESS_PARAMETERS["rotor_speed_rpm"]))
        pressure_bar = float(process.get("pressure_bar", DEFAULT_PROCESS_PARAMETERS["pressure_bar"]))
        dump_temp = float(process.get("dump_temp_c", DEFAULT_PROCESS_PARAMETERS["dump_temp_c"]))
        preheat_temp = float(process.get("preheat_temp_c", DEFAULT_PROCESS_PARAMETERS["preheat_temp_c"]))
        humidity = float(
            process.get("ambient_humidity_pct", DEFAULT_PROCESS_PARAMETERS["ambient_humidity_pct"])
        )

        row: Dict[str, float] = {
            "total_phr": total_phr,
            "n_ingredients": float(len(ingredient_map)),
            "polymer_phr": polymer_phr,
            "filler_phr": filler_phr,
            "oil_phr": oil_phr,
            "curative_phr": curative_phr,
            "additive_phr": additive_phr,
            "polymer_fraction_total": polymer_fraction_total,
            "weighted_filler_reinforcement_index": weighted_reinforcement_index,
            "oil_to_polymer_ratio": oil_to_polymer_ratio,
            "crosslink_density_proxy": crosslink_density_proxy,
            "total_cost_per_kg": total_cost_per_kg,
            "mixing_temp_c": mixing_temp,
            "mixing_time_min": mixing_time,
            "curing_temp_c": curing_temp,
            "curing_time_min": curing_time,
            "rotor_speed_rpm": rotor_speed,
            "pressure_bar": pressure_bar,
            "dump_temp_c": dump_temp,
            "preheat_temp_c": preheat_temp,
            "ambient_humidity_pct": humidity,
            "mixing_energy_proxy": mixing_time * rotor_speed,
            "curing_severity_index": (curing_temp + 273.15) * np.log1p(max(curing_time, 0.0)),
            "pressure_temperature_product": pressure_bar * curing_temp,
            "dominant_elastomer_key": dominant_elastomer_key,
        }
        return row

    def _classify_ingredient_type(
        self,
        code: str,
        name: Any = None,
        explicit_type: Any = None,
    ) -> str:
        explicit = str(explicit_type or "").strip().lower()
        mapped = TYPE_ALIAS.get(explicit)
        if mapped:
            return mapped

        if code in self._elastomer_codes:
            return "polymer"
        if code in self._oil_codes:
            return "oil"
        if code in self._filler_codes:
            return "filler"

        name_upper = str(name or "").strip().upper()
        if any(token in name_upper for token in OIL_HINTS):
            return "oil"
        if any(token in name_upper for token in FILLER_HINTS):
            return "filler"
        if any(token in name_upper for token in CURATIVE_KEYWORDS):
            return "curative"
        if any(token in name_upper for token in POLYMER_HINTS):
            return "polymer"

        filler_key = None
        try:
            filler_key = map_filler_key(code, self._product_catalog_map)
        except Exception:
            filler_key = None
        if filler_key:
            return "filler"

        return "additive"

    def _resolve_unit_cost(self, code: str, ingredient_type: str) -> float:
        mapped = _to_float(self._material_cost_map.get(code))
        if mapped is not None and mapped > 0:
            return float(mapped)
        return float(DEFAULT_COST_BY_TYPE.get(ingredient_type, DEFAULT_COST_BY_TYPE["additive"]))

    def _resolve_reinforcement_index(
        self,
        material_code: str,
        dominant_elastomer_key: str,
    ) -> float:
        try:
            filler_key = map_filler_key(material_code, self._product_catalog_map)
        except Exception:
            filler_key = None
        if not filler_key:
            return 0.0

        filler_info = (self._filler_table.get("fillers") or {}).get(filler_key) or {}
        idx_map = filler_info.get("reinforcement_index_shore_per_phr") or {}
        value = _to_float(idx_map.get(dominant_elastomer_key))
        if value is None:
            value = _to_float(idx_map.get("NR"))
        if value is None:
            return 0.0
        return float(value)

