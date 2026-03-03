from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence

import numpy as np


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip_01(value: float) -> float:
    return float(max(0.0, min(1.0, float(value))))


@dataclass
class EnsemblePrediction:
    mean_by_target: Dict[str, float]
    std_by_target: Dict[str, float]
    uncertainty_score: float
    confidence_index: float


class RiskEngine:
    """
    Computes ensemble uncertainty and converts it into production-safe risk metrics.
    """

    MIN_ENSEMBLE_SIZE = 5

    @staticmethod
    def predict_ensemble(
        models: Sequence[Any],
        X: Any,
        target_names: Sequence[str],
        target_scales: Mapping[str, float] | None = None,
    ) -> EnsemblePrediction:
        if not models:
            raise ValueError("At least one model is required for ensemble prediction.")

        predictions = []
        for model in models:
            pred = np.asarray(model.predict(X), dtype=float)
            if pred.ndim == 1:
                pred = pred.reshape(-1, 1)
            predictions.append(pred)

        stack = np.stack(predictions, axis=0)  # [n_models, n_samples, n_targets]
        if stack.shape[2] != len(target_names):
            raise ValueError("Target count mismatch between ensemble predictions and target names.")

        mean_matrix = np.mean(stack, axis=0)
        if stack.shape[0] > 1:
            std_matrix = np.std(stack, axis=0, ddof=1)
        else:
            std_matrix = np.zeros_like(mean_matrix)

        mean_row = mean_matrix[0]
        std_row = std_matrix[0]

        mean_by_target = {
            str(target_names[idx]): float(mean_row[idx]) for idx in range(len(target_names))
        }
        std_by_target = {
            str(target_names[idx]): float(std_row[idx]) for idx in range(len(target_names))
        }

        uncertainty_score = RiskEngine.compute_uncertainty_score(
            std_by_target=std_by_target,
            target_scales=target_scales or {},
        )
        confidence_index = RiskEngine.compute_confidence_index(
            std_by_target=std_by_target,
            target_scales=target_scales or {},
        )
        return EnsemblePrediction(
            mean_by_target=mean_by_target,
            std_by_target=std_by_target,
            uncertainty_score=uncertainty_score,
            confidence_index=confidence_index,
        )

    @staticmethod
    def compute_uncertainty_score(
        std_by_target: Mapping[str, float],
        target_scales: Mapping[str, float],
    ) -> float:
        normalized = []
        for target, raw_std in (std_by_target or {}).items():
            std_val = max(0.0, float(raw_std))
            scale = _to_float((target_scales or {}).get(target))
            scale = max(scale if scale is not None else 1.0, 1e-6)
            ratio = std_val / scale
            normalized.append(1.0 - math.exp(-ratio))
        if not normalized:
            return 0.0
        return _clip_01(float(np.mean(normalized)))

    @staticmethod
    def compute_confidence_index(
        std_by_target: Mapping[str, float],
        target_scales: Mapping[str, float],
    ) -> float:
        uncertainty = RiskEngine.compute_uncertainty_score(std_by_target, target_scales)
        confidence = math.exp(-1.35 * uncertainty)
        return _clip_01(confidence)

    @staticmethod
    def compute_process_penalty(feature_row: Mapping[str, Any]) -> float:
        ratio = _to_float(feature_row.get("oil_to_polymer_ratio")) or 0.0
        crosslink = _to_float(feature_row.get("crosslink_density_proxy")) or 0.0
        severity = _to_float(feature_row.get("curing_severity_index")) or 0.0

        penalty = 0.0
        if ratio > 1.0:
            penalty += min(0.22, (ratio - 1.0) * 0.30)
        if crosslink > 4.0:
            penalty += min(0.22, (crosslink - 4.0) * 0.08)
        if severity > 1400.0:
            penalty += min(0.12, (severity - 1400.0) / 2200.0)
        return max(0.0, float(penalty))

    @staticmethod
    def compute_risk_score(
        uncertainty_score: float,
        novelty_score: float,
        feature_row: Mapping[str, Any],
    ) -> float:
        process_penalty = RiskEngine.compute_process_penalty(feature_row)
        risk = (0.60 * float(uncertainty_score)) + (0.30 * float(novelty_score)) + process_penalty
        return _clip_01(risk)

    @staticmethod
    def confidence_interval(
        mean_value: float,
        ensemble_std: float,
        residual_std: float,
        confidence_level: float = 0.90,
    ) -> tuple[float, float]:
        conf = float(confidence_level)
        if conf >= 0.95:
            z_value = 1.96
        elif conf >= 0.90:
            z_value = 1.64
        else:
            z_value = 1.28

        total_std = math.sqrt(max(float(ensemble_std), 0.0) ** 2 + max(float(residual_std), 0.0) ** 2)
        radius = z_value * total_std
        low = float(mean_value) - radius
        high = float(mean_value) + radius
        if low > high:
            low, high = high, low
        return low, high

