import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.theory_engine import (
    hardness_prior_details,
    get_base_shore,
    hardness_prior,
    load_json_maps,
)


def test_nr_puro_retorna_base_do_elastomero():
    product_catalog_map, _ = load_json_maps()
    base = get_base_shore("2276", product_catalog_map)
    predicted = hardness_prior({2276: 100.0})

    assert abs(predicted - base) <= 0.2
    assert predicted < 60.0


def test_nr_com_n339_sobe_conforme_indice_teorico():
    product_catalog_map, filler_reinforcement_table = load_json_maps()
    base = get_base_shore("2276", product_catalog_map)
    predicted = hardness_prior({2276: 100.0, 528: 40.0})

    # Modelo nao-linear: impacto = a * (1 - exp(-b * phr)); sem oleo, fator_oleo = 1.
    idx_nr = float(
        filler_reinforcement_table["fillers"]["N339"]["reinforcement_index_shore_per_phr"]["NR"]
    )
    saturation_defaults = filler_reinforcement_table.get("_saturation_defaults") or {}
    b = float(saturation_defaults.get("b") or 0.01)
    a = idx_nr / b
    expected = base + (a * (1.0 - math.exp(-b * 40.0)))
    assert abs(predicted - expected) <= 0.35


def test_nr_com_oleo_reduz_10_shore_em_20phr():
    product_catalog_map, _ = load_json_maps()
    base = get_base_shore("2276", product_catalog_map)
    predicted = hardness_prior({2276: 100.0, 542: 20.0})

    expected = base - 10.0
    assert abs(predicted - expected) <= 0.25


def test_blend_sbr_nr_sem_filler_usa_base_ponderada():
    product_catalog_map, _ = load_json_maps()
    base_sbr = get_base_shore("503", product_catalog_map)
    base_nr = get_base_shore("491", product_catalog_map)

    details = hardness_prior_details({503: 55.0, 491: 60.0})
    hardness_rule = float(details["hardness_rule_final"])

    expected = ((55.0 * base_sbr) + (60.0 * base_nr)) / 115.0
    assert abs(hardness_rule - expected) <= 0.35
    assert abs(hardness_rule - 38.0) > 1.0
