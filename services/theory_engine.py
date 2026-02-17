import json
import logging
import math
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


LOGGER = logging.getLogger(__name__)

_PRODUCT_MAP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "product_catalog_map.json")
)
_FILLER_TABLE_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "resources", "filler_reinforcement_table.json"
    )
)

_DEFAULT_NR_BASE_SHORE = 38.0
_FALLBACK_ELASTOMER_KEY = "NR"
_DEFAULT_FILLER_SATURATION_A = 12.0
_DEFAULT_FILLER_SATURATION_B = 0.01
_DEFAULT_FILLER_OIL_DAMPING_K = 0.01
_SATURATION_DEFAULTS_KEY = "_saturation_defaults"
_SATURATION_PARAMS_KEY = "_saturation_params_by_filler"
_SUPPORTED_ELASTOMER_KEYS = {
    "NR",
    "SBR_S1500",
    "SBR_S1700",
    "IIR",
    "CR",
    "BR",
    "NBR",
    "EPDM",
    "EPDM_OIL100",
}

_MINERAL_TYPE_TO_FILLER_KEY = {
    "SILICA": "SILICA",
    "ZEOSIL": "SILICA",
    "CALCIUM_SILICATE": "CALCIUM_SILICATE",
    "KAOLIN_HARD": "HARD_KAOLIN",
    "HARD_KAOLIN": "HARD_KAOLIN",
    "KAOLIN_SOFT": "SOFT_KAOLIN",
    "SOFT_KAOLIN": "SOFT_KAOLIN",
    "CARBONATE": "NATURAL_MICRONIZED_CACO3",
    "CACO3": "NATURAL_MICRONIZED_CACO3",
    "CALCIUM_CARBONATE": "NATURAL_MICRONIZED_CACO3",
}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_code(raw_code: Any) -> Optional[str]:
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

    return code


def _iter_formulation_phr(formulation_phr: Mapping[Any, Any]) -> Iterable[Tuple[str, float]]:
    if not isinstance(formulation_phr, Mapping):
        return []

    normalized_rows = []
    for raw_code, raw_phr in formulation_phr.items():
        code = _normalize_code(raw_code)
        phr = _to_float(raw_phr)
        if not code or phr is None or phr <= 0:
            continue
        normalized_rows.append((code, float(phr)))

    return normalized_rows


def _resolve_global_saturation_rule(filler_reinforcement_table: Mapping[str, Any]) -> Mapping[str, Any]:
    rule_candidates = (
        "filler_saturation_rule",
        "filler_saturation",
        "saturation_rule",
        "saturation",
    )
    for key in rule_candidates:
        raw_block = filler_reinforcement_table.get(key)
        if isinstance(raw_block, Mapping):
            return raw_block
    return {}


def _resolve_filler_saturation_rule(filler_info: Mapping[str, Any]) -> Mapping[str, Any]:
    rule_candidates = (
        "saturation_params",
        "saturation_rule",
        "saturation",
        "non_linear_params",
    )
    for key in rule_candidates:
        raw_block = filler_info.get(key)
        if isinstance(raw_block, Mapping):
            return raw_block
    return {}


