"""
Report generation.

Produces a self-contained Markdown report for a problem instance, optionally
including one or more solutions, a comparison table and a sensitivity summary.
The report is stored alongside the instance (see :mod:`storage`) so it survives
as a historical artefact and can be viewed from the "report" page.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from . import models, storage


# Canonical objective order is shared with objectives.CANONICAL_OBJECTIVES;
# reports label the two trade-off dimensions explicitly.
_OBJECTIVE_LABELS = {
    "makespan": "完工时间",
    "total_completion": "总完工时间",
    "weighted_completion": "加权完工时间",
    "tardiness": "拖期",
    "cost": "资源成本",
    "overtime_cost": "加班成本",
}


def _objective_label(name: str) -> str:
    return _OBJECTIVE_LABELS.get(name, name)


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _optimized_summary(sol: models.Solution) -> str:
    """Human-readable statement of what the run that produced ``sol``
    actually optimised, with the solution's values in those dimensions."""
    names = sol.optimized_objectives or []
    if not names:
        return ""
    parts = []
    for name in names:
        val = (sol.objectives or {}).get(name)
        parts.append(f"{_objective_label(name)}={_fmt(val)}")
    return " · ".join(parts)


def solution_markdown(sol: models.Solution) -> str:
    lines = [
        f"### 方案 {sol.id}",
        "",
        f"- **求解器**: `{sol.solver}`  ·  **状态**: {sol.status}  ·  "
        f"**目标值**: {_fmt(sol.objective_value)}  ·  **完工时间**: {_fmt(sol.makespan)}",
    ]
    opt = _optimized_summary(sol)
    if opt:
        tag = "（多目标前沿方案）" if sol.pareto_run_id else "（本次优化口径）"
        lines.append(f"- **优化目标**{tag}: {opt}")
    if sol.pareto_run_id:
        lines.append(f"- **所属前沿**: `{sol.pareto_run_id}`  ·  "
                     f"**前沿序号**: {sol.pareto_rank}")
    lines.append(
        f"- **耗时**: {_fmt(sol.solve_time)} s  ·  "
        f"**下界**: {_fmt(sol.lower_bound)}")
    m = sol.metrics
    if m:
        lines.append(f"- **指标**: 完工时间={_fmt(m.get('makespan'))}, "
                     f"总拖期={_fmt(m.get('total_tardiness'))}, "
                     f"资源成本={_fmt(m.get('resource_cost'))}, "
                     f"加班成本={_fmt(m.get('overtime_cost'))}, "
                     f"违反数={_fmt(m.get('n_violations'))}")
    if sol.message:
        lines.append(f"- **说明**: {sol.message}")
    if sol.assignments:
        lines.append("")
        lines.append("| 任务 | 开始 | 结束 | 资源 |")
        lines.append("|------|-------|-----|-----------|")
        for a in sorted(sol.assignments, key=lambda a: (a.start, a.task)):
            lines.append(f"| {a.task} | {a.start} | {a.end} | {', '.join(a.resources)} |")
    lines.append("")
    return "\n".join(lines)


def comparison_markdown(solutions: List[models.Solution]) -> str:
    # Build one column per objective dimension actually present, in canonical
    # order, so single-objective and Pareto solutions share one table and the
    # numbers are the stored canonical vector (not re-computed).
    dims: List[str] = []
    for s in solutions:
        for name in (s.optimized_objectives or []):
            if name not in dims:
                dims.append(name)
    dims.sort(key=lambda n: (list(_OBJECTIVE_LABELS).index(n)
                             if n in _OBJECTIVE_LABELS else 99))
    if not dims:
        dims = ["makespan"]

    best: Dict[str, float] = {}
    for s in solutions:
        for name in dims:
            val = (s.objectives or {}).get(name)
            if val is None:
                continue
            best[name] = val if name not in best else min(best[name], val)

    header = ["求解器", "状态"] + [_objective_label(n) for n in dims] + ["耗时(s)", "下界"]
    lines = ["## 方案对比", "", "| " + " | ".join(header) + " |",
             "|" + "|".join(["--------"] * len(header)) + "|"]
    for s in sorted(solutions, key=lambda s: (s.objective_value is None,
                                              s.objective_value or 0)):
        cells = [s.solver, s.status]
        for name in dims:
            val = (s.objectives or {}).get(name)
            text = _fmt(val)
            if val is not None and name in best and val == best[name]:
                text = f"**{text}** ★"
            cells.append(text)
        cells += [_fmt(s.solve_time), _fmt(s.lower_bound)]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def pareto_markdown(runs: Dict[str, models.ParetoRun]) -> str:
    """Render one section per multi-objective run referenced by the report's
    solutions: which objectives were jointly optimised and the front table."""
    if not runs:
        return ""
    blocks: List[str] = []
    for run in runs.values():
        labels = [_objective_label(n) for n in run.objectives]
        blocks.append("## 多目标前沿")
        blocks.append("")
        blocks.append(f"- **前沿运行**: `{run.id}`  ·  **求解器**: `{run.solver}`")
        blocks.append(f"- **同时优化的目标**: {', '.join(labels)}")
        blocks.append(f"- **前沿方案数**: {run.n_returned}"
                      + (f"（原始非支配点 {run.n_generated} 个，已降采样）"
                         if run.truncated else "")
                      + f"  ·  **评估排程数**: {run.n_evaluated}")
        blocks.append("")
        blocks.append("| 前沿序号 | " + " | ".join(labels) + " | 方案 ID |")
        blocks.append("|" + "|".join(["----------"] * (len(labels) + 2)) + "|")
        for p in run.points:
            vals = " | ".join(_fmt(p.objectives.get(n)) for n in run.objectives)
            blocks.append(f"| {p.rank} | {vals} | `{p.solution_id}` |")
        blocks.append("")
    return "\n".join(blocks)


