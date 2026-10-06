"""
Multi-objective optimisation and Pareto-front handling.

Single-objective runs collapse everything into one weighted scalar; this
module instead optimises several primitives at once (typically ``makespan``
and ``overtime_cost``) and returns the set of mutually non-dominated
schedules -- the Pareto front.

The search is NSGA-II over the same random-key representation used by the GA
and SA solvers (:mod:`backend.solvers.schedule_builder` decodes every key
vector into a precedence-/capacity-feasible schedule, so every point on the
front is a *real, feasible schedule* and can be stored as an ordinary
:class:`~backend.models.Solution`).

Scalability safeguards (the front can explode on big instances):

* every decoded schedule passes through a bounded LRU evaluation cache,
* the non-dominated archive is periodically pruned to a hard cap,
* the returned front is downsampled by equal-stride selection (endpoints are
  always kept) to a caller-supplied ``max_points`` -- plotting and storage
  therefore stay bounded regardless of problem size.
"""

from __future__ import annotations

import random
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import models, objectives
from .solvers import schedule_builder
from .solvers.base import INFEASIBILITY_PENALTY, Solver

PARETO_SOLVER_NAME = "nsga2"

DEFAULT_PARAMS: Dict[str, Any] = {
    "population_size": 48,
    "generations": 80,
    "mutation_rate": 0.15,
    "crossover_rate": 0.85,
    "seed": 42,
    "time_limit": 60,
    "max_points": 40,        # returned/stored front size cap (plot-safe)
    # internal safety nets (not exposed in the UI)
    "eval_cache_size": 20000,
    "archive_cap": 4000,
}

# Objectives the multi-objective engine can optimise (anything the SGS decoder
# can evaluate per schedule, i.e. all primitives except the exact-solver-only
# "custom" aggregate).
SUPPORTED_OBJECTIVES = tuple(objectives.CANONICAL_OBJECTIVES)

KeyVec = Dict[str, float]
Starts = Dict[str, int]
# An individual is (priority keys, overtime-tolerance beta in [0, 1]).
Individual = Tuple[KeyVec, float]


class _ParetoSolver(Solver):
    """Minimal Solver instance only used for its shared ``make_solution``
    factory, so Pareto points are serialised exactly like normal solutions."""

    name = PARETO_SOLVER_NAME

    def solve(self, problem, params):  # pragma: no cover - engine drives search
        raise NotImplementedError("use run_pareto()")


_FACTORY = _ParetoSolver()


# --------------------------------------------------------------------------- #
# Dominance / NSGA-II primitives
# --------------------------------------------------------------------------- #

def no_worse(a: float, b: float, minimize: bool) -> bool:
    return a <= b if minimize else a >= b


def strictly_better(a: float, b: float, minimize: bool) -> bool:
    return a < b if minimize else a > b


def dominates(va: Sequence[float], vb: Sequence[float],
              minimize: Sequence[bool]) -> bool:
    """True iff vector ``va`` Pareto-dominates ``vb``."""
    if len(va) != len(vb) or len(va) != len(minimize):
        raise ValueError("vector / direction length mismatch")
    all_ge = all(no_worse(a, b, m) for a, b, m in zip(va, vb, minimize))
    any_strict = any(strictly_better(a, b, m)
                     for a, b, m in zip(va, vb, minimize))
    return all_ge and any_strict