def _load_filler_saturation_params(filler_reinforcement_table: Dict[str, Any]) -> None:
    global_rule = _resolve_global_saturation_rule(filler_reinforcement_table)

    default_a = _to_float(global_rule.get("a_default"))
    if default_a is None:
        default_a = _to_float(global_rule.get("a"))
    if default_a is None:
        default_a = _DEFAULT_FILLER_SATURATION_A

    default_b = _to_float(global_rule.get("b_default"))
    if default_b is None:
        default_b = _to_float(global_rule.get("b"))
    if default_b is None or default_b <= 0:
        default_b = _DEFAULT_FILLER_SATURATION_B

    default_k = _to_float(global_rule.get("oil_damping_k"))
    if default_k is None:
        default_k = _to_float(global_rule.get("k"))
    if default_k is None or default_k < 0:
        default_k = _DEFAULT_FILLER_OIL_DAMPING_K

    fillers_table = filler_reinforcement_table.get("fillers") or {}
    saturation_by_filler: Dict[str, Dict[str, Optional[float]]] = {}
    for raw_key, raw_filler_info in fillers_table.items():
        filler_key = str(raw_key).strip().upper()
        filler_info = raw_filler_info if isinstance(raw_filler_info, Mapping) else {}
        filler_rule = _resolve_filler_saturation_rule(filler_info)

        a_val = _to_float(filler_rule.get("a"))
        if a_val is None:
            a_val = _to_float(filler_info.get("saturation_a"))

        b_val = _to_float(filler_rule.get("b"))
        if b_val is None:
            b_val = _to_float(filler_info.get("saturation_b"))
        if b_val is not None and b_val <= 0:
            LOGGER.warning(
                "Filler %s com coeficiente b invalido (%s). Usando default %.4f.",
                filler_key,
                b_val,
                default_b,
            )
            b_val = None

        k_val = _to_float(filler_rule.get("oil_damping_k"))
        if k_val is None:
            k_val = _to_float(filler_rule.get("k"))
        if k_val is None:
            k_val = _to_float(filler_info.get("oil_damping_k"))
        if k_val is not None and k_val < 0:
            LOGGER.warning(
                "Filler %s com coeficiente k invalido (%s). Usando default %.4f.",
                filler_key,
                k_val,
                default_k,
            )
            k_val = None

        saturation_by_filler[filler_key] = {
            "a": a_val,
            "b": float(b_val if b_val is not None else default_b),
            "k": float(k_val if k_val is not None else default_k),
        }

    filler_reinforcement_table[_SATURATION_DEFAULTS_KEY] = {
        "a": float(default_a),
        "b": float(default_b),
        "k": float(default_k),
    }
    filler_reinforcement_table[_SATURATION_PARAMS_KEY] = saturation_by_filler


@lru_cache(maxsize=1)
def load_json_maps() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with open(_PRODUCT_MAP_PATH, "r", encoding="utf-8") as fh:
        product_catalog_map = json.load(fh)

    with open(_FILLER_TABLE_PATH, "r", encoding="utf-8") as fh:
        filler_reinforcement_table = json.load(fh)

    _load_filler_saturation_params(filler_reinforcement_table)

    return product_catalog_map, filler_reinforcement_table


def _detect_dominant_elastomer(
    formulation_phr: Mapping[Any, Any], product_catalog_map: Mapping[str, Any]
) -> Tuple[str, Optional[str], float]:
    elastomers = product_catalog_map.get("elastomers") or {}
    poly_map = product_catalog_map.get("poly_type_to_elastomer_key") or {}

    dominant_code = None
    dominant_phr = -1.0
    dominant_entry = None

    for code, phr in _iter_formulation_phr(formulation_phr):
        entry = elastomers.get(code)
        if not entry:
            continue
        if phr > dominant_phr:
            dominant_code = code
            dominant_phr = phr
            dominant_entry = entry

    if dominant_entry is None:
        LOGGER.warning(
            "Nao foi possivel detectar elastomero dominante na formulacao. Fallback para NR."
        )
        return _FALLBACK_ELASTOMER_KEY, None, 0.0

    elastomer_key = dominant_entry.get("elastomer_key")
    if not elastomer_key:
        poly_type = str(dominant_entry.get("poly_type") or "").upper().strip()
        elastomer_key = poly_map.get(poly_type)

    if elastomer_key not in _SUPPORTED_ELASTOMER_KEYS:
        LOGGER.warning(
            "Elastomero dominante %s sem elastomer_key valido. Fallback para NR.",
            dominant_code,
        )
        elastomer_key = _FALLBACK_ELASTOMER_KEY

    return elastomer_key, dominant_code, dominant_phr


def _resolve_elastomer_key(
    elastomer_entry: Mapping[str, Any], poly_map: Mapping[str, Any]
) -> Optional[str]:
    elastomer_key = str(elastomer_entry.get("elastomer_key") or "").strip().upper()
    if elastomer_key in _SUPPORTED_ELASTOMER_KEYS:
        return elastomer_key

    poly_type = str(elastomer_entry.get("poly_type") or "").upper().strip()
    mapped = str(poly_map.get(poly_type) or "").strip().upper()
    if mapped in _SUPPORTED_ELASTOMER_KEYS:
        return mapped

    return None


def detect_elastomer_key(
    formulation_phr: Mapping[Any, Any], product_catalog_map: Mapping[str, Any]
) -> str:
    elastomer_key, _, _ = _detect_dominant_elastomer(formulation_phr, product_catalog_map)
    return elastomer_key


