"""Multi-objective (Pareto) optimisation via NSGA-II.

Where the single-objective solvers scalarise everything into one number, this
module optimises several objectives *simultaneously* and returns the set of
mutually non-dominated schedules — the Pareto front — so the trade-off (e.g.
makespan vs. overtime cost) becomes visible instead of being decided in
advance by a hidden weighting.

Design notes:

* *Representation* — random-key priority vectors like the GA/SA solvers, plus
  a per-task *delay gene* decoded by
  :func:`schedule_builder.decode_with_delays`.  The plain ASAP decoder can
  never express "wait for the next regular-hours window", which is exactly
  the move that trades makespan for overtime cost; the delay genes make that
  trade-off reachable.  Single-objective solvers keep the plain decoder.
* *Front size guard* — the number of non-dominated solutions grows with
  problem size.  The persisted front is capped at ``max_front`` solutions,
  keeping the extremes and the most isolated points (crowding-distance
  truncation), so storage, list pages and charts never have to absorb an
  unbounded front.
* *Consistency* — every returned Solution is built through
  :meth:`Solver.make_solution` (the same code path as single-objective runs)
  and then annotated with ``objectives`` / ``objective_values`` / ``run_id``.
  All solutions of one run share the ``run_id``.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Sequence, Tuple

from . import models, objectives
from .solvers.base import INFEASIBILITY_PENALTY, Solver
from .solvers.schedule_builder import decode_with_delays

PARETO_DEFAULT_PARAMS: Dict[str, Any] = {
    "population_size": 80,
    "generations": 120,
    "mutation_rate": 0.15,
    "crossover_rate": 0.8,
    "seed": 42,
    "time_limit": 30,
    "max_front": 50,          # max number of front solutions persisted/returned
    "max_delay": 24,          # max slots a task may wait for cheaper (regular) hours
}

#: Objectives offered when the caller does not pick any.
DEFAULT_OBJECTIVES: List[str] = ["makespan", "overtime_cost"]

Vector = Tuple[float, ...]
# genes = {"prio": {task: key}, "delay": {task: fraction of max_delay}}
# individual = (genes, objective vector, decoded starts)
_Genes = Dict[str, Dict[str, float]]
_Individual = Tuple[_Genes, Vector, Dict[str, int]]


class _SolutionFactory(Solver):
    """Reuses the shared solution-building code path (objective_value,
    metrics, feasibility) so Pareto solutions are recorded with exactly the
    same 口径 as single-objective ones.  Not registered as a solver: a Pareto
    run yields a *set* of solutions and is driven through solve_pareto()."""
    name = "nsga2"

    def solve(self, problem, params):  # pragma: no cover - not a /solve solver
        raise NotImplementedError("use solve_pareto() for multi-objective runs")


# --------------------------------------------------------------------------- #
# Pareto machinery
# --------------------------------------------------------------------------- #

def dominates(a: Vector, b: Vector) -> bool:
    """True when vector ``a`` Pareto-dominates ``b`` (all objectives min)."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def non_dominated_sort(vectors: Sequence[Vector]) -> List[List[int]]:
    """Fast non-dominated sort.  Returns fronts as lists of indices, best first."""
    n = len(vectors)
    dominated_by_count = [0] * n
    dominates_list: List[List[int]] = [[] for _ in range(n)]
    fronts: List[List[int]] = [[]]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if dominates(vectors[i], vectors[j]):
                dominates_list[i].append(j)
            elif dominates(vectors[j], vectors[i]):
                dominated_by_count[i] += 1
        if dominated_by_count[i] == 0:
            fronts[0].append(i)
    current = 0
    while fronts[current]:
        nxt: List[int] = []
        for i in fronts[current]:
            for j in dominates_list[i]:
                dominated_by_count[j] -= 1
                if dominated_by_count[j] == 0:
                    nxt.append(j)
        current += 1
        fronts.append(nxt)
    fronts.pop()
    return fronts


