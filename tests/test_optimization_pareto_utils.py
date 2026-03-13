import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reoscore.optimization.pareto_utils import (
    ParetoRecord,
    constrained_dominates,
    first_front,
    nsga2_select,
)


def test_constrained_domination_prefers_feasible_solution():
    feasible = ParetoRecord(objectives=(2.0, 1.0), feasible=True, constraint_violation=0.0)
    infeasible = ParetoRecord(objectives=(0.1, 0.1), feasible=False, constraint_violation=0.8)
    assert constrained_dominates(feasible, infeasible)
    assert not constrained_dominates(infeasible, feasible)


def test_first_front_and_nsga2_selection():
    records = [
        ParetoRecord(objectives=(1.0, 3.0)),
        ParetoRecord(objectives=(2.0, 2.0)),
        ParetoRecord(objectives=(3.0, 1.0)),
        ParetoRecord(objectives=(2.7, 2.8)),
    ]
    front = first_front(records, feasible_only=True)
    assert len(front) == 3

    selected = nsga2_select(records, population_size=2)
    assert len(selected) == 2
    assert all(isinstance(row, ParetoRecord) for row in selected)