def _resolve_default_nr_base_shore(product_catalog_map: Mapping[str, Any]) -> float:
    elastomers = product_catalog_map.get("elastomers") or {}
    nr_values = []
    for entry in elastomers.values():
        if str(entry.get("elastomer_key") or "").strip().upper() != "NR":
            continue
        base = _to_float(entry.get("base_shore_a"))
        if base is not None:
            nr_values.append(base)

    if nr_values:
        return float(sum(nr_values) / len(nr_values))
    return _DEFAULT_NR_BASE_SHORE


def get_base_shore(
    elastomer_code: Any, product_catalog_map: Mapping[str, Any]
) -> float:
    elastomers = product_catalog_map.get("elastomers") or {}
    code = _normalize_code(elastomer_code)
    entry = elastomers.get(code) if code else None

    if not entry:
        fallback = _resolve_default_nr_base_shore(product_catalog_map)
        LOGGER.warning(
            "Codigo de elastomero %s nao encontrado. Usando base Shore fallback %.2f.",
            elastomer_code,
            fallback,
        )
        return fallback

    base_shore = _to_float(entry.get("base_shore_a"))
    if base_shore is None:
        fallback = _resolve_default_nr_base_shore(product_catalog_map)
        LOGGER.warning(
            "Codigo de elastomero %s sem base_shore_a. Usando fallback %.2f.",
            code,
            fallback,
        )
        return fallback

    return float(base_shore)


def compute_blend_base_shore(
    formulation_phr: Mapping[Any, Any], product_catalog_map: Mapping[str, Any]
) -> Tuple[Optional[float], List[Dict[str, Any]]]:
    elastomers = product_catalog_map.get("elastomers") or {}
    poly_map = product_catalog_map.get("poly_type_to_elastomer_key") or {}

    raw_blend_rows = []
    total_elastomer_phr = 0.0
    weighted_base = 0.0

    for code, phr in _iter_formulation_phr(formulation_phr):
        entry = elastomers.get(code)
        if not entry:
            continue

        base_shore = _to_float(entry.get("base_shore_a"))
        if base_shore is None:
            LOGGER.warning(
                "Elastomero %s sem base_shore_a. Ignorado no calculo de base blend.",
                code,
            )
            continue

        poly_type = str(entry.get("poly_type") or "").upper().strip()
        elastomer_key = _resolve_elastomer_key(entry, poly_map)
        raw_blend_rows.append(
            {
                "code": code,
                "phr": float(phr),
                "base_shore": float(base_shore),
                "poly_type": poly_type,
                "elastomer_key": elastomer_key,
            }
        )
        total_elastomer_phr += float(phr)
        weighted_base += float(phr) * float(base_shore)

    if total_elastomer_phr <= 0:
        return None, []

    base_blend = weighted_base / total_elastomer_phr
    blend_breakdown = []
    for row in raw_blend_rows:
        frac = row["phr"] / total_elastomer_phr
        blend_breakdown.append(
            {
                "code": row["code"],
                "phr": round(row["phr"], 4),
                "base_shore": round(row["base_shore"], 4),
                "frac": round(frac, 6),
                "poly_type": row["poly_type"],
                "elastomer_key": row["elastomer_key"],
            }
        )

    # Mantem ordem deterministica por maior contribuicao no blend.
    blend_breakdown.sort(key=lambda item: item["phr"], reverse=True)
    return float(base_blend), blend_breakdown


def map_filler_key(code: Any, product_catalog_map: Mapping[str, Any]) -> Optional[str]:
    norm_code = _normalize_code(code)
    if not norm_code:
        return None

    direct_map = product_catalog_map.get("filler_key_by_code") or {}
    direct_key = direct_map.get(norm_code)
    if direct_key:
        return str(direct_key).strip().upper()

    carbon_black = product_catalog_map.get("carbon_black_nf_grades") or {}
    cb_entry = carbon_black.get(norm_code)
    if cb_entry:
        nf_grade = str(cb_entry.get("nf_grade") or "").strip().upper()
        return nf_grade or None

    mineral_fillers = product_catalog_map.get("mineral_fillers") or product_catalog_map.get(
        "fillers_other"
    ) or {}
    mineral_entry = mineral_fillers.get(norm_code)
    if not mineral_entry:
        return None

    mapped_key = str(mineral_entry.get("filler_key") or "").strip().upper()
    if mapped_key:
        return mapped_key

    mineral_type = str(mineral_entry.get("type") or "").strip().upper()
    if mineral_type in _MINERAL_TYPE_TO_FILLER_KEY:
        return _MINERAL_TYPE_TO_FILLER_KEY[mineral_type]

    nome = str(mineral_entry.get("nome") or mineral_entry.get("name") or "").upper()
    if "ZEOSIL" in nome or "SILICA" in nome:
        return "SILICA"
    if "SILICATO DE CALCIO" in nome:
        return "CALCIUM_SILICATE"
    if "CAULIM" in nome and ("DURO" in nome or "HARD" in nome):
        return "HARD_KAOLIN"
    if "CAULIM" in nome and ("MACIO" in nome or "SOFT" in nome):
        return "SOFT_KAOLIN"
    if "CARBONATO" in nome:
        return "NATURAL_MICRONIZED_CACO3"

    return None