def crowding_distance(vectors: Sequence[Vector],
                      idxs: Sequence[int]) -> Dict[int, float]:
    """Crowding distance within one front; boundary points get infinity."""
    dist = {i: 0.0 for i in idxs}
    if not idxs:
        return dist
    k = len(vectors[idxs[0]])
    for m in range(k):
        ordered = sorted(idxs, key=lambda i: vectors[i][m])
        dist[ordered[0]] = dist[ordered[-1]] = float("inf")
        lo, hi = vectors[ordered[0]][m], vectors[ordered[-1]][m]
        span = hi - lo
        if span <= 0:
            continue
        for prev_i, mid_i, next_i in zip(ordered, ordered[1:], ordered[2:]):
            dist[mid_i] += (vectors[next_i][m] - vectors[prev_i][m]) / span
    return dist


# --------------------------------------------------------------------------- #
# Genetic operators (random-key representation, same style as the GA solver)
# --------------------------------------------------------------------------- #

def _tournament(n: int, rank: Dict[int, int], crowd: Dict[int, float],
                rng: random.Random, k: int = 2) -> int:
    best = -1
    for _ in range(k):
        i = rng.randrange(n)
        if best < 0 or (rank[i], -crowd.get(i, 0.0)) < (rank[best], -crowd.get(best, 0.0)):
            best = i
    return best


def _random_genes(task_ids: List[str], rng: random.Random) -> _Genes:
    return {
        "prio": {t: rng.random() for t in task_ids},
        "delay": {t: rng.random() for t in task_ids},
    }


def _crossover(a: _Genes, b: _Genes, rng: random.Random) -> _Genes:
    return {
        part: {t: (a[part][t] if rng.random() < 0.5 else b[part][t])
               for t in a[part]}
        for part in ("prio", "delay")
    }


def _mutate(genes: _Genes, rate: float, rng: random.Random) -> _Genes:
    out = {part: dict(values) for part, values in genes.items()}
    for part in ("prio", "delay"):
        for t in out[part]:
            if rng.random() < rate:
                out[part][t] = rng.random()
    return out


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def validate_objective_names(names: Sequence[str]) -> List[str]:
    """Normalise and validate the requested objective list."""
    names = [str(n) for n in names]
    if len(names) < 2:
        raise ValueError("multi-objective optimisation needs at least 2 objectives")
    if len(set(names)) != len(names):
        raise ValueError("duplicate objectives: " + ", ".join(names))
    known = set(objectives.primitive_names())
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(
            f"unknown objectives: {', '.join(unknown)} "
            f"(available: {', '.join(objectives.primitive_names())})")
    return names


