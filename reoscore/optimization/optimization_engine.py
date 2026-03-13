from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .pareto_utils import ParetoRecord, first_front, nsga2_select


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clone_json(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _clone_json(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_clone_json(v) for v in payload]
    return payload


@dataclass
class OptimizationConfig:
    population_size: int = 96
    generations: int = 24
    mutation_rate: float = 0.18
    mutation_scale: float = 0.16
    crossover_rate: float = 0.92
    random_seed: int = 42
    top_k: int = 10
    ranking_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "cost": 0.32,
            "risk": 0.26,
            "abrasion": 0.17,
            "tensile_loss": 0.25,
        }
    )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None, default_seed: int = 42) -> "OptimizationConfig":
        raw = raw or {}
        return cls(
            population_size=max(24, int(raw.get("population_size") or 96)),
            generations=max(6, int(raw.get("generations") or 24)),
            mutation_rate=max(0.01, min(0.8, float(raw.get("mutation_rate") or 0.18))),
            mutation_scale=max(0.01, min(0.8, float(raw.get("mutation_scale") or 0.16))),
            crossover_rate=max(0.1, min(1.0, float(raw.get("crossover_rate") or 0.92))),
            random_seed=int(raw.get("random_seed") or default_seed),
            top_k=max(1, min(50, int(raw.get("top_k") or 10))),
        )


@dataclass
class IngredientVariable:
    code: str
    min_phr: float
    max_phr: float
    base_phr: float
    material_name: Optional[str] = None
    ingredient_type: Optional[str] = None
    is_elastomer: bool = False
    is_filler: bool = False
    is_carbon_black: bool = False
    template_item: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationProblem:
    application: Optional[str]
    base_process: Dict[str, Any]
    variables: List[IngredientVariable]
    evaluate_candidate: Callable[[List[Dict[str, Any]], Dict[str, Any]], Mapping[str, Any]]
    target_hardness: Optional[float] = None
    hardness_tolerance: float = 3.0
    min_tensile: Optional[float] = None
    max_abrasion: Optional[float] = None
    max_cost: Optional[float] = None
    min_cb: Optional[float] = None
    max_cb: Optional[float] = None
    elastomer_target_sum: float = 100.0
    elastomer_tolerance: float = 0.2
    max_filler_total: float = 120.0
    extra_metric_constraints: Dict[str, Dict[str, float]] = field(default_factory=dict)

    @property
    def dimension(self) -> int:
        return len(self.variables)

    @property
    def base_vector(self) -> np.ndarray:
        return np.array([float(v.base_phr) for v in self.variables], dtype=float)

    @property
    def min_bounds(self) -> np.ndarray:
        return np.array([float(v.min_phr) for v in self.variables], dtype=float)

    @property
    def max_bounds(self) -> np.ndarray:
        return np.array([float(v.max_phr) for v in self.variables], dtype=float)


@dataclass
class OptimizationCandidate:
    candidate_id: int
    genome: np.ndarray
    formulation: List[Dict[str, Any]]
    process_parameters: Dict[str, Any]
    predicted: Dict[str, Any]
    metric_values: Dict[str, float]
    objectives: Tuple[float, float, float, float]
    feasible: bool
    constraint_violation: float
    constraint_violations: List[Dict[str, Any]]
    rank: int = 0
    crowding_distance: float = 0.0
    ranking_score: float = 0.0


