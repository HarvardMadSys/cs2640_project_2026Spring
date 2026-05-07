"""Plots and tables.

Three primary figures:
  1. Cliff plot: per-step uncached tokens for one trajectory, with
     compaction events marked. Optionally overlaid for several policies
     on the same task.
  2. Lifetime cost bars: $ per resolved instance, grouped by policy,
     faceted by model price column.
  3. Pareto: cost vs resolve rate scatter, one point per (policy, seed).

All plots are written as PNG + a matching CSV of underlying numbers.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from .pricing import PriceSheet, cost_of, cost_per_resolved
from .replay import TrajectoryCliffReport, analyze_trajectory
from .types import Trajectory


# ---------------------------------------------------------------------------
# Cliff plot
# ---------------------------------------------------------------------------

def plot_cliff(
    reports: List[TrajectoryCliffReport],
    out_path: Path,
    title: str = "Prefix-cache cliffs around compaction events",
):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = plt.cm.tab10.colors

    for i, r in enumerate(reports):
        steps = [pt.step for pt in r.series]
        unc = [pt.uncached_block_estimate for pt in r.series]
        c = colors[i % len(colors)]
        ax.plot(steps, unc, "-o", markersize=3, color=c,
                label=f"{r.policy}/{r.task_id[:24]}", alpha=0.8)
        for ev in r.compactions:
            ax.axvline(ev.step, color=c, linestyle=":", alpha=0.5)

    ax.set_xlabel("Step")
    ax.set_ylabel("Uncached prefix-cache blocks (lower = better)")
    ax.set_title(title)
    ax.legend(fontsize=7, loc="upper left", ncol=2)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)

    # CSV companion
    with open(out_path.with_suffix(".csv"), "w") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "policy", "step", "n_messages",
                    "shared_blocks", "curr_blocks", "uncached_blocks",
                    "is_compaction"])
        for r in reports:
            comp_steps = {ev.step for ev in r.compactions}
            for pt in r.series:
                w.writerow([r.task_id, r.policy, pt.step, pt.n_messages,
                            pt.shared_blocks, pt.curr_blocks,
                            pt.uncached_block_estimate,
                            int(pt.step in comp_steps)])


# ---------------------------------------------------------------------------
# Lifetime cost bars
# ---------------------------------------------------------------------------

def plot_lifetime_cost(
    trajs: List[Trajectory],
    sheet: PriceSheet,
    out_path: Path,
    *,
    cost_models: Optional[List[str]] = None,
    title: str = "Lifetime $ per resolved instance",
):
    """One bar group per cost-model (price column), bars by policy.

    cost_models = list of model-name keys in pricing.yaml against which to
    re-cost the trajectories. None = use each trajectory's own model.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if cost_models is None:
        cost_models = sorted(set(t.model for t in trajs))

    policies = sorted(set(t.policy for t in trajs))

    # cost_grid[policy_idx][model_idx] = $/resolved
    grid = np.zeros((len(policies), len(cost_models)))
    for j, cm in enumerate(cost_models):
        for i, pol in enumerate(policies):
            sub = [t for t in trajs if t.policy == pol]
            grid[i, j] = cost_per_resolved(sub, sheet, override_model=cm)

    fig, ax = plt.subplots(figsize=(max(8, 1.5 * len(cost_models)), 5))
    bar_width = 0.8 / max(len(policies), 1)
    x = np.arange(len(cost_models))

    for i, pol in enumerate(policies):
        ax.bar(x + i * bar_width, grid[i], bar_width, label=pol)

    ax.set_xticks(x + bar_width * (len(policies) - 1) / 2)
    ax.set_xticklabels([cm.split("/")[-1] for cm in cost_models], rotation=20, ha="right")
    ax.set_ylabel("$ per resolved instance")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)

    with open(out_path.with_suffix(".csv"), "w") as f:
        w = csv.writer(f)
        w.writerow(["policy", *cost_models])
        for i, pol in enumerate(policies):
            w.writerow([pol, *[f"{grid[i, j]:.6f}" for j in range(len(cost_models))]])


