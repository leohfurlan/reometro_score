import os
import sys
from typing import Any, Dict, List

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.formulation_engine_service import FormulationEngineService
from services.formulation_feature_pipeline import FormulationFeaturePipeline


class StubDatasetService:
    def __init__(self, seeds: List[Dict[str, Any]]):
        self._seeds = seeds

    def load_training_records(self, include_legacy: bool = True):
        return []

    def load_seed_formulations(self, limit: int = 40):
        return self._seeds[:limit]


def _build_synthetic_dataset(n: int = 70) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(123)
    rows = []
    for i in range(n):
        phr_nr = float(rng.uniform(45, 85))
        phr_sbr = float(rng.uniform(5, 35))
        phr_filler = float(rng.uniform(20, 65))
        phr_oil = float(rng.uniform(4, 24))
        phr_curative = float(rng.uniform(1.2, 4.8))

        curing_temp = float(rng.uniform(160, 182))
        curing_time = float(rng.uniform(8, 18))
        mixing_temp = float(rng.uniform(95, 125))
        mixing_time = float(rng.uniform(5, 11))
        rotor_speed = float(rng.uniform(35, 60))
        pressure_bar = float(rng.uniform(6, 11))

        noise_h = float(rng.normal(0, 0.8))
        noise_t = float(rng.normal(0, 0.6))
        noise_e = float(rng.normal(0, 9.0))
        noise_a = float(rng.normal(0, 5.0))
        noise_ts2 = float(rng.normal(0, 0.15))
        noise_t90 = float(rng.normal(0, 0.25))

        hardness = 36 + 0.17 * phr_filler - 0.10 * phr_oil + 1.9 * phr_curative + 0.06 * (curing_temp - 170) + noise_h
        tensile = 7 + 0.09 * phr_filler + 0.03 * (phr_nr + phr_sbr) - 0.02 * phr_oil + 0.35 * phr_curative + noise_t
        elongation = 540 - 2.0 * phr_filler - 3.2 * phr_curative + 1.7 * phr_oil + noise_e
        abrasion = 280 - 1.7 * phr_filler + 1.0 * phr_oil - 3.3 * phr_curative + noise_a
        ts2 = 3.5 + 0.08 * phr_curative - 0.02 * (curing_temp - 165) + 0.02 * phr_oil + noise_ts2
        t90 = 14.5 - 0.18 * phr_curative + 0.03 * phr_oil + 0.02 * (175 - curing_temp) + noise_t90

        rows.append(
            {
                "formulation": {
                    "external_code": str(9000 + i),
                    "name": f"FORM_{i}",
                },
                "ingredients": [
                    {
                        "material_code": "491",
                        "material_name": "BORRACHA GEB1 - MC",
                        "phr": phr_nr,
                        "ingredient_type": "polymer",
                        "unit_cost_per_kg": 3.9,
                    },
                    {
                        "material_code": "503",
                        "material_name": "BORRACHA SBR",
                        "phr": phr_sbr,
                        "ingredient_type": "polymer",
                        "unit_cost_per_kg": 3.7,
                    },
                    {
                        "material_code": "528",
                        "material_name": "NEGRO FUMO 339",
                        "phr": phr_filler,
                        "ingredient_type": "filler",
                        "unit_cost_per_kg": 1.5,
                    },
                    {
                        "material_code": "542",
                        "material_name": "OLEO NAFTENICO",
                        "phr": phr_oil,
                        "ingredient_type": "oil",
                        "unit_cost_per_kg": 1.2,
                    },
                    {
                        "material_code": "900001",
                        "material_name": "ENXOFRE DISPERSO",
                        "phr": phr_curative,
                        "ingredient_type": "curative",
                        "unit_cost_per_kg": 4.8,
                    },
                ],
                "process_parameters": {
                    "mixing_temp_c": mixing_temp,
                    "mixing_time_min": mixing_time,
                    "curing_temp_c": curing_temp,
                    "curing_time_min": curing_time,
                    "rotor_speed_rpm": rotor_speed,
                    "pressure_bar": pressure_bar,
                },
                "measured_properties": {
                    "hardness": float(np.clip(hardness, 25, 95)),
                    "tensile": float(max(1.0, tensile)),
                    "elongation": float(max(50.0, elongation)),
                    "abrasion": float(max(15.0, abrasion)),
                    "ts2": float(max(0.4, ts2)),
                    "t90": float(max(1.0, t90)),
                },
            }
        )
    return rows


def test_feature_pipeline_generates_required_features():
    dataset = _build_synthetic_dataset(12)
    pipeline = FormulationFeaturePipeline()
    frame = pipeline.fit_transform(dataset)

    required = {
        "polymer_fraction_total",
        "weighted_filler_reinforcement_index",
        "oil_to_polymer_ratio",
        "crosslink_density_proxy",
        "total_cost_per_kg",
    }
    assert required.issubset(set(frame.columns))
    assert len(frame) == 12


def test_engine_train_predict_and_explain(tmp_path):
    dataset = _build_synthetic_dataset(60)
    seeds = dataset[:8]
    service = FormulationEngineService(
        artifacts_dir=str(tmp_path / "artifacts"),
        dataset_service=StubDatasetService(seeds=seeds),
        random_seed=123,
    )

    train_out = service.train(records=dataset, min_samples=20, auto_include_legacy=False)
    assert train_out["status"] == "sucesso"
    assert train_out["model_version"]

    sample = dataset[0]
    pred_out = service.predict(
        formulation_payload={"ingredients": sample["ingredients"]},
        process_payload=sample["process_parameters"],
        auto_train_if_missing=False,
    )
    assert pred_out["status"] == "sucesso"
    for key in ("hardness", "tensile", "elongation", "abrasion", "ts2", "t90"):
        assert key in pred_out["predicted_properties"]
        assert len(pred_out["predicted_properties"][key]["confidence_interval"]) == 2

    exp_out = service.explain(
        formulation_payload={"ingredients": sample["ingredients"]},
        process_payload=sample["process_parameters"],
        top_n=8,
        auto_train_if_missing=False,
    )
    assert exp_out["status"] == "sucesso"
    assert exp_out["global_importance"]


def test_engine_optimize_and_active_learning(tmp_path):
    dataset = _build_synthetic_dataset(55)
    seeds = dataset[:10]
    service = FormulationEngineService(
        artifacts_dir=str(tmp_path / "artifacts"),
        dataset_service=StubDatasetService(seeds=seeds),
        random_seed=321,
    )
    assert service.train(records=dataset, min_samples=20)["status"] == "sucesso"

    base = dataset[1]
    opt_payload = {
        "formulation": {"ingredients": base["ingredients"]},
        "process_parameters": base["process_parameters"],
        "technical_constraints": {
            "hardness": {"min": 45, "max": 90},
            "tensile": {"min": 5},
            "ts2": {"min": 0.5},
        },
        "search_config": {"population_size": 180, "mutation_scale": 0.2},
        "ingredient_bounds": {
            "528": {"min": 15, "max": 75},
            "542": {"min": 2, "max": 28},
        },
    }
    opt_out = service.optimize(opt_payload, auto_train_if_missing=False)
    assert opt_out["status"] == "sucesso"
    assert len(opt_out["top_candidates"]) <= 10
    assert opt_out["pareto_frontier"]

    al_out = service.suggest_next_experiments(
        top_n=6,
        candidate_pool_size=90,
        auto_train_if_missing=False,
    )
    assert al_out["status"] == "sucesso"
    assert len(al_out["suggestions"]) == 6