def sensitivity_markdown(result: models.SensitivityResult) -> str:
    lines = [
        "## 敏感性分析",
        "",
        f"- 参数: `{result.parameter}`  ·  求解器 `{result.solver}`  ·  "
        f"基准目标值 {_fmt(result.base_objective)}",
        "",
        "| 变量取值 | 目标值 | 完工时间 | 目标值变化 | 状态 |",
        "|-----------|-----------|----------|-------------|--------|",
    ]
    for v in result.variations:
        lines.append(f"| {v.get('label')} | {_fmt(v.get('objective_value'))} | "
                     f"{_fmt(v.get('makespan'))} | {_fmt(v.get('delta'))} | {v.get('status')} |")
    lines.append("")
    return "\n".join(lines)


def generate_report(problem: models.Problem,
                    solutions: Optional[List[models.Solution]] = None,
                    sensitivity: Optional[models.SensitivityResult] = None,
                    title: Optional[str] = None,
                    persist: bool = True) -> models.Report:
    solutions = solutions or []
    lines = [
        f"# 排程报告：{problem.name or problem.id}",
        "",
        f"- **实例**: `{problem.id}`  ·  **版本**: {problem.version}",
        f"- **任务数**: {len(problem.tasks)}  ·  **资源数**: {len(problem.resources)}"
        f"  ·  **计划周期**: {problem.horizon} {problem.time_unit}",
        f"- **目标**: {models.objective_summary(problem)}",
        f"- **生成时间**: {models.now_iso()}",
        "",
        "## 资源",
        "",
        "| ID | 名称 | 类型 | 容量 | 单位成本 |",
        "|----|------|------|----------|-----------|",
    ]
    for r in problem.resources:
        lines.append(f"| {r.id} | {r.name} | {r.type} | {_fmt(r.capacity)} | {_fmt(r.cost_per_unit)} |")

    lines.append("")
    lines.append("## 任务")
    lines.append("")
    lines.append("| ID | 名称 | 工期 | 最早开始 | 截止 | 依赖 | 资源 |")
    lines.append("|----|------|----------|---------|-----|--------------|-----------|")
    for t in problem.tasks:
        deps = ",".join(t.dependencies) or "-"
        res = ",".join(f"{k}:{v}" for k, v in t.resource_requirements.items()) or "-"
        lines.append(f"| {t.id} | {t.name} | {t.duration} | {t.release_time} | "
                     f"{_fmt(t.due_date)} | {deps} | {res} |")

    lines.append("")
    # Collect the Pareto runs referenced by the selected solutions so the
    # report states the joint optimisation goals and lists each front once.
    pareto_runs: Dict[str, models.ParetoRun] = {}
    for sol in solutions:
        if sol.pareto_run_id and sol.pareto_run_id not in pareto_runs:
            run = storage.load_pareto_run(problem.id, sol.pareto_run_id)
            if run is not None:
                pareto_runs[run.id] = run
    if solutions:
        lines.append("## 求解方案")
        lines.append("")
        for sol in solutions:
            lines.append(solution_markdown(sol))
    if len(solutions) > 1:
        lines.append(comparison_markdown(solutions))
    if pareto_runs:
        lines.append(pareto_markdown(pareto_runs))
    if sensitivity is not None:
        lines.append(sensitivity_markdown(sensitivity))

    content = "\n".join(lines)
    report = models.Report(
        id=models.new_id("rep"),
        problem_id=problem.id,
        title=title or f"{problem.name} 报告",
        format="markdown",
        content=content,
    )
    if persist:
        storage.save_report(problem.id, report)
    return report
