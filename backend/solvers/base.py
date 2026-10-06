"""Solver base class and registry."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Type

from .. import models


class Solver(ABC):
    """Common interface for every solver in the system."""

    name: str = "base"

    @abstractmethod
    def solve(self, problem: models.Problem,
              params: Dict[str, Any]) -> models.Solution:
        """Solve ``problem`` with ``params`` and return a Solution."""

    # ------------------------------------------------------------------ #
    def make_solution(self, problem: models.Problem, starts: Dict[str, int],
                      status: str = "feasible", solve_time: float = 0.0,
                      message: str = "", params: Dict[str, Any] | None = None,
                      lower_bound: float | None = None,
                      extra_metrics: Dict[str, Any] | None = None,
                      objectives: Dict[str, float] | None = None,
                      optimized_objectives: List[str] | None = None,
                      pareto_run_id: str | None = None,
                      pareto_rank: int | None = None) -> models.Solution:
        """Build a Solution from a start-time dict, filling objective/metrics.

        ``objectives`` is the canonical vector for the schedule; when omitted
        it is computed here.  ``optimized_objectives`` declares what the run
        actually optimised (default: the problem's single configured objective,
        or every weighted primitive for a ``custom`` objective)."""
        from .. import objectives as obj_mod
        from .schedule_builder import schedule_to_assignments

        soft = obj_mod.soft_penalty(problem, starts)
        obj = obj_mod.evaluate_objective(problem, starts, soft)
        ms = obj_mod.makespan(problem, starts)
        metrics = obj_mod.compute_metrics(problem, starts)
        if extra_metrics:
            metrics.update(extra_metrics)
        violations = obj_mod.hard_violations(problem, starts)
        if violations:
            status = "infeasible"
            if not message:
                message = "; ".join(violations[:3])

        if optimized_objectives is None:
            if problem.objective.type == "custom":
                optimized_objectives = [k for k, w in problem.objective.weights.items()
                                        if w != 0] or ["makespan"]
            else:
                optimized_objectives = [problem.objective.type]

        return models.Solution(
            id=models.new_id("sol"),
            problem_id=problem.id,
            solver=self.name,
            status=status,
            objective_value=round(obj, 4) if obj is not None else None,
            makespan=ms,
            assignments=schedule_to_assignments(problem, starts),
            metrics=metrics,
            params=params or {},
            solve_time=round(solve_time, 4),
            message=message,
            lower_bound=lower_bound,
            objectives=objectives if objectives is not None
            else obj_mod.canonical_objectives(problem, starts),
            optimized_objectives=optimized_objectives,
            pareto_run_id=pareto_run_id,
            pareto_rank=pareto_rank,
        )

    def run_timed(self, problem: models.Problem, params: Dict[str, Any],
                  fn) -> models.Solution:
        t0 = time.time()
        result = fn()
        result.solve_time = round(time.time() - t0, 4)
        return result


_SOLVERS: Dict[str, Type[Solver]] = {}

# Large penalty added per unscheduled task when a decoder cannot place a task
# inside the horizon; keeps infeasible solutions strictly worse than feasible ones.
INFEASIBILITY_PENALTY = 1e9


def evaluate_priorities(problem: models.Problem,
                        priorities: Dict[str, float]):
    """Decode a priority vector into (starts, objective).  Returns the decoded
    start dict and the penalised objective value."""
    from .schedule_builder import decode
    from .. import objectives

    # When the single scalar objective is overtime cost, the ASAP decoder
    # cannot express "wait for the regular shift"; the multi-objective engine
    # carries an overtime-tolerance gene, but scalar GA/SA do not.  Decode with
    # the maximal overtime weight in that one case so the scalar overtime
    # objective is actually optimisable.
    from .schedule_builder import max_overtime_weight
    weight = (max_overtime_weight(problem)
              if problem.objective.type == "overtime_cost" else 0.0)
    starts = decode(problem, priorities, overtime_weight=weight)
    soft = objectives.soft_penalty(problem, starts)
    obj = objectives.evaluate_objective(problem, starts, soft)
    missing = len(problem.tasks) - len(starts)
    if missing:
        obj += INFEASIBILITY_PENALTY * missing
    return starts, obj


def register(cls: Type[Solver]) -> Type[Solver]:
    _SOLVERS[cls.name] = cls
    return cls


def get_solver(name: str) -> Solver:
    if name not in _SOLVERS:
        raise ValueError(f"unknown solver: {name}")
    return _SOLVERS[name]()


def available_solvers() -> List[str]:
    return sorted(_SOLVERS)


def default_params(name: str) -> Dict[str, Any]:
    """Default parameter set for each solver (kept in one place so the UI and
    CLI always agree on the schema)."""
    defaults = {
        "lp": {"time_limit": 60, "use_lp_lower_bound": True},
        "ip": {"time_limit": 60, "node_limit": 20000, "gap_tolerance": 0.0},
        "genetic": {
            "population_size": 60,
            "generations": 150,
            "mutation_rate": 0.15,
            "crossover_rate": 0.8,
            "elitism": 4,
            "seed": 42,
            "time_limit": 30,
        },
        "simulated_annealing": {
            "initial_temperature": 100.0,
            "cooling_rate": 0.97,
            "iterations": 800,
            "restarts": 1,
            "seed": 42,
            "time_limit": 30,
        },
        "greedy": {},
    }
    return defaults.get(name, {})
