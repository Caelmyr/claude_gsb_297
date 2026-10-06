"""
Serial Schedule Generation Scheme (SGS) decoder.

This is the workhorse that turns a *priority* representation into a concrete
schedule.  Meta-heuristics (genetic algorithm, simulated annealing) explore the
space of priority vectors / task orderings; the decoder deterministically maps
each one to a feasible schedule by scheduling each task as early as possible
while respecting:

* precedence constraints (explicit + hard ``precedence`` constraints),
* per-resource capacity over the task's whole execution window,
* resource availability calendars,
* task release times and hard ``time_window`` / ``fixed_start`` constraints.

The decoder also implements a *greedy* priority rule (highest priority weight,
then earliest release, then most successors) so that a deterministic baseline
solver is available for free.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .. import models


def topological_order(problem: models.Problem,
                      priorities: Optional[Dict[str, float]] = None) -> List[str]:
    """Return a precedence-feasible task order.  When ``priorities`` is given the
    available task with the *highest* priority is chosen first (random-key style
    decoding); otherwise tasks are ordered by the problem's own priority field,
    then by a stable heuristic (most successors / shortest duration)."""
    tasks = problem.task_map()
    succ = problem.successors()
    edges = problem.precedence_edges()
    indeg = {t: 0 for t in tasks}
    for b, a in edges:
        if b in indeg and a in indeg:
            indeg[a] += 1

    order: List[str] = []
    while len(order) < len(tasks):
        available = [t for t in tasks if indeg[t] == 0 and t not in order]
        if not available:
            # cycle: fall back to any unscheduled task
            available = [t for t in tasks if t not in order]

        def key(t: str) -> Tuple[float, float, float, int, str]:
            if priorities is not None:
                p = priorities.get(t, 0.0)
            else:
                p = tasks[t].priority
            # primary: priority (higher first -> negate)
            return (
                -p,
                -len(succ.get(t, [])),
                tasks[t].release_time,
                tasks[t].duration,
                t,
            )

        chosen = min(available, key=key)
        order.append(chosen)
        for s in succ.get(chosen, []):
            indeg[s] -= 1
    return order


def _resource_usage_at(task: models.Task, resource_id: str) -> float:
    return task.resource_requirements.get(resource_id, 0.0)


def _overtime_window_cost(problem: models.Problem, task: models.Task,
                          start: int) -> float:
    """Overtime charge the task would incur if executed at ``start``.  Only
    resources that declare a regular shift can generate overtime."""
    rmap = problem.resource_map()
    charge = 0.0
    for res_id, amount in task.resource_requirements.items():
        resource = rmap.get(res_id)
        if resource is None or resource.regular_hours is None:
            continue
        for slot in range(start, start + task.duration):
            if resource.available_at(slot) and not resource.regular_at(slot):
                charge += amount * resource.overtime_cost_per_unit
    return charge


def _feasible_start(problem: models.Problem, task_id: str,
                    starts: Dict[str, int],
                    overtime_weight: float = 0.0) -> Optional[int]:
    """Earliest feasible start time for ``task_id`` given already-placed tasks.

    Returns None if no start time within the horizon exists (e.g. an
    availability window makes the task unschedulable).

    ``overtime_weight`` (>= 0) trades earliness against overtime in start
    selection: the chosen start minimises ``t + weight * overtime_charge``.
    At 0.0 this is the plain earliest-feasible (ASAP) rule -- exactly the
    behaviour every existing solver relies on; larger weights push the task
    into regular-shift slots even if that delays completion, which is what
    lets the multi-objective search surface makespan/overtime trade-offs."""
    task = problem.task_map()[task_id]
    d = task.duration
    rmap = problem.resource_map()

    # earliest bound: release time + precedence completion
    earliest = task.release_time
    for dep in task.dependencies:
        if dep in starts:
            earliest = max(earliest, starts[dep] + problem.task_map()[dep].duration)
    # hard precedence constraints referencing this task as "after"
    for c in problem.hard_constraints:
        if c.type == "precedence" and c.params.get("after") == task_id:
            before = c.params.get("before")
            if before in starts:
                earliest = max(earliest, starts[before] + problem.task_map()[before].duration)

    # time_window / fixed_start hard constraints
    for c in problem.hard_constraints:
        p = c.params
        if p.get("task") != task_id:
            continue
        if c.type == "time_window":
            if p.get("release") is not None:
                earliest = max(earliest, p["release"])
        elif c.type == "fixed_start":
            s = p.get("start")
            if s is not None:
                return s if s >= earliest and s + d <= problem.horizon else None

    horizon = problem.horizon
    best_t: Optional[int] = None
    best_key: Optional[Tuple[float, int]] = None
    # Precompute the set of candidate starts: placement can only change at
    # resource-calendar boundaries (regular/overtime transitions) or at prior
    # task completions.  Restricting the scan keeps the overtime-aware search
    # O(n^2 * |events|) instead of O(n^2 * horizon).
    candidates = range(earliest, horizon - d + 1)
    if overtime_weight > 0.0 and task.resource_requirements:
        events = {earliest, horizon - d}
        for res_id in task.resource_requirements:
            resource = rmap.get(res_id)
            if resource is None:
                continue
            for interval in (resource.availability or []):
                events.update(interval)
            for interval in (resource.regular_hours or []):
                events.update(interval)
        for other_id, s_other in starts.items():
            events.add(s_other)
            events.add(s_other + problem.task_map()[other_id].duration)
        candidates = sorted(t for t in events if earliest <= t <= horizon - d)
    for t in candidates:
        if _window_ok(problem, task, t, starts, rmap):
            # fixed window deadline check
            if not _window_deadline_ok(problem, task_id, t):
                continue
            key = (t + overtime_weight * _overtime_window_cost(problem, task, t), t)
            if best_key is None or key < best_key:
                best_key = key
                best_t = t
                if overtime_weight == 0.0:
                    break  # earliest-feasible ASAP rule
    return best_t


def _effective_capacity(problem: models.Problem, resource_id: str,
                        default: float) -> float:
    """Hard ``resource_capacity`` constraints override a resource's capacity."""
    for c in problem.hard_constraints:
        if c.type == "resource_capacity" and c.params.get("resource") == resource_id:
            return float(c.params.get("capacity", default))
    return default


