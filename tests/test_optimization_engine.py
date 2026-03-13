import os
import sys
from typing import Any, Dict, List, Mapping

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reoscore.optimization.optimization_engine import (
    IngredientVariable,
    NSGA2OptimizationEngine,
    OptimizationConfig,
    OptimizationProblem,
)


def _evaluate(items: List[Dict[str, Any]], process: Dict[str, Any]) -> Mapping[str, Any]:
    phr_by_code = {str(row["material_code"]): float(row["phr"]) for row in items}
    nr = phr_by_code.get("491", 0.0)
    sbr = phr_by_code.get("503", 0.0)
    cb = phr_by_code.get("528", 0.0)
    oil = phr_by_code.get("542", 0.0)
    sulfur = phr_by_code.get("900001", 0.0)

    polymer_sum = nr + sbr
    hardness = 38.0 + 0.22 * cb - 0.08 * oil + 1.6 * sulfur
    tensile = 10.0 + 0.10 * cb + 0.02 * polymer_sum - 0.03 * oil
    abrasion = 220.0 - 1.5 * cb + 0.9 * oil
    cost = (
        (3.9 * nr) + (3.8 * sbr) + (1.4 * cb) + (1.2 * oil) + (4.7 * sulfur)
    ) / max(1.0, nr + sbr + cb + oil + sulfur)
    risk = min(1.0, 0.12 + (abs(polymer_sum - 100.0) / 140.0) + max(0.0, oil - 20.0) * 0.02)
    confidence = max(0.0, 1.0 - risk * 0.7)

    predicted = {
        "hardness_pred": hardness,
        "tensile_pred": tensile,
        "abrasion_resistance_pred": abrasion,
        "estimated_cost_per_kg": cost,
        "risk_score": risk,
        "confidence_index": confidence,
    }
    metric_values = {
        "hardness": hardness,
        "tensile": tensile,
        "abrasion": abrasion,
        "estimated_cost_per_kg": cost,
        "risk_score": risk,
        "filler_total": cb,
    }
    return {"predicted": predicted, "metric_values": metric_values}


def test_nsga2_engine_generates_pareto_candidates():
    variables = [
        IngredientVariable(
            code="491",
            min_phr=50.0,
            max_phr=85.0,
            base_phr=70.0,
            ingredient_type="polymer",
            is_elastomer=True,
            template_item={"material_code": "491", "material_name": "NR"},
        ),
        IngredientVariable(
            code="503",
            min_phr=15.0,
            max_phr=50.0,
            base_phr=30.0,
            ingredient_type="polymer",
            is_elastomer=True,
            template_item={"material_code": "503", "material_name": "SBR"},
        ),
        IngredientVariable(
            code="528",
            min_phr=35.0,
            max_phr=85.0,
            base_phr=55.0,
            ingredient_type="filler",
            is_filler=True,
            is_carbon_black=True,
            template_item={"material_code": "528", "material_name": "NEGRO FUMO 339"},
        ),
        IngredientVariable(
            code="542",
            min_phr=4.0,
            max_phr=24.0,
            base_phr=12.0,
            ingredient_type="oil",
            template_item={"material_code": "542", "material_name": "OLEO"},
        ),
        IngredientVariable(
            code="900001",
            min_phr=1.0,
            max_phr=5.0,
            base_phr=2.6,
            ingredient_type="curative",
            template_item={"material_code": "900001", "material_name": "ENXOFRE"},
        ),
    ]

    problem = OptimizationProblem(
        application="DIN_X",
        base_process={"curing_temp_c": 170.0, "curing_time_min": 12.0},
        variables=variables,
        evaluate_candidate=_evaluate,
        target_hardness=62.0,
        hardness_tolerance=6.0,
        min_tensile=17.0,
        max_abrasion=150.0,
        max_cost=3.2,
        min_cb=40.0,
        max_cb=70.0,
        elastomer_target_sum=100.0,
        elastomer_tolerance=0.2,
        max_filler_total=120.0,
    )
    engine = NSGA2OptimizationEngine(
        config=OptimizationConfig(
            population_size=40,
            generations=10,
            mutation_rate=0.2,
            mutation_scale=0.14,
            crossover_rate=0.9,
            random_seed=123,
            top_k=8,
        )
    )
    out = engine.run(problem)
    assert out["summary"]["evaluations"] >= 40
    assert out["summary"]["top_k"] <= 8
    assert out["pareto_candidates"]
    assert out["top_candidates"]
    first = out["pareto_candidates"][0]
    assert "formulation" in first
    assert "predicted" in first
    assert "cost" in first
    assert "risk" in first

