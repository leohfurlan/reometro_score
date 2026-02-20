import json
import logging
import math
import os
import pickle
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import shap
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor

from services.formulation_data_service import FormulationDatasetService
from services.formulation_feature_pipeline import FormulationFeaturePipeline, normalize_code


LOGGER = logging.getLogger(__name__)


TARGETS = ("hardness", "tensile", "elongation", "abrasion", "ts2", "t90")

DEFAULT_TECHNICAL_CONSTRAINTS = {
    "hardness": {"min": 30.0, "max": 95.0},
    "tensile": {"min": 1.0},
    "elongation": {"min": 20.0},
    "abrasion": {"min": 0.0, "max": 1000.0},
    "ts2": {"min": 0.1},
    "t90": {"min": 0.1, "max": 240.0},
    "oil_to_polymer_ratio": {"min": 0.0, "max": 1.4},
    "crosslink_density_proxy": {"min": 0.0, "max": 6.0},
}

DEFAULT_WEIGHTS = {"cost": 0.4, "risk": 0.3, "resistance": 0.3}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _deep_copy_jsonable(payload: Any) -> Any:
    try:
        return json.loads(json.dumps(payload))
    except Exception:
        return payload


class FormulationEngineService:
    """
    Motor de formulacao preditivo, explainable e com otimizacao multiobjetivo.
    """

    def __init__(
        self,
        artifacts_dir: Optional[str] = None,
        dataset_service: Optional[FormulationDatasetService] = None,
        random_seed: int = 42,
    ):
        self.artifacts_dir = artifacts_dir or os.path.join("instance", "formulation_ml")
        self.models_dir = os.path.join(self.artifacts_dir, "models")
        self.events_dir = os.path.join(self.artifacts_dir, "events")
        self.registry_path = os.path.join(self.artifacts_dir, "model_registry.json")
        self.dataset_service = dataset_service or FormulationDatasetService()
        self.random_seed = int(random_seed)
        self._cache_lock = threading.Lock()
        self._bundle_cache: Optional[Dict[str, Any]] = None
        self._bundle_cache_version: Optional[str] = None

        _ensure_dir(self.artifacts_dir)
        _ensure_dir(self.models_dir)
        _ensure_dir(self.events_dir)

    # ===================================
    # Public API
    # ===================================
    def train(
        self,
        records: Optional[Sequence[Mapping[str, Any]]] = None,
        min_samples: int = 20,
        auto_include_legacy: bool = True,
    ) -> Dict[str, Any]:
        dataset = list(records or [])
        if not dataset:
            dataset = self.dataset_service.load_training_records(include_legacy=auto_include_legacy)

        if len(dataset) < max(8, int(min_samples)):
            return {
                "status": "erro",
                "mensagem": (
                    f"Dados insuficientes para treino multioutput: {len(dataset)} "
                    f"amostras, minimo recomendado={max(8, int(min_samples))}."
                ),
            }

        pipeline = FormulationFeaturePipeline()
        X = pipeline.fit_transform(dataset)
        y = self._extract_target_df(dataset)

        if len(X) != len(y):
            return {
                "status": "erro",
                "mensagem": "Inconsistencia de dataset: numero de linhas de features != targets.",
            }

        if len(X) < 8:
            return {"status": "erro", "mensagem": "Base final insuficiente apos saneamento."}

        test_size = 0.2 if len(X) >= 40 else 0.25
        X_train, X_test, y_train, y_test = train_test_split(
            X,
            y,
            test_size=test_size,
            random_state=self.random_seed,
            shuffle=True,
        )

        model = self._build_model()
        model.fit(X_train, y_train)

        pred_train = model.predict(X_train)
        pred_test = model.predict(X_test)

        metrics = self._compute_metrics(
            y_train=y_train.to_numpy(dtype=float),
            pred_train=np.asarray(pred_train, dtype=float),
            y_test=y_test.to_numpy(dtype=float),
            pred_test=np.asarray(pred_test, dtype=float),
        )
        residual_profile = self._build_residual_profile(
            y_true=y_test.to_numpy(dtype=float),
            pred=np.asarray(pred_test, dtype=float),
        )
        feature_importance = self._build_feature_importance(
            model=model,
            feature_columns=list(X.columns),
        )

        training_summary = self._build_training_summary(
            X_train=X_train,
            y_train=y_train,
        )
        model_version = self._new_version()

        bundle = {
            "version": model_version,
            "created_at": _utc_now_iso(),
            "targets": list(TARGETS),
            "model": model,
            "pipeline_state": pipeline.to_state(),
            "feature_columns": list(X.columns),
            "residual_profile": residual_profile,
            "feature_importance": feature_importance,
            "training_summary": training_summary,
        }

        model_path = os.path.join(self.models_dir, f"formulation_model_{model_version}.pkl")
        metrics_path = os.path.join(self.artifacts_dir, f"metrics_{model_version}.json")
        importance_path = os.path.join(
            self.artifacts_dir, f"feature_importance_{model_version}.json"
        )
        manifest_path = os.path.join(self.artifacts_dir, f"model_manifest_{model_version}.json")

        with open(model_path, "wb") as fh:
            pickle.dump(bundle, fh)

        self._write_json(metrics_path, metrics)
        self._write_json(importance_path, feature_importance)
        self._write_json(
            manifest_path,
            {
                "version": model_version,
                "created_at": bundle["created_at"],
                "model_path": model_path,
                "metrics_path": metrics_path,
                "feature_importance_path": importance_path,
                "targets": list(TARGETS),
                "n_samples": int(len(X)),
                "n_features": int(X.shape[1]),
                "random_seed": self.random_seed,
            },
        )
        self._register_version(
            version=model_version,
            model_path=model_path,
            metrics_path=metrics_path,
            importance_path=importance_path,
            manifest_path=manifest_path,
        )

        with self._cache_lock:
            self._bundle_cache = bundle
            self._bundle_cache_version = model_version

        payload = {
            "status": "sucesso",
            "model_version": model_version,
            "samples": int(len(X)),
            "features": int(X.shape[1]),
            "metrics": metrics,
            "paths": {
                "model": model_path,
                "metrics": metrics_path,
                "feature_importance": importance_path,
                "manifest": manifest_path,
            },
        }
        self._append_event("train", payload)
        return payload

    def predict(
        self,
        formulation_payload: Mapping[str, Any],
        process_payload: Optional[Mapping[str, Any]] = None,
        auto_train_if_missing: bool = True,
    ) -> Dict[str, Any]:
        bundle = self._load_active_bundle(auto_train_if_missing=auto_train_if_missing)
        if not bundle:
            return {
                "status": "erro",
                "mensagem": "Nao foi possivel carregar um modelo ativo para previsao.",
            }

        formulation, process = self._normalize_inference_input(
            formulation_payload=formulation_payload,
            process_payload=process_payload,
        )
        pipeline = FormulationFeaturePipeline.from_state(bundle.get("pipeline_state") or {})
        X = pipeline.transform_one(formulation, process)
        pred_vector = np.asarray(bundle["model"].predict(X), dtype=float).ravel()
        residual_profile = bundle.get("residual_profile") or {}

        predicted_properties: Dict[str, Dict[str, Any]] = {}
        ci_width_ratios = []
        point_predictions: Dict[str, float] = {}

        for idx, target in enumerate(TARGETS):
            pred_value = float(pred_vector[idx])
            point_predictions[target] = pred_value
            profile = residual_profile.get(target) or {}
            q05 = _to_float(profile.get("q05"))
            q95 = _to_float(profile.get("q95"))
            std = _to_float(profile.get("std")) or 0.0
            if q05 is None:
                q05 = -1.64 * std
            if q95 is None:
                q95 = 1.64 * std
            ci_low = float(pred_value + q05)
            ci_high = float(pred_value + q95)
            if ci_low > ci_high:
                ci_low, ci_high = ci_high, ci_low

            ci_width = ci_high - ci_low
            ci_width_ratios.append(ci_width / max(abs(pred_value), 1.0))
            predicted_properties[target] = {
                "value": round(pred_value, 4),
                "confidence_interval": [round(ci_low, 4), round(ci_high, 4)],
            }

        feature_row = X.iloc[0].to_dict()
        novelty_score = self._compute_novelty_score(feature_row, bundle)
        uncertainty_score = float(np.mean(ci_width_ratios)) if ci_width_ratios else 0.0
        uncertainty_score = max(0.0, min(1.0, uncertainty_score))
        risk_score = self._compute_risk_score(
            uncertainty_score=uncertainty_score,
            novelty_score=novelty_score,
            feature_row=feature_row,
        )
        resistance_score = self._compute_resistance_score(point_predictions, bundle)

        response = {
            "status": "sucesso",
            "model_version": bundle.get("version"),
            "confidence_level": 0.90,
            "predicted_properties": predicted_properties,
            "derived_features": {
                "polymer_fraction_total": round(
                    float(feature_row.get("polymer_fraction_total", 0.0)), 6
                ),
                "weighted_filler_reinforcement_index": round(
                    float(feature_row.get("weighted_filler_reinforcement_index", 0.0)), 6
                ),
                "oil_to_polymer_ratio": round(
                    float(feature_row.get("oil_to_polymer_ratio", 0.0)), 6
                ),
                "crosslink_density_proxy": round(
                    float(feature_row.get("crosslink_density_proxy", 0.0)), 6
                ),
                "total_cost_per_kg": round(float(feature_row.get("total_cost_per_kg", 0.0)), 6),
            },
            "objective_signals": {
                "cost": round(float(feature_row.get("total_cost_per_kg", 0.0)), 6),
                "risk": round(float(risk_score), 6),
                "resistance": round(float(resistance_score), 6),
                "uncertainty_score": round(float(uncertainty_score), 6),
                "novelty_score": round(float(novelty_score), 6),
            },
        }
        return response

    def explain(
        self,
        formulation_payload: Mapping[str, Any],
        process_payload: Optional[Mapping[str, Any]] = None,
        top_n: int = 12,
        auto_train_if_missing: bool = True,
    ) -> Dict[str, Any]:
        bundle = self._load_active_bundle(auto_train_if_missing=auto_train_if_missing)
        if not bundle:
            return {
                "status": "erro",
                "mensagem": "Nao foi possivel carregar um modelo ativo para explicacao.",
            }

        formulation, process = self._normalize_inference_input(
            formulation_payload=formulation_payload,
            process_payload=process_payload,
        )
        pipeline = FormulationFeaturePipeline.from_state(bundle.get("pipeline_state") or {})
        X = pipeline.transform_one(formulation, process)
        feature_names = list(X.columns)
        top_n = max(3, min(50, int(top_n)))

        per_target = {}
        global_accumulator = {name: 0.0 for name in feature_names}

        for idx, target in enumerate(TARGETS):
            estimator = bundle["model"].estimators_[idx]
            try:
                explainer = shap.TreeExplainer(estimator)
                shap_values = explainer.shap_values(X)
                shap_array = np.asarray(shap_values, dtype=float).reshape(1, -1)[0]
                base_value_raw = getattr(explainer, "expected_value", 0.0)
                base_value = float(np.ravel(base_value_raw)[0])
            except Exception as exc:
                LOGGER.warning("Falha no SHAP para target %s: %s", target, exc)
                shap_array = np.asarray(
                    getattr(estimator, "feature_importances_", np.zeros(len(feature_names)))
                )
                base_value = 0.0

            contributions = []
            for feat_name, shap_val in zip(feature_names, shap_array):
                abs_val = abs(float(shap_val))
                global_accumulator[feat_name] += abs_val
                contributions.append(
                    {
                        "feature": feat_name,
                        "shap_value": round(float(shap_val), 6),
                        "abs_shap": round(abs_val, 6),
                    }
                )

            contributions.sort(key=lambda row: row["abs_shap"], reverse=True)
            per_target[target] = {
                "base_value": round(base_value, 6),
                "top_contributions": contributions[:top_n],
            }

        global_importance = [
            {"feature": feat, "mean_abs_shap": round(float(val) / max(1, len(TARGETS)), 6)}
            for feat, val in global_accumulator.items()
        ]
        global_importance.sort(key=lambda row: row["mean_abs_shap"], reverse=True)

        payload = {
            "status": "sucesso",
            "model_version": bundle.get("version"),
            "target_explanations": per_target,
            "global_importance": global_importance[:top_n],
        }
        self._append_event("explain", payload)
        return payload

    def optimize(
        self,
        payload: Mapping[str, Any],
        auto_train_if_missing: bool = True,
    ) -> Dict[str, Any]:
        bundle = self._load_active_bundle(auto_train_if_missing=auto_train_if_missing)
        if not bundle:
            return {"status": "erro", "mensagem": "Sem modelo ativo para otimizar formulacao."}

        base_formulation, base_process = self._extract_base_payload(payload)
        if not base_formulation.get("ingredients"):
            return {
                "status": "erro",
                "mensagem": "Payload sem ingredientes validos para otimizar.",
            }

        search_cfg = payload.get("search_config") if isinstance(payload, Mapping) else {}
        if not isinstance(search_cfg, Mapping):
            search_cfg = {}
        population_size = max(120, int(search_cfg.get("population_size") or 900))
        mutation_scale = max(0.02, float(search_cfg.get("mutation_scale") or 0.18))
        random_seed = int(search_cfg.get("random_seed") or self.random_seed)
        rng = np.random.default_rng(random_seed)

        ingredient_bounds = payload.get("ingredient_bounds") if isinstance(payload, Mapping) else {}
        process_bounds = payload.get("process_bounds") if isinstance(payload, Mapping) else {}
        fixed_ingredients = payload.get("fixed_ingredients") if isinstance(payload, Mapping) else []
        if not isinstance(ingredient_bounds, Mapping):
            ingredient_bounds = {}
        if not isinstance(process_bounds, Mapping):
            process_bounds = {}
        if not isinstance(fixed_ingredients, Sequence) or isinstance(fixed_ingredients, (str, bytes)):
            fixed_ingredients = []
        fixed_ingredients_norm = {normalize_code(code) for code in fixed_ingredients}

        technical_constraints = self._merge_constraints(payload.get("technical_constraints"))
        weights = self._resolve_weights(payload.get("weights"))

        base_items = list(base_formulation.get("ingredients") or [])
        candidate_rows: List[Dict[str, Any]] = []

        for idx in range(population_size):
            cand_ingredients = self._mutate_ingredients(
                base_items=base_items,
                ingredient_bounds=ingredient_bounds,
                fixed_ingredients=fixed_ingredients_norm,
                mutation_scale=mutation_scale,
                rng=rng,
            )
            cand_process = self._mutate_process(
                base_process=base_process,
                process_bounds=process_bounds,
                mutation_scale=mutation_scale,
                rng=rng,
            )
            pred = self.predict(
                formulation_payload={"ingredients": cand_ingredients},
                process_payload=cand_process,
                auto_train_if_missing=False,
            )
            if pred.get("status") != "sucesso":
                continue

            objectives = {
                "cost": float(pred["objective_signals"]["cost"]),
                "risk": float(pred["objective_signals"]["risk"]),
                "resistance": float(pred["objective_signals"]["resistance"]),
            }
            feasible, violation_details, violation_score = self._check_constraints(
                prediction=pred,
                technical_constraints=technical_constraints,
            )
            candidate_rows.append(
                {
                    "candidate_id": idx + 1,
                    "formulation": {"ingredients": cand_ingredients},
                    "process_parameters": cand_process,
                    "predicted_properties": pred["predicted_properties"],
                    "derived_features": pred["derived_features"],
                    "objective_signals": objectives,
                    "feasible": feasible,
                    "constraint_violation_score": round(float(violation_score), 8),
                    "constraint_violations": violation_details,
                }
            )

        if not candidate_rows:
            return {"status": "erro", "mensagem": "Falha ao gerar candidatos validos."}

        feasible_rows = [row for row in candidate_rows if row["feasible"]]
        evaluation_pool = feasible_rows if feasible_rows else candidate_rows
        pareto_idx = self._pareto_frontier_indices(evaluation_pool)
        frontier_rows = [evaluation_pool[i] for i in pareto_idx]

        ranked_frontier = self._rank_candidates(frontier_rows, weights)
        top_candidates = ranked_frontier[:10]

        frontier_payload = [
            {
                "candidate_id": row["candidate_id"],
                "cost": row["objective_signals"]["cost"],
                "risk": row["objective_signals"]["risk"],
                "resistance": row["objective_signals"]["resistance"],
                "feasible": row["feasible"],
                "constraint_violation_score": row["constraint_violation_score"],
            }
            for row in ranked_frontier
        ]

        result = {
            "status": "sucesso",
            "model_version": bundle.get("version"),
            "summary": {
                "num_candidates_evaluated": len(candidate_rows),
                "num_feasible": len(feasible_rows),
                "num_pareto": len(frontier_rows),
            },
            "top_candidates": top_candidates,
            "pareto_frontier": frontier_payload,
        }
        self._append_event("optimize", result)
        return result

    def suggest_next_experiments(
        self,
        top_n: int = 10,
        candidate_pool_size: int = 260,
        auto_train_if_missing: bool = True,
    ) -> Dict[str, Any]:
        bundle = self._load_active_bundle(auto_train_if_missing=auto_train_if_missing)
        if not bundle:
            return {
                "status": "erro",
                "mensagem": "Sem modelo ativo para sugerir experimentos.",
            }

        seeds = self.dataset_service.load_seed_formulations(limit=40)
        if not seeds:
            return {
                "status": "erro",
                "mensagem": "Nenhuma formulacao de referencia encontrada para active learning.",
            }

        top_n = max(1, min(30, int(top_n)))
        candidate_pool_size = max(top_n, int(candidate_pool_size))
        per_seed = max(2, int(math.ceil(candidate_pool_size / max(1, len(seeds)))))
        rng = np.random.default_rng(self.random_seed + 17)

        candidates = []
        idx = 0
        for seed in seeds:
            base_items = list(seed.get("ingredients") or [])
            base_process = dict(seed.get("process_parameters") or {})
            for _ in range(per_seed):
                idx += 1
                cand_ingredients = self._mutate_ingredients(
                    base_items=base_items,
                    ingredient_bounds={},
                    fixed_ingredients=set(),
                    mutation_scale=0.22,
                    rng=rng,
                )
                cand_process = self._mutate_process(
                    base_process=base_process,
                    process_bounds={},
                    mutation_scale=0.22,
                    rng=rng,
                )

                pred = self.predict(
                    formulation_payload={"ingredients": cand_ingredients},
                    process_payload=cand_process,
                    auto_train_if_missing=False,
                )
                if pred.get("status") != "sucesso":
                    continue

                uncertainty = float(pred["objective_signals"]["uncertainty_score"])
                novelty = float(pred["objective_signals"]["novelty_score"])
                acquisition = (0.65 * uncertainty) + (0.35 * novelty)

                candidates.append(
                    {
                        "experiment_id": idx,
                        "formulation": {"ingredients": cand_ingredients},
                        "process_parameters": cand_process,
                        "predicted_properties": pred["predicted_properties"],
                        "uncertainty_score": round(uncertainty, 6),
                        "novelty_score": round(novelty, 6),
                        "acquisition_score": round(acquisition, 6),
                    }
                )

                if len(candidates) >= candidate_pool_size:
                    break
            if len(candidates) >= candidate_pool_size:
                break

        if not candidates:
            return {
                "status": "erro",
                "mensagem": "Nao foi possivel gerar candidatos para active learning.",
            }

        candidates.sort(key=lambda row: row["acquisition_score"], reverse=True)
        suggestions = candidates[:top_n]

        payload = {
            "status": "sucesso",
            "model_version": bundle.get("version"),
            "suggestions": suggestions,
            "summary": {
                "candidate_pool_size": len(candidates),
                "returned": len(suggestions),
            },
        }
        self._append_event("active_learning", payload)
        return payload

    # ===================================
    # Core helpers
    # ===================================
    def _extract_target_df(self, records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        rows = []
        for record in records or []:
            measured = record.get("measured_properties") or {}
            row = []
            valid = True
            for target in TARGETS:
                val = _to_float(measured.get(target))
                if val is None:
                    valid = False
                    break
                row.append(float(val))
            if valid:
                rows.append(row)
        return pd.DataFrame(rows, columns=list(TARGETS))

    def _build_model(self) -> MultiOutputRegressor:
        base = xgb.XGBRegressor(
            n_estimators=320,
            learning_rate=0.04,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            min_child_weight=1.2,
            objective="reg:squarederror",
            random_state=self.random_seed,
            n_jobs=4,
        )
        return MultiOutputRegressor(base)

    def _compute_metrics(
        self,
        y_train: np.ndarray,
        pred_train: np.ndarray,
        y_test: np.ndarray,
        pred_test: np.ndarray,
    ) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {
            "global": {},
            "targets": {},
        }

        train_rmse_all = []
        test_rmse_all = []
        train_mae_all = []
        test_mae_all = []

        for idx, target in enumerate(TARGETS):
            yt_train = y_train[:, idx]
            yp_train = pred_train[:, idx]
            yt_test = y_test[:, idx]
            yp_test = pred_test[:, idx]

            train_rmse = float(np.sqrt(mean_squared_error(yt_train, yp_train)))
            test_rmse = float(np.sqrt(mean_squared_error(yt_test, yp_test)))
            train_mae = float(mean_absolute_error(yt_train, yp_train))
            test_mae = float(mean_absolute_error(yt_test, yp_test))
            train_r2 = float(r2_score(yt_train, yp_train))
            test_r2 = float(r2_score(yt_test, yp_test))

            train_rmse_all.append(train_rmse)
            test_rmse_all.append(test_rmse)
            train_mae_all.append(train_mae)
            test_mae_all.append(test_mae)

            metrics["targets"][target] = {
                "train_rmse": round(train_rmse, 6),
                "test_rmse": round(test_rmse, 6),
                "train_mae": round(train_mae, 6),
                "test_mae": round(test_mae, 6),
                "train_r2": round(train_r2, 6),
                "test_r2": round(test_r2, 6),
            }

        metrics["global"] = {
            "train_rmse_mean": round(float(np.mean(train_rmse_all)), 6),
            "test_rmse_mean": round(float(np.mean(test_rmse_all)), 6),
            "train_mae_mean": round(float(np.mean(train_mae_all)), 6),
            "test_mae_mean": round(float(np.mean(test_mae_all)), 6),
            "multioutput_r2_train": round(
                float(r2_score(y_train, pred_train, multioutput="uniform_average")), 6
            ),
            "multioutput_r2_test": round(
                float(r2_score(y_test, pred_test, multioutput="uniform_average")), 6
            ),
        }
        return metrics

    def _build_residual_profile(self, y_true: np.ndarray, pred: np.ndarray) -> Dict[str, Dict[str, float]]:
        profile: Dict[str, Dict[str, float]] = {}
        for idx, target in enumerate(TARGETS):
            residuals = np.asarray(y_true[:, idx] - pred[:, idx], dtype=float)
            if residuals.size == 0:
                profile[target] = {"std": 0.0, "q05": 0.0, "q95": 0.0}
                continue
            std = float(np.std(residuals, ddof=1)) if residuals.size > 1 else float(np.std(residuals))
            q05 = float(np.quantile(residuals, 0.05))
            q95 = float(np.quantile(residuals, 0.95))
            profile[target] = {
                "std": round(std, 8),
                "q05": round(q05, 8),
                "q95": round(q95, 8),
            }
        return profile

    def _build_feature_importance(
        self, model: MultiOutputRegressor, feature_columns: Sequence[str]
    ) -> Dict[str, Any]:
        target_importance = {}
        global_scores = np.zeros(len(feature_columns), dtype=float)

        for idx, target in enumerate(TARGETS):
            estimator = model.estimators_[idx]
            importance = np.asarray(
                getattr(estimator, "feature_importances_", np.zeros(len(feature_columns))),
                dtype=float,
            )
            if importance.size != len(feature_columns):
                importance = np.resize(importance, len(feature_columns))
            global_scores += np.abs(importance)
            rows = [
                {"feature": feature_columns[i], "importance": round(float(importance[i]), 8)}
                for i in range(len(feature_columns))
            ]
            rows.sort(key=lambda row: row["importance"], reverse=True)
            target_importance[target] = rows

        global_rows = [
            {
                "feature": feature_columns[i],
                "importance": round(float(global_scores[i] / max(1, len(TARGETS))), 8),
            }
            for i in range(len(feature_columns))
        ]
        global_rows.sort(key=lambda row: row["importance"], reverse=True)

        return {"global": global_rows, "targets": target_importance}

    def _build_training_summary(
        self, X_train: pd.DataFrame, y_train: pd.DataFrame
    ) -> Dict[str, Any]:
        feature_mean = X_train.mean(axis=0).to_dict()
        feature_std = X_train.std(axis=0, ddof=0).replace(0, 1.0).to_dict()

        matrix = X_train.to_numpy(dtype=float)
        if matrix.shape[0] > 1500:
            rng = np.random.default_rng(self.random_seed + 99)
            idx = rng.choice(matrix.shape[0], size=1500, replace=False)
            matrix = matrix[idx]

        target_stats = {}
        for col in y_train.columns:
            arr = y_train[col].to_numpy(dtype=float)
            target_stats[col] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr, ddof=0) or 1.0),
            }

        return {
            "feature_mean": feature_mean,
            "feature_std": feature_std,
            "training_feature_matrix_sample": matrix.tolist(),
            "target_stats": target_stats,
        }

    def _compute_novelty_score(self, feature_row: Mapping[str, Any], bundle: Mapping[str, Any]) -> float:
        summary = bundle.get("training_summary") or {}
        feature_mean = summary.get("feature_mean") or {}
        feature_std = summary.get("feature_std") or {}
        matrix = np.asarray(summary.get("training_feature_matrix_sample") or [], dtype=float)
        feature_columns = bundle.get("feature_columns") or []
        if matrix.size == 0 or not feature_columns:
            return 0.0

        vector = np.array([_to_float(feature_row.get(col)) or 0.0 for col in feature_columns], dtype=float)
        mean_vec = np.array([_to_float(feature_mean.get(col)) or 0.0 for col in feature_columns], dtype=float)
        std_vec = np.array([_to_float(feature_std.get(col)) or 1.0 for col in feature_columns], dtype=float)
        std_vec = np.where(std_vec <= 1e-9, 1.0, std_vec)

        vector_z = (vector - mean_vec) / std_vec
        matrix_z = (matrix - mean_vec) / std_vec
        dists = np.linalg.norm(matrix_z - vector_z, axis=1)
        min_dist = float(np.min(dists)) if dists.size else 0.0
        novelty = 1.0 - math.exp(-min_dist / 4.0)
        return max(0.0, min(1.0, novelty))

    def _compute_resistance_score(
        self, point_predictions: Mapping[str, float], bundle: Mapping[str, Any]
    ) -> float:
        target_stats = (bundle.get("training_summary") or {}).get("target_stats") or {}

        def z(target: str, value: float, invert: bool = False) -> float:
            stats = target_stats.get(target) or {}
            mean = _to_float(stats.get("mean")) or 0.0
            std = _to_float(stats.get("std")) or 1.0
            val = (float(value) - mean) / max(std, 1e-6)
            return -val if invert else val

        tensile = z("tensile", point_predictions.get("tensile", 0.0))
        elongation = z("elongation", point_predictions.get("elongation", 0.0))
        abrasion = z("abrasion", point_predictions.get("abrasion", 0.0), invert=True)
        ts2 = z("ts2", point_predictions.get("ts2", 0.0))
        t90 = z("t90", point_predictions.get("t90", 0.0), invert=True)

        score = (0.38 * tensile) + (0.28 * elongation) + (0.20 * abrasion) + (0.08 * ts2) + (0.06 * t90)
        return float(score)

    def _compute_risk_score(
        self,
        uncertainty_score: float,
        novelty_score: float,
        feature_row: Mapping[str, Any],
    ) -> float:
        ratio = _to_float(feature_row.get("oil_to_polymer_ratio")) or 0.0
        crosslink = _to_float(feature_row.get("crosslink_density_proxy")) or 0.0

        process_penalty = 0.0
        if ratio > 1.0:
            process_penalty += min(0.25, (ratio - 1.0) * 0.3)
        if crosslink > 4.0:
            process_penalty += min(0.25, (crosslink - 4.0) * 0.08)

        risk = (0.55 * float(uncertainty_score)) + (0.35 * float(novelty_score)) + process_penalty
        return max(0.0, min(1.0, float(risk)))

    # ===================================
    # Optimization helpers
    # ===================================
    def _extract_base_payload(
        self, payload: Mapping[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(payload, Mapping):
            payload = {}

        formulation = payload.get("formulation") if isinstance(payload.get("formulation"), Mapping) else {}
        process = (
            payload.get("process_parameters")
            if isinstance(payload.get("process_parameters"), Mapping)
            else {}
        )

        formulation_norm, process_norm = self._normalize_inference_input(formulation, process)
        if formulation_norm.get("ingredients"):
            return formulation_norm, process_norm

        seeds = self.dataset_service.load_seed_formulations(limit=1)
        if seeds:
            seed = seeds[0]
            return (
                {"ingredients": list(seed.get("ingredients") or [])},
                dict(seed.get("process_parameters") or {}),
            )
        return formulation_norm, process_norm

    def _mutate_ingredients(
        self,
        base_items: Sequence[Mapping[str, Any]],
        ingredient_bounds: Mapping[str, Any],
        fixed_ingredients: Sequence[str],
        mutation_scale: float,
        rng: np.random.Generator,
    ) -> List[Dict[str, Any]]:
        fixed = {normalize_code(c) for c in fixed_ingredients if normalize_code(c)}
        out = []
        for raw_item in base_items:
            item = dict(raw_item)
            code = normalize_code(item.get("material_code") or item.get("code"))
            if not code:
                continue
            base_phr = _to_float(item.get("phr") or item.get("qt_phr")) or 0.0
            bounds = ingredient_bounds.get(code) if isinstance(ingredient_bounds, Mapping) else None
            if not isinstance(bounds, Mapping):
                bounds = ingredient_bounds.get(f"mp_{code}") if isinstance(ingredient_bounds, Mapping) else None
            if not isinstance(bounds, Mapping):
                bounds = {}

            min_bound = _to_float(bounds.get("min"))
            max_bound = _to_float(bounds.get("max"))
            if min_bound is None:
                min_bound = max(0.0, base_phr * (1.0 - mutation_scale))
            if max_bound is None:
                max_bound = max(min_bound, base_phr * (1.0 + mutation_scale))

            if code in fixed:
                phr = base_phr
            else:
                if max_bound <= min_bound:
                    phr = min_bound
                else:
                    phr = float(rng.uniform(min_bound, max_bound))

            item["material_code"] = code
            item["phr"] = round(max(0.0, phr), 6)
            out.append(item)
        return out

    def _mutate_process(
        self,
        base_process: Mapping[str, Any],
        process_bounds: Mapping[str, Any],
        mutation_scale: float,
        rng: np.random.Generator,
    ) -> Dict[str, Any]:
        out = dict(base_process or {})
        numeric_fields = (
            "mixing_temp_c",
            "mixing_time_min",
            "curing_temp_c",
            "curing_time_min",
            "rotor_speed_rpm",
            "pressure_bar",
            "dump_temp_c",
            "preheat_temp_c",
            "ambient_humidity_pct",
        )

        for key in numeric_fields:
            base_val = _to_float(out.get(key))
            if base_val is None:
                continue

            bounds = process_bounds.get(key) if isinstance(process_bounds, Mapping) else None
            if not isinstance(bounds, Mapping):
                bounds = {}

            min_bound = _to_float(bounds.get("min"))
            max_bound = _to_float(bounds.get("max"))
            if min_bound is None:
                min_bound = base_val * (1.0 - mutation_scale * 0.5)
            if max_bound is None:
                max_bound = base_val * (1.0 + mutation_scale * 0.5)

            if max_bound <= min_bound:
                out[key] = float(min_bound)
            else:
                out[key] = float(rng.uniform(min_bound, max_bound))
        return out

    def _merge_constraints(self, raw_constraints: Any) -> Dict[str, Dict[str, float]]:
        out = json.loads(json.dumps(DEFAULT_TECHNICAL_CONSTRAINTS))
        if not isinstance(raw_constraints, Mapping):
            return out

        for key, rule in raw_constraints.items():
            if isinstance(rule, Mapping):
                new_rule = {}
                vmin = _to_float(rule.get("min"))
                vmax = _to_float(rule.get("max"))
                if vmin is not None:
                    new_rule["min"] = vmin
                if vmax is not None:
                    new_rule["max"] = vmax
                if new_rule:
                    out[str(key)] = new_rule
            elif isinstance(rule, Sequence) and not isinstance(rule, (str, bytes)) and len(rule) >= 2:
                vmin = _to_float(rule[0])
                vmax = _to_float(rule[1])
                merged = {}
                if vmin is not None:
                    merged["min"] = vmin
                if vmax is not None:
                    merged["max"] = vmax
                if merged:
                    out[str(key)] = merged
        return out

    def _resolve_weights(self, raw_weights: Any) -> Dict[str, float]:
        weights = dict(DEFAULT_WEIGHTS)
        if isinstance(raw_weights, Mapping):
            for key in ("cost", "risk", "resistance"):
                val = _to_float(raw_weights.get(key))
                if val is not None and val >= 0:
                    weights[key] = float(val)
        total = sum(weights.values())
        if total <= 0:
            return dict(DEFAULT_WEIGHTS)
        return {k: float(v) / total for k, v in weights.items()}

    def _check_constraints(
        self,
        prediction: Mapping[str, Any],
        technical_constraints: Mapping[str, Mapping[str, float]],
    ) -> Tuple[bool, List[Dict[str, Any]], float]:
        predicted_props = prediction.get("predicted_properties") or {}
        derived = prediction.get("derived_features") or {}
        objectives = prediction.get("objective_signals") or {}

        violations = []
        total_violation = 0.0

        for key, rule in technical_constraints.items():
            value = None
            if key in predicted_props:
                value = _to_float((predicted_props.get(key) or {}).get("value"))
            if value is None and key in derived:
                value = _to_float(derived.get(key))
            if value is None and key in objectives:
                value = _to_float(objectives.get(key))
            if value is None:
                continue

            vmin = _to_float(rule.get("min"))
            vmax = _to_float(rule.get("max"))
            if vmin is not None and value < vmin:
                delta = float(vmin - value)
                total_violation += delta / max(abs(vmin), 1.0)
                violations.append(
                    {
                        "metric": key,
                        "type": "below_min",
                        "value": round(value, 6),
                        "min": round(vmin, 6),
                        "delta": round(delta, 6),
                    }
                )
            if vmax is not None and value > vmax:
                delta = float(value - vmax)
                total_violation += delta / max(abs(vmax), 1.0)
                violations.append(
                    {
                        "metric": key,
                        "type": "above_max",
                        "value": round(value, 6),
                        "max": round(vmax, 6),
                        "delta": round(delta, 6),
                    }
                )

        feasible = len(violations) == 0
        return feasible, violations, float(total_violation)

    def _pareto_frontier_indices(self, candidates: Sequence[Mapping[str, Any]]) -> List[int]:
        if not candidates:
            return []

        objs = []
        for row in candidates:
            sig = row.get("objective_signals") or {}
            objs.append(
                (
                    float(sig.get("cost") or 0.0),
                    float(sig.get("risk") or 0.0),
                    -float(sig.get("resistance") or 0.0),
                )
            )

        frontier = []
        for i, a in enumerate(objs):
            dominated = False
            for j, b in enumerate(objs):
                if i == j:
                    continue
                if (
                    (b[0] <= a[0] and b[1] <= a[1] and b[2] <= a[2])
                    and (b[0] < a[0] or b[1] < a[1] or b[2] < a[2])
                ):
                    dominated = True
                    break
            if not dominated:
                frontier.append(i)
        return frontier

    def _rank_candidates(
        self, candidates: Sequence[Mapping[str, Any]], weights: Mapping[str, float]
    ) -> List[Dict[str, Any]]:
        rows = list(candidates or [])
        if not rows:
            return []

        costs = np.array([float(r["objective_signals"]["cost"]) for r in rows], dtype=float)
        risks = np.array([float(r["objective_signals"]["risk"]) for r in rows], dtype=float)
        resist = np.array([float(r["objective_signals"]["resistance"]) for r in rows], dtype=float)

        def norm(arr: np.ndarray, reverse: bool = False) -> np.ndarray:
            if arr.size == 0:
                return arr
            amin = float(np.min(arr))
            amax = float(np.max(arr))
            if abs(amax - amin) < 1e-12:
                out = np.zeros_like(arr)
            else:
                out = (arr - amin) / (amax - amin)
            if reverse:
                out = 1.0 - out
            return out

        norm_cost = norm(costs, reverse=False)
        norm_risk = norm(risks, reverse=False)
        norm_res = norm(resist, reverse=True)

        w_cost = float(weights.get("cost", DEFAULT_WEIGHTS["cost"]))
        w_risk = float(weights.get("risk", DEFAULT_WEIGHTS["risk"]))
        w_res = float(weights.get("resistance", DEFAULT_WEIGHTS["resistance"]))

        ranking_score = (w_cost * norm_cost) + (w_risk * norm_risk) + (w_res * norm_res)

        ranked = []
        for idx, row in enumerate(rows):
            clone = _deep_copy_jsonable(row)
            clone["ranking_score"] = round(float(ranking_score[idx]), 8)
            ranked.append(clone)
        ranked.sort(key=lambda r: (r["ranking_score"], r["constraint_violation_score"]))
        return ranked

    # ===================================
    # Model Registry / Persistence
    # ===================================
    def _new_version(self) -> str:
        return datetime.now(timezone.utc).strftime("v%Y%m%d_%H%M%S")

    def _load_registry(self) -> Dict[str, Any]:
        if not os.path.exists(self.registry_path):
            return {"active_version": None, "versions": []}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
            if "versions" not in data or not isinstance(data.get("versions"), list):
                data["versions"] = []
            if "active_version" not in data:
                data["active_version"] = None
            return data
        except Exception as exc:
            LOGGER.warning("Falha ao carregar registro de modelos: %s", exc)
            return {"active_version": None, "versions": []}

    def _save_registry(self, registry: Mapping[str, Any]) -> None:
        self._write_json(self.registry_path, registry)

    def _register_version(
        self,
        version: str,
        model_path: str,
        metrics_path: str,
        importance_path: str,
        manifest_path: str,
    ) -> None:
        registry = self._load_registry()
        versions = registry.get("versions") or []

        versions = [v for v in versions if v.get("version") != version]
        versions.append(
            {
                "version": version,
                "created_at": _utc_now_iso(),
                "model_path": model_path,
                "metrics_path": metrics_path,
                "feature_importance_path": importance_path,
                "manifest_path": manifest_path,
            }
        )
        registry["versions"] = sorted(versions, key=lambda x: x.get("version", ""))
        registry["active_version"] = version
        self._save_registry(registry)

    def _load_active_bundle(self, auto_train_if_missing: bool = True) -> Optional[Dict[str, Any]]:
        registry = self._load_registry()
        active_version = registry.get("active_version")

        if active_version and self._bundle_cache is not None and self._bundle_cache_version == active_version:
            return self._bundle_cache

        model_path = None
        for row in registry.get("versions") or []:
            if row.get("version") == active_version:
                model_path = row.get("model_path")
                break

        if active_version and model_path and os.path.exists(model_path):
            try:
                with open(model_path, "rb") as fh:
                    bundle = pickle.load(fh)
                with self._cache_lock:
                    self._bundle_cache = bundle
                    self._bundle_cache_version = active_version
                return bundle
            except Exception as exc:
                LOGGER.warning("Falha ao carregar bundle ativo (%s): %s", active_version, exc)

        if auto_train_if_missing:
            trained = self.train()
            if trained.get("status") == "sucesso":
                return self._load_active_bundle(auto_train_if_missing=False)
        return None

    def _write_json(self, path: str, payload: Any) -> None:
        _ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _append_event(self, event_name: str, payload: Any) -> None:
        try:
            _ensure_dir(self.events_dir)
            event_path = os.path.join(self.events_dir, f"{event_name}.jsonl")
            line = {
                "event": event_name,
                "timestamp": _utc_now_iso(),
                "payload": payload,
            }
            with open(event_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:
            LOGGER.warning("Falha ao persistir evento %s: %s", event_name, exc)

    # ===================================
    # Inference payload normalization
    # ===================================
    def _normalize_inference_input(
        self,
        formulation_payload: Mapping[str, Any],
        process_payload: Optional[Mapping[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        formulation_payload = formulation_payload or {}
        process_payload = process_payload or {}

        ingredients_raw = formulation_payload.get("ingredients")
        if ingredients_raw is None:
            ingredients_raw = formulation_payload

        if isinstance(ingredients_raw, Mapping):
            ingredients = []
            for code, val in ingredients_raw.items():
                if str(code).strip().lower() in (
                    "name",
                    "external_code",
                    "version",
                    "source",
                    "metadata",
                    "metadata_json",
                ):
                    continue
                if isinstance(val, Mapping):
                    phr = _to_float(val.get("phr") or val.get("qt_phr") or val.get("value"))
                    material_name = val.get("material_name") or val.get("name") or val.get("nome")
                    ingredient_type = val.get("ingredient_type") or val.get("type")
                    unit_cost = _to_float(val.get("unit_cost_per_kg") or val.get("cost_per_kg"))
                    reinf = _to_float(val.get("reinforcement_index"))
                else:
                    phr = _to_float(val)
                    material_name = None
                    ingredient_type = None
                    unit_cost = None
                    reinf = None
                code_norm = normalize_code(code)
                if code_norm and phr is not None and phr > 0:
                    ingredients.append(
                        {
                            "material_code": code_norm,
                            "phr": float(phr),
                            "material_name": material_name,
                            "ingredient_type": ingredient_type,
                            "unit_cost_per_kg": unit_cost,
                            "reinforcement_index": reinf,
                        }
                    )
        elif isinstance(ingredients_raw, Sequence) and not isinstance(ingredients_raw, (str, bytes)):
            ingredients = []
            for item in ingredients_raw:
                if not isinstance(item, Mapping):
                    continue
                code = normalize_code(
                    item.get("material_code")
                    or item.get("code")
                    or item.get("cd_materia_prima")
                    or item.get("mp")
                )
                phr = _to_float(item.get("phr") or item.get("qt_phr") or item.get("value"))
                if not code or phr is None or phr <= 0:
                    continue
                ingredients.append(
                    {
                        "material_code": code,
                        "phr": float(phr),
                        "material_name": item.get("material_name")
                        or item.get("name")
                        or item.get("nome"),
                        "ingredient_type": item.get("ingredient_type") or item.get("type"),
                        "unit_cost_per_kg": _to_float(
                            item.get("unit_cost_per_kg") or item.get("cost_per_kg")
                        ),
                        "reinforcement_index": _to_float(item.get("reinforcement_index")),
                    }
                )
        else:
            ingredients = []

        formulation = {
            "external_code": formulation_payload.get("external_code"),
            "name": formulation_payload.get("name"),
            "ingredients": ingredients,
        }
        process = dict(process_payload or {})
        return formulation, process