# ---------------------------------------------------------------------------
# Pareto: cost vs resolve rate
# ---------------------------------------------------------------------------

def plot_pareto(
    trajs: List[Trajectory],
    sheet: PriceSheet,
    out_path: Path,
    *,
    cost_model: Optional[str] = None,
    title: str = "Cost vs resolve rate (Pareto)",
):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    by_policy: Dict[str, list] = {}
    for t in trajs:
        by_policy.setdefault(t.policy, []).append(t)

    fig, ax = plt.subplots(figsize=(7, 5))
    colors = plt.cm.tab10.colors
    rows = []
    for i, (pol, ts) in enumerate(by_policy.items()):
        if not ts:
            continue
        n = len(ts)
        n_resolved = sum(1 for t in ts if t.resolved)
        resolve_rate = n_resolved / n
        total_cost = sum(cost_of(t, sheet, override_model=cost_model).total for t in ts)
        cost_per_task = total_cost / max(n, 1)
        ax.scatter(resolve_rate, cost_per_task, s=120, color=colors[i % len(colors)],
                   label=pol, edgecolors="black", linewidths=0.5)
        ax.annotate(pol, (resolve_rate, cost_per_task),
                    xytext=(8, 4), textcoords="offset points", fontsize=8)
        rows.append([pol, n, n_resolved, resolve_rate, cost_per_task])

    ax.set_xlabel("Resolve rate")
    ax.set_ylabel("Mean $ per task")
    ax.set_title(title + (f" — costed as {cost_model}" if cost_model else ""))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)

    with open(out_path.with_suffix(".csv"), "w") as f:
        w = csv.writer(f)
        w.writerow(["policy", "n", "n_resolved", "resolve_rate", "mean_cost_per_task"])
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def write_summary_table(
    trajs: List[Trajectory],
    sheet: PriceSheet,
    out_path: Path,
    *,
    cost_models: Optional[List[str]] = None,
):
    """One row per (benchmark, model, policy) with cost decomposition."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if cost_models is None:
        cost_models = sorted(set(t.model for t in trajs))

    cells = {}
    for t in trajs:
        key = (t.benchmark, t.model, t.policy)
        cells.setdefault(key, []).append(t)

    headers = [
        "benchmark", "model", "policy", "n_tasks", "n_resolved",
        "n_compactions_total", "mean_steps", "mean_prompt_tokens",
        "mean_cached_tokens", "mean_cliff_blocks",
    ]
    for cm in cost_models:
        headers.append(f"cost_per_resolved__{cm}")

    with open(out_path, "w") as f:
        w = csv.writer(f)
        w.writerow(headers)

        for (bench, model, pol), ts in sorted(cells.items()):
            n = len(ts)
            n_res = sum(1 for t in ts if t.resolved)
            n_comp = sum(t.num_compactions for t in ts)
            mean_steps = sum(len(t.steps) for t in ts) / max(n, 1)
            mean_pt = sum(t.total_prompt_tokens for t in ts) / max(n, 1)
            mean_cached = sum(t.total_cached_tokens for t in ts) / max(n, 1)

            cliffs = []
            for t in ts:
                td = t.to_dict()
                td["task_id"] = t.task_id
                td["model"] = t.model
                td["policy"] = t.policy
                td["source_file"] = "(in-memory)"
                td["step_messages"] = [s["messages_in"] for s in td["steps"]]
                rep = analyze_trajectory(td)
                for ev in rep.compactions:
                    cliffs.append(ev.cliff_ratio_blocks)
            mean_cliff = (sum(cliffs) / len(cliffs)) if cliffs else 0.0

            row = [bench, model, pol, n, n_res, n_comp, f"{mean_steps:.2f}",
                   f"{mean_pt:.0f}", f"{mean_cached:.0f}", f"{mean_cliff:.3f}"]
            for cm in cost_models:
                row.append(f"{cost_per_resolved(ts, sheet, override_model=cm):.6f}")
            w.writerow(row)