def _is_oil_code(code: str, product_catalog_map: Mapping[str, Any]) -> bool:
    oils_block = product_catalog_map.get("oils") or {}
    oil_codes = oils_block.get("codes") or []
    if code in {str(item).strip() for item in oil_codes}:
        return True

    items = oils_block.get("items") or {}
    return code in items


def _compute_filler_index_for_blend(
    filler_key: str,
    idx_map: Mapping[str, Any],
    blend_breakdown: List[Dict[str, Any]],
) -> Optional[float]:
    if not blend_breakdown:
        return None

    index_blend = 0.0
    has_any_term = False

    for elastomer_row in blend_breakdown:
        elastomer_key = str(elastomer_row.get("elastomer_key") or "").strip().upper()
        frac = _to_float(elastomer_row.get("frac")) or 0.0
        elastomer_code = elastomer_row.get("code")

        if frac <= 0:
            continue

        if not elastomer_key:
            LOGGER.warning(
                "Filler %s: elastomero %s sem elastomer_key. Termo ignorado no index_blend.",
                filler_key,
                elastomer_code,
            )
            continue

        index = _to_float(idx_map.get(elastomer_key))
        if index is None:
            LOGGER.warning(
                "Filler %s sem indice para elastomero %s (codigo %s). Termo ignorado.",
                filler_key,
                elastomer_key,
                elastomer_code,
            )
            continue

        index_blend += frac * index
        has_any_term = True

    if not has_any_term:
        return None
    return index_blend


def calculate_filler_impact(
    filler_key: str,
    phr: float,
    oil_phr: float,
    reinforcement_index: Optional[float] = None,
    filler_reinforcement_table: Optional[Mapping[str, Any]] = None,
) -> float:
    if phr <= 0:
        return 0.0

    if filler_reinforcement_table is None:
        _, filler_reinforcement_table = load_json_maps()

    defaults = filler_reinforcement_table.get(_SATURATION_DEFAULTS_KEY) or {}
    params_by_filler = filler_reinforcement_table.get(_SATURATION_PARAMS_KEY) or {}
    normalized_filler_key = str(filler_key).strip().upper()
    filler_params = params_by_filler.get(normalized_filler_key) or {}

    default_a = _to_float(defaults.get("a"))
    if default_a is None:
        default_a = _DEFAULT_FILLER_SATURATION_A

    b = _to_float(filler_params.get("b"))
    if b is None:
        b = _to_float(defaults.get("b"))
    if b is None or b <= 0:
        LOGGER.warning(
            "Filler %s com parametro b invalido (%s). Usando default %.4f.",
            normalized_filler_key,
            filler_params.get("b"),
            _DEFAULT_FILLER_SATURATION_B,
        )
        b = _DEFAULT_FILLER_SATURATION_B

    k = _to_float(filler_params.get("k"))
    if k is None:
        k = _to_float(defaults.get("k"))
    if k is None or k < 0:
        LOGGER.warning(
            "Filler %s com parametro k invalido (%s). Usando default %.4f.",
            normalized_filler_key,
            filler_params.get("k"),
            _DEFAULT_FILLER_OIL_DAMPING_K,
        )
        k = _DEFAULT_FILLER_OIL_DAMPING_K

    a = _to_float(filler_params.get("a"))
    if a is None and reinforcement_index is not None:
        a = reinforcement_index / b
    if a is None:
        a = default_a
    if a < 0:
        LOGGER.warning(
            "Filler %s com parametro a negativo (%s). Impacto zerado.",
            normalized_filler_key,
            a,
        )
        return 0.0

    oil_safe = max(0.0, float(oil_phr))
    factor_oleo = 1.0 / (1.0 + (k * oil_safe))
    impact = a * (1.0 - math.exp(-b * float(phr))) * factor_oleo
    return float(impact)


