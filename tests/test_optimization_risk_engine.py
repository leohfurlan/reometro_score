import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reoscore.optimization.risk_engine import RiskEngine


class _DummyModel:
    def __init__(self, output):
        self._output = np.asarray(output, dtype=float).reshape(1, -1)

    def predict(self, X):
        rows = len(X)
        return np.repeat(self._output, rows, axis=0)


def test_risk_engine_predicts_ensemble_with_confidence():
    models = [
        _DummyModel([60.0, 21.0, 180.0]),
        _DummyModel([61.0, 20.6, 183.0]),
        _DummyModel([59.4, 21.3, 179.0]),
        _DummyModel([60.8, 20.8, 181.6]),
        _DummyModel([60.2, 21.1, 180.5]),
    ]
    X = np.array([[1.0, 2.0, 3.0]], dtype=float)
    out = RiskEngine.predict_ensemble(
        models=models,
        X=X,
        target_names=("hardness", "tensile", "abrasion"),
        target_scales={"hardness": 3.0, "tensile": 2.0, "abrasion": 8.0},
    )
    assert "hardness" in out.mean_by_target
    assert "tensile" in out.std_by_target
    assert 0.0 <= out.uncertainty_score <= 1.0
    assert 0.0 <= out.confidence_index <= 1.0
    assert out.std_by_target["hardness"] > 0


def test_risk_score_and_confidence_interval():
    risk = RiskEngine.compute_risk_score(
        uncertainty_score=0.28,
        novelty_score=0.32,
        feature_row={
            "oil_to_polymer_ratio": 0.9,
            "crosslink_density_proxy": 3.4,
            "curing_severity_index": 1250,
        },
    )
    assert 0.0 <= risk <= 1.0

    low, high = RiskEngine.confidence_interval(
        mean_value=22.0,
        ensemble_std=0.8,
        residual_std=1.1,
        confidence_level=0.90,
    )
    assert low < high
    assert low < 22.0 < high