def _max_concurrent_ok(problem: models.Problem, task_id: str, t: int,
                       starts: Dict[str, int],
                       concurrent_profile: Dict[int, int] | None = None) -> bool:
    """Enforce global ``max_concurrent`` hard constraints for a candidate start.

    ``concurrent_profile`` maps slot -> number of already-placed active tasks;
    when given the check is O(duration) instead of scanning every placed task.
    """
    d = problem.task_map()[task_id].duration
    for c in problem.hard_constraints:
        if c.type != "max_concurrent":
            continue
        limit = int(c.params.get("limit", 1))
        for slot in range(t, t + d):
            active = 1 + (concurrent_profile.get(slot, 0)
                          if concurrent_profile is not None
                          else sum(1 for other_id, s in starts.items()
                                   if s <= slot < s + problem.task_map()[other_id].duration))
            if active > limit:
                return False
    return True


def _non_overlap_ok(problem: models.Problem, task_id: str, t: int,
                    starts: Dict[str, int]) -> bool:
    """Enforce ``non_overlap`` hard constraints for a candidate start."""
    d = problem.task_map()[task_id].duration
    for c in problem.hard_constraints:
        if c.type != "non_overlap":
            continue
        group = c.params.get("tasks", [])
        if task_id not in group:
            continue
        for other_id in group:
            if other_id == task_id or other_id not in starts:
                continue
            s = starts[other_id]
            od = problem.task_map()[other_id].duration
            if t < s + od and s < t + d:
                return False
    return True


def _window_ok_fast(problem: models.Problem, task: models.Task, t: int,
                    rmap: Dict[str, models.Resource],
                    usage: Dict[str, Dict[int, float]],
                    concurrent_profile: Dict[int, int]) -> bool:
    """O(duration * resources) feasibility against an incremental load
    profile: availability calendars, per-resource capacity and global
    max-concurrent / non-overlap hard constraints."""
    d = task.duration
    for res_id, req in task.resource_requirements.items():
        resource = rmap.get(res_id)
        if resource is None:
            continue
        profile = usage.get(res_id, {})
        cap = _effective_capacity(problem, res_id, resource.capacity)
        for slot in range(t, t + d):
            if not resource.available_at(slot):
                return False
            if profile.get(slot, 0.0) + req > cap + 1e-9:
                return False
    if not _max_concurrent_ok(problem, task.id, t, {}, concurrent_profile):
        return False
    if not _non_overlap_ok(problem, task.id, t, {}):
        return False
    return True


