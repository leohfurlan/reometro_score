from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple


@dataclass
class ParetoRecord:
    """
    Row used by non-dominated sorting.

    Objectives are always interpreted as minimization.
    """

    objectives: Tuple[float, ...]
    payload: Any = None
    feasible: bool = True
    constraint_violation: float = 0.0
    rank: int = 0
    crowding_distance: float = 0.0


def dominates_minimization(left: Sequence[float], right: Sequence[float]) -> bool:
    """
    True when `left` dominates `right` in a minimization problem.
    """

    if len(left) != len(right):
        raise ValueError("Objective vectors must share the same length.")
    if not left:
        return False

    is_no_worse = all(float(a) <= float(b) for a, b in zip(left, right))
    is_better = any(float(a) < float(b) for a, b in zip(left, right))
    return bool(is_no_worse and is_better)


def constrained_dominates(left: ParetoRecord, right: ParetoRecord) -> bool:
    """
    Deb's constrained-domination rule:
    - Feasible dominates infeasible
    - If both infeasible, lower violation dominates
    - If both feasible, use Pareto minimization dominance
    """

    if left.feasible and not right.feasible:
        return True
    if not left.feasible and right.feasible:
        return False
    if not left.feasible and not right.feasible:
        return float(left.constraint_violation) < float(right.constraint_violation)
    return dominates_minimization(left.objectives, right.objectives)


def fast_non_dominated_sort(records: Sequence[ParetoRecord]) -> List[List[int]]:
    """
    Returns fronts as list of index lists.
    """

    n = len(records)
    if n == 0:
        return []

    domination_sets: List[List[int]] = [[] for _ in range(n)]
    dominated_count = [0] * n
    fronts: List[List[int]] = [[]]

    for i in range(n):
        for j in range(i + 1, n):
            ri = records[i]
            rj = records[j]
            i_dom_j = constrained_dominates(ri, rj)
            j_dom_i = constrained_dominates(rj, ri)

            if i_dom_j and not j_dom_i:
                domination_sets[i].append(j)
                dominated_count[j] += 1
            elif j_dom_i and not i_dom_j:
                domination_sets[j].append(i)
                dominated_count[i] += 1

    for idx, count in enumerate(dominated_count):
        if count == 0:
            fronts[0].append(idx)
            records[idx].rank = 0

    current = 0
    while current < len(fronts) and fronts[current]:
        next_front: List[int] = []
        for i in fronts[current]:
            for j in domination_sets[i]:
                dominated_count[j] -= 1
                if dominated_count[j] == 0:
                    records[j].rank = current + 1
                    next_front.append(j)
        if next_front:
            fronts.append(next_front)
        current += 1

    return fronts


def assign_crowding_distance(records: Sequence[ParetoRecord], front: Sequence[int]) -> None:
    """
    NSGA-II crowding distance assignment for one front.
    """

    if not front:
        return

    for idx in front:
        records[idx].crowding_distance = 0.0

    if len(front) <= 2:
        for idx in front:
            records[idx].crowding_distance = float("inf")
        return

    num_objectives = len(records[front[0]].objectives)
    for objective_id in range(num_objectives):
        ordered = sorted(front, key=lambda idx: records[idx].objectives[objective_id])
        min_value = float(records[ordered[0]].objectives[objective_id])
        max_value = float(records[ordered[-1]].objectives[objective_id])

        records[ordered[0]].crowding_distance = float("inf")
        records[ordered[-1]].crowding_distance = float("inf")

        denom = max(max_value - min_value, 1e-12)
        for pos in range(1, len(ordered) - 1):
            prev_val = float(records[ordered[pos - 1]].objectives[objective_id])
            next_val = float(records[ordered[pos + 1]].objectives[objective_id])
            if records[ordered[pos]].crowding_distance != float("inf"):
                records[ordered[pos]].crowding_distance += (next_val - prev_val) / denom


def nsga2_select(records: Sequence[ParetoRecord], population_size: int) -> List[ParetoRecord]:
    """
    Selects the next population using rank + crowding distance.
    """

    pop_size = max(1, int(population_size))
    source = list(records or [])
    if not source:
        return []

    fronts = fast_non_dominated_sort(source)
    selected: List[ParetoRecord] = []

    for front in fronts:
        assign_crowding_distance(source, front)
        front_rows = [source[idx] for idx in front]
        if len(selected) + len(front_rows) <= pop_size:
            selected.extend(front_rows)
            continue

        front_rows.sort(key=lambda row: row.crowding_distance, reverse=True)
        slots = pop_size - len(selected)
        if slots > 0:
            selected.extend(front_rows[:slots])
        break

    return selected


def first_front(records: Sequence[ParetoRecord], feasible_only: bool = True) -> List[ParetoRecord]:
    """
    Returns the best available front.
    """

    source = list(records or [])
    if not source:
        return []

    if feasible_only:
        feasible = [row for row in source if row.feasible]
        if feasible:
            source = feasible

    fronts = fast_non_dominated_sort(source)
    if not fronts:
        return []
    return [source[idx] for idx in fronts[0]]