class NSGA2OptimizationEngine:
    """
    NSGA-II optimizer with constrained domination.

    Objectives (minimization):
    - estimated_cost_per_kg
    - risk_score
    - (-tensile_pred)  -> equivalent to maximize tensile
    - (-abrasion_resistance_pred) -> equivalent to maximize abrasion resistance
    """

    def __init__(self, config: OptimizationConfig):
        self.config = config
        self._rng = np.random.default_rng(self.config.random_seed)
        self._candidate_counter = 0

    def run(self, problem: OptimizationProblem) -> Dict[str, Any]:
        if problem.dimension == 0:
            raise ValueError("Optimization problem has no variables.")

        population = self._initialize_population(problem)
        population = [self._evaluate(genome, problem) for genome in population]
        evaluations = len(population)

        for _ in range(self.config.generations):
            self._assign_rank_and_crowding(population)
            offspring: List[OptimizationCandidate] = []
            while len(offspring) < self.config.population_size:
                parent_a = self._tournament(population)
                parent_b = self._tournament(population)
                child_1, child_2 = self._crossover(parent_a.genome, parent_b.genome, problem)
                child_1 = self._mutate(child_1, problem)
                child_2 = self._mutate(child_2, problem)
                offspring.append(self._evaluate(child_1, problem))
                if len(offspring) < self.config.population_size:
                    offspring.append(self._evaluate(child_2, problem))
            evaluations += len(offspring)

            combined = population + offspring
            records = self._to_records(combined)
            selected_records = nsga2_select(records, self.config.population_size)
            population = [row.payload for row in selected_records]
            for rec in selected_records:
                cand = rec.payload
                cand.rank = int(rec.rank)
                cand.crowding_distance = float(rec.crowding_distance)

        self._assign_rank_and_crowding(population)
        pareto_candidates = self._pareto_candidates(population)
        ranked = self._rank_front(pareto_candidates)
        top_k = ranked[: self.config.top_k]

        return {
            "summary": {
                "population_size": self.config.population_size,
                "generations": self.config.generations,
                "evaluations": evaluations,
                "pareto_count": len(ranked),
                "top_k": len(top_k),
                "seed": self.config.random_seed,
            },
            "pareto_candidates": [self._candidate_to_dict(row) for row in ranked],
            "top_candidates": [self._candidate_to_dict(row) for row in top_k],
        }

    def _initialize_population(self, problem: OptimizationProblem) -> List[np.ndarray]:
        base = problem.base_vector
        span = np.maximum(problem.max_bounds - problem.min_bounds, 1e-9)

        genomes = []
        for idx in range(self.config.population_size):
            if idx == 0:
                vector = np.array(base, dtype=float)
            else:
                delta = self._rng.normal(
                    loc=0.0,
                    scale=self.config.mutation_scale * span,
                    size=base.shape,
                )
                vector = base + delta
            genomes.append(self._repair(vector, problem))
        return genomes

    def _repair(self, genome: np.ndarray, problem: OptimizationProblem) -> np.ndarray:
        vector = np.asarray(genome, dtype=float).copy()
        vector = np.clip(vector, problem.min_bounds, problem.max_bounds)

        elastomer_idx = [i for i, row in enumerate(problem.variables) if row.is_elastomer]
        if elastomer_idx:
            vector = self._project_group_sum(
                vector=vector,
                group_indices=elastomer_idx,
                target=float(problem.elastomer_target_sum),
                min_bounds=problem.min_bounds,
                max_bounds=problem.max_bounds,
            )

        filler_idx = [i for i, row in enumerate(problem.variables) if row.is_filler]
        if filler_idx:
            filler_total = float(np.sum(vector[filler_idx]))
            if filler_total > float(problem.max_filler_total) and filler_total > 1e-9:
                scale = float(problem.max_filler_total) / filler_total
                vector[filler_idx] = vector[filler_idx] * scale
                vector = np.clip(vector, problem.min_bounds, problem.max_bounds)

        return vector

    @staticmethod
    def _project_group_sum(
        vector: np.ndarray,
        group_indices: Sequence[int],
        target: float,
        min_bounds: np.ndarray,
        max_bounds: np.ndarray,
    ) -> np.ndarray:
        indices = list(group_indices or [])
        if not indices:
            return vector

        out = np.asarray(vector, dtype=float).copy()
        vals = out[indices].copy()
        mins = min_bounds[indices]
        maxs = max_bounds[indices]

        target_clamped = float(np.clip(target, float(np.sum(mins)), float(np.sum(maxs))))
        current = float(np.sum(vals))
        if current <= 1e-9:
            vals = np.full_like(vals, target_clamped / max(1, len(vals)))
        else:
            vals = vals * (target_clamped / current)
        vals = np.clip(vals, mins, maxs)

        for _ in range(18):
            delta = float(target_clamped - np.sum(vals))
            if abs(delta) <= 1e-7:
                break
            if delta > 0:
                capacity = maxs - vals
            else:
                capacity = vals - mins
            active = capacity > 1e-10
            if not np.any(active):
                break
            weight = capacity[active] / max(float(np.sum(capacity[active])), 1e-12)
            if delta > 0:
                vals[active] += weight * delta
            else:
                vals[active] -= weight * abs(delta)
            vals = np.clip(vals, mins, maxs)

        out[indices] = vals
        return out

    def _decode_formulation(
        self, genome: np.ndarray, problem: OptimizationProblem
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for idx, variable in enumerate(problem.variables):
            item = _clone_json(variable.template_item)
            item["material_code"] = variable.code
            item["phr"] = float(max(0.0, genome[idx]))
            if variable.material_name:
                item["material_name"] = variable.material_name
            if variable.ingredient_type:
                item["ingredient_type"] = variable.ingredient_type
            items.append(item)
        return items

    def _evaluate(self, genome: np.ndarray, problem: OptimizationProblem) -> OptimizationCandidate:
        self._candidate_counter += 1
        repaired = self._repair(genome, problem)
        formulation = self._decode_formulation(repaired, problem)
        process = _clone_json(problem.base_process)

        predicted: Dict[str, Any] = {}
        metric_values: Dict[str, float] = {}
        feasible = False
        violation_rows: List[Dict[str, Any]] = []
        violation_score = 1_000.0
        objectives = (1e9, 1e9, 1e9, 1e9)

        try:
            evaluation = problem.evaluate_candidate(formulation, process) or {}
            predicted = _clone_json(evaluation.get("predicted") or {})
            metric_values = {
                str(k): float(v)
                for k, v in (evaluation.get("metric_values") or {}).items()
                if _to_float(v) is not None
            }

            cost = _to_float(predicted.get("estimated_cost_per_kg"))
            risk = _to_float(predicted.get("risk_score"))
            tensile = _to_float(predicted.get("tensile_pred"))
            abrasion_resistance = _to_float(predicted.get("abrasion_resistance_pred"))

            objectives = (
                float(cost if cost is not None else 1e6),
                float(risk if risk is not None else 1e6),
                float(-(tensile if tensile is not None else -1e6)),
                float(-(abrasion_resistance if abrasion_resistance is not None else -1e6)),
            )

            feasible, violation_rows, violation_score = self._constraint_analysis(
                formulation=formulation,
                predicted=predicted,
                metric_values=metric_values,
                problem=problem,
            )
        except Exception as exc:  # pragma: no cover - safety net
            violation_rows = [
                {
                    "metric": "engine_exception",
                    "type": "evaluation_error",
                    "message": str(exc),
                }
            ]

        return OptimizationCandidate(
            candidate_id=self._candidate_counter,
            genome=repaired,
            formulation=formulation,
            process_parameters=process,
            predicted=predicted,
            metric_values=metric_values,
            objectives=objectives,
            feasible=bool(feasible),
            constraint_violation=float(violation_score),
            constraint_violations=violation_rows,
        )

    def _constraint_analysis(
        self,
        formulation: Sequence[Mapping[str, Any]],
        predicted: Mapping[str, Any],
        metric_values: Mapping[str, float],
        problem: OptimizationProblem,
    ) -> Tuple[bool, List[Dict[str, Any]], float]:
        rows: List[Dict[str, Any]] = []
        total_violation = 0.0

        values = {str(k): float(v) for k, v in (metric_values or {}).items()}

        code_to_phr = {}
        elastomer_sum = 0.0
        filler_sum = 0.0
        carbon_black_sum = 0.0

        type_by_code = {var.code: var for var in problem.variables}
        for item in formulation:
            code = str(item.get("material_code") or "").strip()
            phr = _to_float(item.get("phr")) or 0.0
            code_to_phr[code] = float(phr)
            meta = type_by_code.get(code)
            if meta and meta.is_elastomer:
                elastomer_sum += float(phr)
            if meta and meta.is_filler:
                filler_sum += float(phr)
            if meta and meta.is_carbon_black:
                carbon_black_sum += float(phr)

        def add_violation(
            metric: str,
            kind: str,
            value: float,
            bound: float,
            normalized_delta: float,
        ) -> None:
            nonlocal total_violation
            rows.append(
                {
                    "metric": metric,
                    "type": kind,
                    "value": round(float(value), 6),
                    "bound": round(float(bound), 6),
                    "delta_norm": round(float(normalized_delta), 6),
                }
            )
            total_violation += max(0.0, float(normalized_delta))

        hardness = _to_float(predicted.get("hardness_pred"))
        tensile = _to_float(predicted.get("tensile_pred"))
        abrasion = _to_float(predicted.get("abrasion_pred"))
        if abrasion is None:
            abrasion = _to_float(metric_values.get("abrasion"))
        if abrasion is None:
            abrasion = _to_float(predicted.get("abrasion_resistance_pred"))
        cost = _to_float(predicted.get("estimated_cost_per_kg"))

        if problem.target_hardness is not None and hardness is not None:
            delta = abs(float(hardness) - float(problem.target_hardness))
            tol = max(float(problem.hardness_tolerance), 0.0)
            if delta > tol:
                add_violation(
                    metric="hardness",
                    kind="target_tolerance",
                    value=float(hardness),
                    bound=float(problem.target_hardness),
                    normalized_delta=(delta - tol) / max(abs(float(problem.target_hardness)), 1.0),
                )

        if problem.min_tensile is not None and tensile is not None and tensile < float(problem.min_tensile):
            add_violation(
                metric="tensile",
                kind="below_min",
                value=float(tensile),
                bound=float(problem.min_tensile),
                normalized_delta=(float(problem.min_tensile) - float(tensile))
                / max(abs(float(problem.min_tensile)), 1.0),
            )

        if problem.max_abrasion is not None and abrasion is not None and abrasion > float(problem.max_abrasion):
            add_violation(
                metric="abrasion",
                kind="above_max",
                value=float(abrasion),
                bound=float(problem.max_abrasion),
                normalized_delta=(float(abrasion) - float(problem.max_abrasion))
                / max(abs(float(problem.max_abrasion)), 1.0),
            )

        if problem.max_cost is not None and cost is not None and cost > float(problem.max_cost):
            add_violation(
                metric="estimated_cost_per_kg",
                kind="above_max",
                value=float(cost),
                bound=float(problem.max_cost),
                normalized_delta=(float(cost) - float(problem.max_cost))
                / max(abs(float(problem.max_cost)), 1.0),
            )

        if abs(float(elastomer_sum) - float(problem.elastomer_target_sum)) > float(problem.elastomer_tolerance):
            add_violation(
                metric="elastomer_total",
                kind="not_equal_target",
                value=float(elastomer_sum),
                bound=float(problem.elastomer_target_sum),
                normalized_delta=abs(float(elastomer_sum) - float(problem.elastomer_target_sum))
                / max(abs(float(problem.elastomer_target_sum)), 1.0),
            )

        if filler_sum > float(problem.max_filler_total):
            add_violation(
                metric="filler_total",
                kind="above_max",
                value=float(filler_sum),
                bound=float(problem.max_filler_total),
                normalized_delta=(float(filler_sum) - float(problem.max_filler_total))
                / max(abs(float(problem.max_filler_total)), 1.0),
            )

        if problem.min_cb is not None and carbon_black_sum < float(problem.min_cb):
            add_violation(
                metric="carbon_black_total",
                kind="below_min",
                value=float(carbon_black_sum),
                bound=float(problem.min_cb),
                normalized_delta=(float(problem.min_cb) - float(carbon_black_sum))
                / max(abs(float(problem.min_cb)), 1.0),
            )

        if problem.max_cb is not None and carbon_black_sum > float(problem.max_cb):
            add_violation(
                metric="carbon_black_total",
                kind="above_max",
                value=float(carbon_black_sum),
                bound=float(problem.max_cb),
                normalized_delta=(float(carbon_black_sum) - float(problem.max_cb))
                / max(abs(float(problem.max_cb)), 1.0),
            )

        for variable in problem.variables:
            phr = float(code_to_phr.get(variable.code, 0.0))
            if phr < float(variable.min_phr):
                add_violation(
                    metric=f"ingredient_{variable.code}",
                    kind="below_min",
                    value=phr,
                    bound=float(variable.min_phr),
                    normalized_delta=(float(variable.min_phr) - phr)
                    / max(abs(float(variable.min_phr)), 1.0),
                )
            if phr > float(variable.max_phr):
                add_violation(
                    metric=f"ingredient_{variable.code}",
                    kind="above_max",
                    value=phr,
                    bound=float(variable.max_phr),
                    normalized_delta=(phr - float(variable.max_phr))
                    / max(abs(float(variable.max_phr)), 1.0),
                )

        for metric_key, bounds in (problem.extra_metric_constraints or {}).items():
            if not isinstance(bounds, Mapping):
                continue
            value = _to_float(values.get(metric_key))
            if value is None:
                value = _to_float(predicted.get(metric_key))
            if value is None:
                continue
            vmin = _to_float(bounds.get("min"))
            vmax = _to_float(bounds.get("max"))
            if vmin is not None and value < vmin:
                add_violation(
                    metric=metric_key,
                    kind="below_min",
                    value=float(value),
                    bound=float(vmin),
                    normalized_delta=(float(vmin) - float(value)) / max(abs(float(vmin)), 1.0),
                )
            if vmax is not None and value > vmax:
                add_violation(
                    metric=metric_key,
                    kind="above_max",
                    value=float(value),
                    bound=float(vmax),
                    normalized_delta=(float(value) - float(vmax)) / max(abs(float(vmax)), 1.0),
                )

        feasible = len(rows) == 0
        return feasible, rows, float(total_violation)

    def _crossover(
        self, parent_a: np.ndarray, parent_b: np.ndarray, problem: OptimizationProblem
    ) -> Tuple[np.ndarray, np.ndarray]:
        if self._rng.random() >= self.config.crossover_rate:
            return self._repair(parent_a, problem), self._repair(parent_b, problem)

        alpha = self._rng.uniform(low=-0.12, high=1.12, size=parent_a.shape)
        child_1 = alpha * parent_a + (1.0 - alpha) * parent_b
        child_2 = alpha * parent_b + (1.0 - alpha) * parent_a
        return self._repair(child_1, problem), self._repair(child_2, problem)

    def _mutate(self, genome: np.ndarray, problem: OptimizationProblem) -> np.ndarray:
        out = np.asarray(genome, dtype=float).copy()
        span = np.maximum(problem.max_bounds - problem.min_bounds, 1e-9)
        for idx in range(out.size):
            if self._rng.random() <= self.config.mutation_rate:
                out[idx] += self._rng.normal(loc=0.0, scale=self.config.mutation_scale * span[idx])
        return self._repair(out, problem)

    def _to_records(self, population: Sequence[OptimizationCandidate]) -> List[ParetoRecord]:
        return [
            ParetoRecord(
                objectives=tuple(candidate.objectives),
                payload=candidate,
                feasible=bool(candidate.feasible),
                constraint_violation=float(candidate.constraint_violation),
            )
            for candidate in population
        ]

    def _assign_rank_and_crowding(self, population: Sequence[OptimizationCandidate]) -> None:
        records = self._to_records(population)
        selected = nsga2_select(records, len(records))
        for rec in selected:
            cand = rec.payload
            cand.rank = int(rec.rank)
            cand.crowding_distance = float(rec.crowding_distance)

    def _tournament(self, population: Sequence[OptimizationCandidate]) -> OptimizationCandidate:
        if len(population) == 1:
            return population[0]
        idx_a, idx_b = self._rng.choice(len(population), size=2, replace=False)
        a = population[int(idx_a)]
        b = population[int(idx_b)]

        if a.rank != b.rank:
            return a if a.rank < b.rank else b
        if a.feasible != b.feasible:
            return a if a.feasible else b
        if abs(a.constraint_violation - b.constraint_violation) > 1e-12:
            return a if a.constraint_violation < b.constraint_violation else b
        if a.crowding_distance != b.crowding_distance:
            return a if a.crowding_distance > b.crowding_distance else b
        return a if self._rng.random() < 0.5 else b

    def _pareto_candidates(self, population: Sequence[OptimizationCandidate]) -> List[OptimizationCandidate]:
        records = self._to_records(population)
        front_records = first_front(records, feasible_only=True)
        if not front_records:
            front_records = first_front(records, feasible_only=False)
        return [row.payload for row in front_records]

    def _rank_front(self, candidates: Sequence[OptimizationCandidate]) -> List[OptimizationCandidate]:
        rows = list(candidates or [])
        if not rows:
            return []

        costs = np.array([float(c.objectives[0]) for c in rows], dtype=float)
        risks = np.array([float(c.objectives[1]) for c in rows], dtype=float)
        tensile_loss = np.array([float(c.objectives[2]) for c in rows], dtype=float)
        abrasion_objective = np.array([float(c.objectives[3]) for c in rows], dtype=float)

        def normalize(vector: np.ndarray) -> np.ndarray:
            if vector.size == 0:
                return vector
            vmin = float(np.min(vector))
            vmax = float(np.max(vector))
            if abs(vmax - vmin) <= 1e-12:
                return np.zeros_like(vector)
            return (vector - vmin) / (vmax - vmin)

        c_norm = normalize(costs)
        r_norm = normalize(risks)
        a_norm = normalize(abrasion_objective)
        t_norm = normalize(tensile_loss)

        w = self.config.ranking_weights
        scores = (
            float(w.get("cost", 0.32)) * c_norm
            + float(w.get("risk", 0.26)) * r_norm
            + float(w.get("abrasion", 0.17)) * a_norm
            + float(w.get("tensile_loss", 0.25)) * t_norm
        )

        for idx, row in enumerate(rows):
            row.ranking_score = float(scores[idx])

        rows.sort(
            key=lambda row: (
                row.ranking_score,
                row.rank,
                -row.crowding_distance,
                row.constraint_violation,
            )
        )
        return rows

    @staticmethod
    def _candidate_to_dict(candidate: OptimizationCandidate) -> Dict[str, Any]:
        pred = dict(candidate.predicted or {})
        return {
            "candidate_id": candidate.candidate_id,
            "formulation": {"ingredients": _clone_json(candidate.formulation)},
            "process_parameters": _clone_json(candidate.process_parameters),
            "predicted": pred,
            "cost": _to_float(pred.get("estimated_cost_per_kg")),
            "risk": _to_float(pred.get("risk_score")),
            "confidence_index": _to_float(pred.get("confidence_index")),
            "objective_vector": {
                "cost": float(candidate.objectives[0]),
                "risk": float(candidate.objectives[1]),
                "negative_tensile": float(candidate.objectives[2]),
                "negative_abrasion_resistance": float(candidate.objectives[3]),
            },
            "metric_values": _clone_json(candidate.metric_values),
            "feasible": bool(candidate.feasible),
            "constraint_violation_score": round(float(candidate.constraint_violation), 8),
            "constraint_violations": _clone_json(candidate.constraint_violations),
            "rank": int(candidate.rank),
            "crowding_distance": float(candidate.crowding_distance),
            "ranking_score": round(float(candidate.ranking_score), 8),
        }