def fast_non_dominated_sort(vectors: Sequence[Sequence[float]],
                            minimize: Sequence[bool]
                            ) -> List[List[int]]:
    """Classic NSGA-II non-dominated sort.  Returns a list of fronts, each a
    list of indices (front 0 is the non-dominated set)."""
    n = len(vectors)
    domination_sets: List[set] = [set() for _ in range(n)]
    domination_counts = [0] * n
    fronts: List[List[int]] = [[]]
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if dominates(vectors[p], vectors[q], minimize):
                domination_sets[p].add(q)
            elif dominates(vectors[q], vectors[p], minimize):
                domination_counts[p] += 1
        if domination_counts[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt: List[int] = []
        for p in fronts[i]:
            for q in domination_sets[p]:
                domination_counts[q] -= 1
                if domination_counts[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(nxt)
    return fronts[:-1] if fronts and not fronts[-1] else fronts


def crowding_distance(vectors: Sequence[Sequence[float]],
                      indices: Sequence[int]) -> Dict[int, float]:
    """NSGA-II crowding distance for the members of one front."""
    dist = {i: 0.0 for i in indices}
    if len(indices) <= 2:
        return {i: float("inf") for i in indices}
    m = len(vectors[0])
    for dim in range(m):
        order = sorted(indices, key=lambda i: vectors[i][dim])
        dist[order[0]] = float("inf")
        dist[order[-1]] = float("inf")
        lo, hi = vectors[order[0]][dim], vectors[order[-1]][dim]
        span = (hi - lo) or 1.0
        for k in range(1, len(order) - 1):
            if dist[order[k]] == float("inf"):
                continue
            dist[order[k]] += (vectors[order[k + 1]][dim]
                               - vectors[order[k - 1]][dim]) / span
    return dist


def nondominated_filter(points: Sequence[Tuple[Any, Sequence[float]]],
                        minimize: Sequence[bool]
                        ) -> List[Tuple[Any, Sequence[float]]]:
    """Keep only nondominated (payload, vector) pairs."""
    if not points:
        return []
    vectors = [v for _, v in points]
    front0 = set(fast_non_dominated_sort(vectors, minimize)[0])
    return [points[i] for i in sorted(front0)]


def downsample(items: Sequence[Tuple[Any, Sequence[float]]],
               max_points: int) -> Tuple[List[Tuple[Any, Sequence[float]]], bool]:
    """Equal-stride selection that always keeps both anchor endpoints."""
    n = len(items)
    if max_points <= 0 or n <= max_points:
        return list(items), False
    if max_points == 1:
        return [items[0]], True
    picked = []
    seen = set()
    for i in range(max_points):
        idx = round(i * (n - 1) / (max_points - 1))
        if idx not in seen:
            seen.add(idx)
            picked.append(items[idx])
    return picked, True


# --------------------------------------------------------------------------- #
# NSGA-II
# --------------------------------------------------------------------------- #

class _Evaluator:
    """Decodes (priority keys, overtime-tolerance beta) with a bounded LRU
    cache and returns the objective vector.  Beta=0 is the ordinary ASAP
    decoder; positive beta makes the decoder wait for regular shifts.  The
    cap keeps memory bounded on large instances."""

    def __init__(self, problem: models.Problem, names: List[str],
                 cache_size: int):
        self.problem = problem
        self.names = names
        self.cache: "OrderedDict[Tuple, Tuple[Tuple[str, int], Tuple[float, ...]]]" = OrderedDict()
        self.cache_size = max(256, cache_size)
        self.n_evaluated = 0

    def __call__(self, keys: KeyVec, beta: float = 0.0
                 ) -> Tuple[Tuple[str, int], Tuple[float, ...]]:
        cache_key = (tuple(sorted((t, round(k, 9)) for t, k in keys.items())),
                     round(beta, 6))
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.cache.move_to_end(cache_key)
            return cached
        self.n_evaluated += 1
        starts = schedule_builder.decode(self.problem, keys,
                                         overtime_weight=beta)
        missing = len(self.problem.tasks) - len(starts)
        vec = []
        for name in self.names:
            v = objectives.evaluate_primitive(name, self.problem, starts)
            if missing:
                v += INFEASIBILITY_PENALTY * missing
            vec.append(v)
        record = (tuple(sorted(starts.items())), tuple(vec))
        self.cache[cache_key] = record
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return record


def _uniform_crossover(a: KeyVec, b: KeyVec, rng: random.Random) -> KeyVec:
    return {tid: (a[tid] if rng.random() < 0.5 else b[tid]) for tid in a}


def _mutate(ind: KeyVec, rate: float, rng: random.Random) -> KeyVec:
    out = dict(ind)
    for tid in out:
        if rng.random() < rate:
            out[tid] = rng.random()
    return out


def _crossover_ind(ia: Individual, ib: Individual,
                   rng: random.Random) -> Individual:
    keys = _uniform_crossover(ia[0], ib[0], rng)
    beta = ia[1] if rng.random() < 0.5 else ib[1]
    return keys, beta


def _mutate_ind(ind: Individual, rate: float,
                rng: random.Random, beta_mutate: float = 0.1) -> Individual:
    return _mutate(ind[0], rate, rng), ind[1]


def _scalar_seed(problem: models.Problem, names: List[str], weights: Tuple[float, ...],
                 rng: random.Random, t0: float, time_limit: float,
                 evaluator: "_Evaluator", task_ids: List[str],
                 beta: float = 0.0,
                 budget_pop: int = 20, budget_gens: int = 25) -> KeyVec:
    """A short weighted-sum GA that anchors an extreme of the front (fastest
    schedule / cheapest-overtime schedule).  Values are normalised from a
    random probe so weights compare across differently-scaled objectives."""
    probes = [evaluator({tid: rng.random() for tid in task_ids}, beta)[1]
              for _ in range(12)]
    mins = [min(p[d] for p in probes) for d in range(len(names))]
    maxs = [max(p[d] for p in probes) for d in range(len(names))]
    spans = [(hi - lo) or 1.0 for lo, hi in zip(mins, maxs)]

    def scalar(vec: Sequence[float]) -> float:
        return sum(w * (v - lo) / s
                   for w, v, lo, s in zip(weights, vec, mins, spans))

    pop = [{tid: rng.random() for tid in task_ids} for _ in range(budget_pop)]
    scored = [(ind, evaluator(ind, beta)[1]) for ind in pop]
    best = min(scored, key=lambda x: scalar(x[1]))
    for _ in range(budget_gens):
        if time.time() - t0 > time_limit * 0.35:
            break
        scored.sort(key=lambda x: scalar(x[1]))
        elites = [ind for ind, _ in scored[:3]]
        nxt = list(elites)
        while len(nxt) < budget_pop:
            p1 = scored[rng.randrange(len(scored) // 2 + 1)][0]
            p2 = scored[rng.randrange(len(scored) // 2 + 1)][0]
            child = _mutate(_uniform_crossover(p1, p2, rng), 0.2, rng)
            nxt.append(child)
        scored = [(ind, evaluator(ind, beta)[1]) for ind in nxt]
        cand = min(scored, key=lambda x: scalar(x[1]))
        if scalar(cand[1]) < scalar(best[1]):
            best = cand
    return best[0]


def run_pareto(problem: models.Problem,
               objective_names: List[str],
               params: Optional[Dict[str, Any]] = None,
               minimize: Optional[List[bool]] = None,
               ) -> Tuple[models.ParetoRun, List[models.Solution]]:
    """Optimise ``objective_names`` jointly and return (run, front solutions).

    Every front member is a fully-built :class:`models.Solution` carrying the
    run id and its rank; the returned :class:`models.ParetoRun` is the run
    header.  Nothing is persisted here (the caller does that)."""
    names = list(dict.fromkeys(objective_names))  # de-dup, keep order
    if len(names) < 2:
        raise ValueError("multi-objective run needs at least two objectives")
    bad = [n for n in names if n not in SUPPORTED_OBJECTIVES]
    if bad:
        raise ValueError(f"unsupported objective(s): {', '.join(bad)}; "
                         f"choose from {', '.join(SUPPORTED_OBJECTIVES)}")
    if minimize is None:
        minimize = [True] * len(names)
    if len(minimize) != len(names):
        raise ValueError("minimize must have one flag per objective")

    cfg = dict(DEFAULT_PARAMS)
    cfg.update(params or {})
    pop_size = max(4, int(cfg["population_size"]))
    generations = max(0, int(cfg["generations"]))
    mutation_rate = float(cfg["mutation_rate"])
    crossover_rate = float(cfg["crossover_rate"])
    max_points = max(2, int(cfg["max_points"]))
    archive_cap = max(pop_size * 4, int(cfg["archive_cap"]))
    time_limit = float(cfg["time_limit"])
    rng = random.Random(int(cfg["seed"]))

    task_ids = [t.id for t in problem.tasks]
    evaluator = _Evaluator(problem, names, int(cfg["eval_cache_size"]))
    t0 = time.time()

    # Beta maps the [0,1] tolerance gene onto a decoder weight large enough to
    # delay a task by up to a horizon to avoid one unit of overtime charge.
    beta_scale = schedule_builder.max_overtime_weight(problem)
    overtime_idx = names.index("overtime_cost") if "overtime_cost" in names else None

    # ---- initial population: greedy + both scalar extremes + random ------ #
    # Each anchor is a (priority keys, beta) individual: beta=0 seeks speed,
    # beta=1 makes the decoder wait for regular shifts (zero overtime anchor).
    seed_individuals: List[Individual] = [(schedule_builder.greedy_order(problem), 0.0)]
    for d in range(len(names)):
        if time.time() - t0 > time_limit:
            break
        w = tuple(1.0 if i == d else 0.0 for i in range(len(names)))
        anchor_beta = 1.0 if overtime_idx == d else 0.0
        keys = _scalar_seed(
            problem, names, w, rng, t0, time_limit, evaluator, task_ids,
            beta=anchor_beta * beta_scale)
        seed_individuals.append((keys, anchor_beta))
    while len(seed_individuals) < pop_size:
        seed_individuals.append(({tid: rng.random() for tid in task_ids},
                                 rng.random()))

    # member: (individual, starts_tuple, vector)
    population = []
    for keys, beta_gene in seed_individuals[:pop_size]:
        starts_tuple, vec = evaluator(keys, beta_gene * beta_scale)
        population.append(((keys, beta_gene), starts_tuple, vec))

    # Global archive of every feasible non-dominated schedule seen.
    archive: Dict[Tuple[str, int], Tuple[float, ...]] = {}
    n_tasks = len(task_ids)

    def update_archive(members) -> None:
        for _, starts_tuple, vec in members:
            if len(starts_tuple) != n_tasks:
                continue  # feasible schedules only
            archive[starts_tuple] = vec
        if len(archive) > archive_cap:
            kept = nondominated_filter(list(archive.items()), minimize)
            survivors = dict(kept[:archive_cap])
            archive.clear()
            archive.update(survivors)

    update_archive(population)

    def rank_crowd(members):
        vecs = [m[2] for m in members]
        fronts = fast_non_dominated_sort(vecs, minimize)
        rank_of = {}
        crowd_of: Dict[int, float] = {}
        for r, front in enumerate(fronts):
            for idx in front:
                rank_of[idx] = r
            crowd_of.update(crowding_distance(vecs, front))
        return rank_of, crowd_of

    generations_run = 0
    timed_out = False
    for gen in range(generations):
        if time.time() - t0 > time_limit:
            timed_out = True
            break
        generations_run = gen + 1
        rank_of, crowd_of = rank_crowd(population)

        def tournament(k: int = 2) -> Tuple[KeyVec, Tuple[str, int], Tuple[float, ...]]:
            i, j = (rng.randrange(len(population)) for _ in range(k))
            if (rank_of[i], -crowd_of[i]) <= (rank_of[j], -crowd_of[j]):
                return population[i]
            return population[j]

        offspring = []
        while len(offspring) < pop_size:
            p1, p2 = tournament(), tournament()
            if rng.random() < crossover_rate:
                child = _crossover_ind(p1[0], p2[0], rng)
            else:
                child = (dict(p1[0][0]), p1[0][1])
            child = _mutate_ind(child, mutation_rate, rng)
            # the overtime-tolerance gene evolves as well, with its own small
            # random reset so the search keeps exploring the speed<->overtime
            # spectrum even after the anchors converge
            keys, beta_gene = child
            if rng.random() < 0.1:
                beta_gene = rng.random()
            child = (keys, beta_gene)
            starts_tuple, vec = evaluator(child[0], child[1] * beta_scale)
            offspring.append((child, starts_tuple, vec))

        combined = population + offspring
        fronts = fast_non_dominated_sort([m[2] for m in combined], minimize)
        next_pop: List[Tuple[KeyVec, Tuple[str, int], Tuple[float, ...]]] = []
        for front in fronts:
            if len(next_pop) + len(front) > pop_size:
                crowd = crowding_distance([m[2] for m in combined], front)
                front = sorted(front, key=lambda i: crowd[i], reverse=True)
                need = pop_size - len(next_pop)
                next_pop.extend(combined[i] for i in front[:need])
                break
            next_pop.extend(combined[i] for i in front)
        population = next_pop
        update_archive(population)

    elapsed = time.time() - t0

    # ---- final front: unique objective vectors, sorted on dimension 0 -- #
    # Different schedules can share an objective vector (many orderings give
    # the same makespan/overtime); collapse those so the stored front and its
    # plot carry one point per distinct trade-off, not a pile of duplicates.
    nd = nondominated_filter(list(archive.items()), minimize)
    unique: "OrderedDict[Tuple[float, ...], Tuple[str, int]]" = OrderedDict()
    for starts_tuple, vec in nd:
        unique.setdefault(vec, starts_tuple)
    final = [(starts_tuple, vec) for vec, starts_tuple in unique.items()]
    final.sort(key=lambda item: (item[1][0], item[1]))
    n_generated = len(final)
    picked, truncated = downsample(final, max_points)

    run_id = models.new_id("pareto")
    run = models.ParetoRun(
        id=run_id,
        problem_id=problem.id,
        solver=PARETO_SOLVER_NAME,
        objectives=names,
        minimize=list(minimize),
        params=cfg,
        status="timeout" if timed_out else "feasible",
        n_evaluated=evaluator.n_evaluated,
        n_generated=n_generated,
        n_returned=len(picked),
        truncated=truncated,
        solve_time=round(elapsed, 4),
        message=(f"NSGA-II: {generations_run} generations, pop {pop_size}, "
                 f"{evaluator.n_evaluated} schedules evaluated"
                 + (" (time limit reached)" if timed_out else "")),
    )

    solutions: List[models.Solution] = []
    for rank, (starts_tuple, _vec) in enumerate(picked):
        starts = dict(starts_tuple)
        sol = _FACTORY.make_solution(
            problem, starts,
            status="feasible",
            solve_time=round(elapsed, 4),
            message=run.message,
            params=cfg,
            optimized_objectives=names,
            pareto_run_id=run_id,
            pareto_rank=rank,
            extra_metrics={"feasible": True, "pareto_evaluations": evaluator.n_evaluated},
        )
        solutions.append(sol)
        run.points.append(models.ParetoPoint(
            solution_id=sol.id, rank=rank,
            objectives={name: sol.objectives[name] for name in names}))

    return run, solutions