def solve_pareto(problem: models.Problem,
                 objective_names: Sequence[str] = (),
                 params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Optimise ``objective_names`` simultaneously; return the Pareto front.

    Returns a dict with ``run_id``, ``objectives``, ``params``, ``solve_time``
    and ``solutions`` — a list of :class:`models.Solution` (not yet persisted;
    the caller stores them so web and CLI share one persistence path).
    """
    names = validate_objective_names(objective_names or DEFAULT_OBJECTIVES)

    p = dict(PARETO_DEFAULT_PARAMS)
    p.update(params or {})
    pop_size = max(4, int(p.get("population_size", 80)))
    generations = max(1, int(p.get("generations", 120)))
    mutation_rate = float(p.get("mutation_rate", 0.15))
    crossover_rate = float(p.get("crossover_rate", 0.8))
    time_limit = float(p.get("time_limit", 30))
    max_front = max(2, int(p.get("max_front", 50)))
    max_delay = max(0, int(p.get("max_delay", 24)))
    seed = int(p.get("seed", 42))
    p.update(population_size=pop_size, generations=generations,
             mutation_rate=mutation_rate, crossover_rate=crossover_rate,
             time_limit=time_limit, max_front=max_front, max_delay=max_delay,
             seed=seed)

    rng = random.Random(seed)
    t0 = time.time()
    task_ids = [t.id for t in problem.tasks]
    n_tasks = len(task_ids)

    def evaluate(genes: _Genes) -> _Individual:
        starts = decode_with_delays(problem, genes["prio"], genes["delay"],
                                    max_delay)
        vec = tuple(objectives.evaluate_primitive(n, problem, starts)
                    for n in names)
        missing = n_tasks - len(starts)
        if missing:  # keep infeasible points strictly dominated by feasible ones
            vec = tuple(v + INFEASIBILITY_PENALTY * missing for v in vec)
        return (genes, vec, starts)

    # -- initial population ------------------------------------------------ #
    pop: List[_Individual] = [
        evaluate(_random_genes(task_ids, rng))
        for _ in range(pop_size)
    ]

    timed_out = False
    generations_run = 0
    for gen in range(generations):
        if time.time() - t0 > time_limit:
            timed_out = True
            break
        generations_run = gen + 1

        vectors = [ind[1] for ind in pop]
        fronts = non_dominated_sort(vectors)
        rank: Dict[int, int] = {}
        crowd: Dict[int, float] = {}
        for r, front in enumerate(fronts):
            crowd.update(crowding_distance(vectors, front))
            for i in front:
                rank[i] = r

        offspring: List[_Individual] = []
        while len(offspring) < pop_size:
            i1 = _tournament(len(pop), rank, crowd, rng)
            i2 = _tournament(len(pop), rank, crowd, rng)
            if rng.random() < crossover_rate:
                child_keys = _crossover(pop[i1][0], pop[i2][0], rng)
            else:
                child_keys = dict(pop[i1][0])
            child_keys = _mutate(child_keys, mutation_rate, rng)
            offspring.append(evaluate(child_keys))

        # environmental selection over parents + offspring
        combined = pop + offspring
        vectors = [ind[1] for ind in combined]
        fronts = non_dominated_sort(vectors)
        new_pop: List[_Individual] = []
        for front in fronts:
            if len(new_pop) + len(front) <= pop_size:
                new_pop.extend(combined[i] for i in front)
                continue
            cd = crowding_distance(vectors, front)
            for i in sorted(front, key=lambda i: -cd[i]):
                if len(new_pop) < pop_size:
                    new_pop.append(combined[i])
            break
        pop = new_pop

    # -- extract the first front ------------------------------------------- #
    vectors = [ind[1] for ind in pop]
    first_front = non_dominated_sort(vectors)[0] if pop else []

    # de-duplicate identical objective vectors (different keys can decode to
    # the same schedule)
    seen: Dict[Vector, int] = {}
    for i in first_front:
        seen.setdefault(tuple(round(v, 6) for v in vectors[i]), i)
    candidates = sorted(seen.values(), key=lambda i: vectors[i])
    raw_front_size = len(candidates)

    # cap the front: keep extreme + most isolated points
    truncated = False
    if len(candidates) > max_front:
        truncated = True
        cd = crowding_distance(vectors, candidates)
        keep = set(sorted(candidates, key=lambda i: -cd[i])[:max_front])
        candidates = [i for i in candidates if i in keep]

    solve_time = time.time() - t0
    run_id = models.new_id("run")
    factory = _SolutionFactory()
    message = (f"NSGA-II：{generations_run} 代 × 种群 {pop_size}，"
               f"前沿 {len(candidates)} 个方案"
               + (f"（原始 {raw_front_size} 个，已按分散度截断）" if truncated else "")
               + ("；已达时间上限" if timed_out else ""))

    solutions: List[models.Solution] = []
    for i in candidates:
        starts = pop[i][2]
        feasible = len(starts) == n_tasks
        sol = factory.make_solution(
            problem, starts,
            status="feasible" if feasible else "infeasible",
            solve_time=solve_time,
            message=message,
            params=p,
            extra_metrics={
                "generations": generations_run,
                "population_size": pop_size,
                "front_size": len(candidates),
                "front_size_raw": raw_front_size,
                "feasible": feasible,
            })
        sol.objectives = list(names)
        sol.objective_values = objectives.evaluate_objectives(problem, starts, list(names))
        sol.run_id = run_id
        solutions.append(sol)

    solutions.sort(key=lambda s: tuple(s.objective_values.get(n, 0.0) for n in names))
    return {
        "run_id": run_id,
        "objectives": list(names),
        "params": p,
        "solutions": solutions,
        "solve_time": round(solve_time, 4),
        "timed_out": timed_out,
        "generations": generations_run,
    }