def _window_ok(problem: models.Problem, task: models.Task, t: int,
               starts: Dict[str, int], rmap: Dict[str, models.Resource]) -> bool:
    """Check resource capacity + availability over [t, t+duration)."""
    d = task.duration
    # resource availability for the task's own resource assignments
    for res_id in task.resource_requirements:
        if res_id not in rmap:
            continue
        res = rmap[res_id]
        for slot in range(t, t + d):
            if not res.available_at(slot):
                return False

    # capacity: sum requirements of overlapping placed tasks + this task
    active_res = set(task.resource_requirements)
    for res_id in active_res:
        res = rmap[res_id]
        cap = _effective_capacity(problem, res_id, res.capacity)
        for slot in range(t, t + d):
            used = task.resource_requirements.get(res_id, 0.0)
            for other_id, s in starts.items():
                other = problem.task_map()[other_id]
                if s <= slot < s + other.duration:
                    used += other.resource_requirements.get(res_id, 0.0)
            if used > cap + 1e-9:
                return False

    # global hard constraints (max concurrent, non-overlap)
    if not _max_concurrent_ok(problem, task.id, t, starts):
        return False
    if not _non_overlap_ok(problem, task.id, t, starts):
        return False
    return True


def _window_deadline_ok(problem: models.Problem, task_id: str, t: int) -> bool:
    task = problem.task_map()[task_id]
    for c in problem.hard_constraints:
        if c.type == "time_window" and c.params.get("task") == task_id:
            deadline = c.params.get("deadline")
            if deadline is not None and t + task.duration > deadline:
                return False
    return True