def hardness_prior_details(formulation_phr: Mapping[Any, Any]) -> Dict[str, Any]:
    product_catalog_map, filler_reinforcement_table = load_json_maps()
    elastomer_key, dominant_elastomer_code, _ = _detect_dominant_elastomer(
        formulation_phr, product_catalog_map
    )

    base_blend_shore, blend_breakdown = compute_blend_base_shore(
        formulation_phr, product_catalog_map
    )
    if base_blend_shore is None:
        base_shore = get_base_shore(dominant_elastomer_code, product_catalog_map)
        base_blend_shore = base_shore
    else:
        base_shore = base_blend_shore

    fillers_table = filler_reinforcement_table.get("fillers") or {}
    oil_rule = (
        filler_reinforcement_table.get("oil_rule")
        or product_catalog_map.get("oils", {}).get("rule")
        or {}
    )
    oil_index = _to_float(oil_rule.get("shore_index_shore_per_phr"))
    if oil_index is None:
        oil_index = -0.5

    formulation_rows = list(_iter_formulation_phr(formulation_phr))
    delta_filler = 0.0
    total_filler_phr = 0.0
    total_oil_phr = sum(
        phr for code, phr in formulation_rows if _is_oil_code(code, product_catalog_map)
    )

    saturation_by_filler = filler_reinforcement_table.get(_SATURATION_PARAMS_KEY) or {}

    for code, phr in formulation_rows:
        filler_key = map_filler_key(code, product_catalog_map)
        if not filler_key:
            continue

        filler_info = fillers_table.get(filler_key) or {}
        idx_map = filler_info.get("reinforcement_index_shore_per_phr") or {}

        if blend_breakdown:
            index = _compute_filler_index_for_blend(filler_key, idx_map, blend_breakdown)
        else:
            index = _to_float(idx_map.get(elastomer_key))
            if index is None:
                index = _to_float(idx_map.get(_FALLBACK_ELASTOMER_KEY))

        filler_sat_params = saturation_by_filler.get(str(filler_key).strip().upper()) or {}
        has_explicit_a = _to_float(filler_sat_params.get("a")) is not None
        if index is None and not has_explicit_a:
            continue

        delta_filler += calculate_filler_impact(
            filler_key=filler_key,
            phr=phr,
            oil_phr=total_oil_phr,
            reinforcement_index=index,
            filler_reinforcement_table=filler_reinforcement_table,
        )
        total_filler_phr += phr

    delta_oil = oil_index * total_oil_phr
    hardness_raw = base_shore + delta_filler + delta_oil
    hardness_clamped = max(20.0, min(90.0, hardness_raw))

    blend_debug = [
        f"{item.get('code')}:{(float(item.get('frac') or 0.0) * 100):.1f}%"
        for item in blend_breakdown
    ]
    LOGGER.debug("hardness_prior elastomer_blend_fractions=%s", ", ".join(blend_debug))

    LOGGER.info(
        "hardness_prior dominant_elastomer=%s code=%s base_blend=%.2f total_filler_phr=%.2f total_oil_phr=%.2f hardness_prior=%.2f",
        elastomer_key,
        dominant_elastomer_code or "N/A",
        base_blend_shore,
        total_filler_phr,
        total_oil_phr,
        hardness_clamped,
    )

    return {
        "dominant_elastomer_key": elastomer_key,
        "dominant_elastomer_code": dominant_elastomer_code,
        "base_shore": round(base_shore, 4),
        "base_blend_shore": round(base_blend_shore, 4),
        "elastomer_blend_breakdown": blend_breakdown,
        "delta_filler": round(delta_filler, 4),
        "delta_oil": round(delta_oil, 4),
        "total_filler_phr": round(total_filler_phr, 4),
        "total_oil_phr": round(total_oil_phr, 4),
        "hardness_rule_final": round(hardness_clamped, 2),
        "hardness_rule": round(hardness_clamped, 2),
        "hardness_raw": round(hardness_raw, 4),
    }


def hardness_prior(formulation_phr: Mapping[Any, Any]) -> float:
    details = hardness_prior_details(formulation_phr)
    return float(details.get("hardness_rule_final") or details["hardness_rule"])