def decode(problem: models.Problem,
           priorities: Optional[Dict[str, float]] = None,
           seed: Optional[int] = None,
           overtime_weight: float = 0.0) -> Dict[str, int]:
    """Decode priorities into a schedule (task id -> start time).

    Tasks that cannot be placed within the horizon are omitted; the caller can
    detect infeasibility from the returned dict's length.  ``overtime_weight``
    trades earliness against overtime in start selection (0.0 == ASAP, the
    default every single-objective solver uses).

    The scan maintains incremental per-resource load profiles, a global
    concurrency profile and a per-``non_overlap``-group occupancy profile, so
    each feasibility probe is O(duration * resources) and the whole decode is
    near O(n * horizon) instead of O(n^2 * horizon)."""
    order = topological_order(problem, priorities)
    tmap = problem.task_map()
    rmap = problem.resource_map()
    horizon = problem.horizon

    usage: Dict[str, Dict[int, float]] = {r.id: {} for r in problem.resources}
    concurrent: Dict[int, int] = {}
    # group id -> slot -> number of occupying group members already placed
    group_profiles: Dict[str, Dict[str, Dict[int, int]]] = {}
    for c in problem.hard_constraints:
        if c.type == "non_overlap":
            group_profiles[c.id] = {tid: {} for tid in c.params.get("tasks", [])}
    groups_of: Dict[str, List[Tuple[str, int]]] = {}
    for cid, members in group_profiles.items():
        for tid in members:
            groups_of.setdefault(tid, []).append((cid, 1))

    starts: Dict[str, int] = {}

    def precedence_lower_bound(task_id: str) -> int:
        task = tmap[task_id]
        earliest = task.release_time
        for dep in task.dependencies:
            if dep in starts:
                earliest = max(earliest, starts[dep] + tmap[dep].duration)
        for c in problem.hard_constraints:
            if c.type == "precedence" and c.params.get("after") == task_id:
                before = c.params.get("before")
                if before in starts:
                    earliest = max(earliest, starts[before] + tmap[before].duration)
        return earliest

    def fixed_override(task_id: str) -> Optional[int]:
        for c in problem.hard_constraints:
            p = c.params
            if c.type == "fixed_start" and p.get("task") == task_id:
                s = p.get("start")
                if s is not None:
                    return int(s)
        return None

    def window_lower_bound(task_id: str) -> int:
        lo = 0
        for c in problem.hard_constraints:
            p = c.params
            if c.type == "time_window" and p.get("task") == task_id \
                    and p.get("release") is not None:
                lo = max(lo, int(p["release"]))
        return lo

    def deadline_ok(task_id: str, t: int, d: int) -> bool:
        for c in problem.hard_constraints:
            p = c.params
            if c.type == "time_window" and p.get("task") == task_id \
                    and p.get("deadline") is not None \
                    and t + d > int(p["deadline"]):
                return False
        return True

    def feasible(task: models.Task, t: int) -> bool:
        d = task.duration
        for res_id, req in task.resource_requirements.items():
            resource = rmap.get(res_id)
            if resource is None:
                continue
            profile = usage[res_id]
            cap = _effective_capacity(problem, res_id, resource.capacity)
            for slot in range(t, t + d):
                if not resource.available_at(slot):
                    return False
                if profile.get(slot, 0.0) + req > cap + 1e-9:
                    return False
        for c in problem.hard_constraints:
            if c.type == "max_concurrent":
                limit = int(c.params.get("limit", 1))
                for slot in range(t, t + d):
                    if 1 + concurrent.get(slot, 0) > limit:
                        return False
        for cid, _ in groups_of.get(task.id, ()):
            prof = group_profiles[cid]
            for other_id, other_prof in prof.items():
                if other_id == task.id:
                    continue
                for slot in range(t, t + d):
                    if other_prof.get(slot, 0) > 0:
                        return False
        return True

    def commit(task: models.Task, t: int) -> None:
        d = task.duration
        for res_id, req in task.resource_requirements.items():
            profile = usage.setdefault(res_id, {})
            for slot in range(t, t + d):
                profile[slot] = profile.get(slot, 0.0) + req
        for slot in range(t, t + d):
            concurrent[slot] = concurrent.get(slot, 0) + 1
        for cid, _ in groups_of.get(task.id, ()):
            prof = group_profiles[cid].setdefault(task.id, {})
            for slot in range(t, t + d):
                prof[slot] = 1

    for task_id in order:
        task = tmap[task_id]
        d = task.duration
        earliest = max(precedence_lower_bound(task_id), window_lower_bound(task_id))
        fixed = fixed_override(task_id)
        if fixed is not None:
            if fixed >= earliest and fixed + d <= horizon and feasible(task, fixed):
                starts[task_id] = fixed
                commit(task, fixed)
            continue

        # candidate starts: every slot for ASAP; calendar / completion-event
        # boundaries only when trading overtime (between events the overtime
        # charge and the resource load are constant, so nothing in between can
        # improve the selection key).
        upper = horizon - d
        if overtime_weight > 0.0 and task.resource_requirements:
            events = {earliest, upper}
            for res_id in task.resource_requirements:
                resource = rmap.get(res_id)
                if resource is None:
                    continue
                for interval in (resource.availability or []):
                    events.update(interval)
                for interval in (resource.regular_hours or []):
                    events.update(interval)
            for other_id, s in starts.items():
                events.add(s)
                events.add(s + tmap[other_id].duration)
            candidates = (t for t in sorted(events) if earliest <= t <= upper)
        else:
            candidates = range(earliest, upper + 1)

        best_t: Optional[int] = None
        best_key: Optional[Tuple[float, int]] = None
        for t in candidates:
            if not feasible(task, t) or not deadline_ok(task_id, t, d):
                continue
            key = (t + overtime_weight * _overtime_window_cost(problem, task, t), t)
            if best_key is None or key < best_key:
                best_key, best_t = key, t
                if overtime_weight == 0.0:
                    break
        if best_t is not None:
            starts[task_id] = best_t
            commit(task, best_t)
    return starts


def max_overtime_weight(problem: models.Problem) -> float:
    """Decoder weight at which avoiding ONE unit of overtime charge beats
    delaying a task by up to a whole horizon (the zero-overtime anchor).
    The multi-objective engine maps its [0, 1] tolerance gene onto
    ``beta * this value`` to sweep the speed<->overtime spectrum."""
    max_rate = max((r.overtime_cost_per_unit
                    for r in problem.resources
                    if r.regular_hours is not None), default=0.0)
    if max_rate <= 0:
        return 0.0
    return float(max(1, problem.horizon))


def random_keys(problem: models.Problem, rng: random.Random) -> Dict[str, float]:
    """Random priority vector (each task gets a uniform random key)."""
    return {t.id: rng.random() for t in problem.tasks}


def greedy_order(problem: models.Problem) -> Dict[str, float]:
    """Deterministic priority rule: highest problem.priority first."""
    return {t.id: float(t.priority) for t in problem.tasks}


def schedule_to_assignments(problem: models.Problem,
                            starts: Dict[str, int]) -> List[models.Assignment]:
    tmap = problem.task_map()
    out = []
    for tid in sorted(starts, key=lambda t: (starts[t], t)):
        task = tmap[tid]
        out.append(models.Assignment(
            task=tid,
            start=starts[tid],
            end=starts[tid] + task.duration,
            resources=sorted(task.resource_requirements),
        ))
    return out
