"""Cliff + lifetime-cost analysis using the REAL cache_read_tokens recorded
in results/exp_haiku_5/*.traj.json files.

These trajectories were captured against Anthropic Claude Haiku, which
reports cache_read_input_tokens per request — so we don't need to estimate
the cliff from byte-prefix matching. We can read it straight off the
Anthropic API's usage block (already saved as `cache_read_tokens` in
cache_trace).

Output: SVG/PNG figures + a CSV summary in studies/lifetime_cost/out/recorded_cache/.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


# Anthropic Haiku 4.5 prices ($/MTok). Sourced from pricing.yaml / Anthropic docs.
PRICE_INPUT_UNCACHED = 1.00
PRICE_INPUT_CACHED   = 0.10
PRICE_OUTPUT         = 5.00
PRICE_CACHE_WRITE    = 1.25


@dataclass
class StepRow:
    step: int
    prompt_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    completion_tokens: int
    messages_evicted: int
    cost: float
    hit_rate: float

    @property
    def uncached(self) -> int:
        return max(self.prompt_tokens - self.cache_read_tokens, 0)


@dataclass
class TrajRecord:
    path: Path
    instance_id: str
    policy: str
    budget: int
    n_messages: int
    steps: List[StepRow]


def load_haiku_trajectories(root: Path) -> List[TrajRecord]:
    out: List[TrajRecord] = []
    for p in sorted(root.rglob("*.traj.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(d, dict) or "cache_trace" not in d:
            continue
        ct = d["cache_trace"] or []
        if not ct:
            continue
        steps = [
            StepRow(
                step=int(r["step"]),
                prompt_tokens=int(r.get("prompt_tokens", 0) or 0),
                cache_read_tokens=int(r.get("cache_read_tokens", 0) or 0),
                cache_creation_tokens=int(r.get("cache_creation_tokens", 0) or 0),
                completion_tokens=int(r.get("completion_tokens", 0) or 0),
                messages_evicted=int(r.get("messages_evicted", 0) or 0),
                cost=float(r.get("cost", 0.0) or 0.0),
                hit_rate=float(r.get("cache_hit_rate", 0.0) or 0.0),
            )
            for r in ct
        ]
        out.append(TrajRecord(
            path=p,
            instance_id=d.get("instance_id", p.stem),
            policy=d.get("cache_policy", "unknown"),
            budget=int(d.get("cache_budget", 0)),
            n_messages=len(d.get("messages", [])),
            steps=steps,
        ))
    return out


def detect_eviction_events(t: TrajRecord) -> List[int]:
    """Step indices where messages_evicted increased vs the previous step.
    These are the events at which a cliff *should* fire."""
    events = []
    prev = 0
    for s in t.steps:
        if s.messages_evicted > prev:
            events.append(s.step)
        prev = s.messages_evicted
    return events


def cliff_ratios(t: TrajRecord, *, episode_first_only: bool = True) -> List[tuple[int, float, int, int]]:
    """Cliff ratio = uncached(k) / max(uncached at last warm step before k, 1).

    A "warm step" = hit_rate > 0.5. This sidesteps the artifact where many
    consecutive eviction steps each compare against another zero-cache step
    and therefore look like cliff ≈ 1.

    `episode_first_only`: if True, only the FIRST step of each contiguous
    eviction run is reported. This is what we want for the headline plot —
    one cliff per compaction episode, not one per evicted message.
    """
    out = []
    by_step = {s.step: s for s in t.steps}
    events = detect_eviction_events(t)
    if episode_first_only:
        # Keep only the start of each contiguous run
        kept = []
        for k in events:
            if not kept or k != kept[-1] + 1:
                kept.append(k)
        events = kept

    for k in events:
        # Find the most recent warm step (hit_rate > 0.5) before k
        warm_unc = None
        for j in range(k - 1, 0, -1):
            s = by_step.get(j)
            if s is None:
                continue
            if s.hit_rate > 0.5:
                warm_unc = max(s.uncached, 1)
                break
        curr = by_step.get(k)
        if curr is None or warm_unc is None:
            continue
        out.append((k, curr.uncached / warm_unc, warm_unc, curr.uncached))
    return out


def lifetime_cost_recorded(t: TrajRecord) -> float:
    """Use the recorded `cost` field (Anthropic's billed cost)."""
    return sum(s.cost for s in t.steps)


def lifetime_cost_recomputed(t: TrajRecord) -> dict:
    """Recompute lifetime $ from token counts and the canonical price sheet,
    so all policies are compared on identical math."""
    M = 1_000_000
    uncached = sum(s.uncached for s in t.steps)
    cached = sum(s.cache_read_tokens for s in t.steps)
    output = sum(s.completion_tokens for s in t.steps)
    cache_write = sum(s.cache_creation_tokens for s in t.steps)
    return {
        "uncached_dollars": uncached * PRICE_INPUT_UNCACHED / M,
        "cached_dollars":   cached   * PRICE_INPUT_CACHED   / M,
        "output_dollars":   output   * PRICE_OUTPUT         / M,
        "cache_write_dollars": cache_write * PRICE_CACHE_WRITE / M,
        "total": (uncached * PRICE_INPUT_UNCACHED
                  + cached * PRICE_INPUT_CACHED
                  + output * PRICE_OUTPUT
                  + cache_write * PRICE_CACHE_WRITE) / M,
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_cliff_per_trajectory(records: List[TrajRecord], out_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_inst: Dict[str, List[TrajRecord]] = defaultdict(list)
    for t in records:
        by_inst[t.instance_id].append(t)

    out_dir.mkdir(parents=True, exist_ok=True)

    for inst, trajs in by_inst.items():
        # Two-panel: top = cache hit rate, bottom = uncached tokens
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        colors = {"none": "#1f77b4", "fifo": "#d62728", "adaptive": "#2ca02c",
                  "summarize": "#9467bd", "kv_adaptive": "#ff7f0e"}
        for t in sorted(trajs, key=lambda x: x.policy):
            xs = [s.step for s in t.steps]
            hits = [s.hit_rate for s in t.steps]
            unc = [s.uncached for s in t.steps]
            c = colors.get(t.policy, "gray")
            ax1.plot(xs, hits, "-o", markersize=3, color=c, label=f"{t.policy}", alpha=0.85)
            ax2.plot(xs, unc, "-o", markersize=3, color=c, label=f"{t.policy}", alpha=0.85)

            # Mark eviction events
            for ev in detect_eviction_events(t):
                ax1.axvline(ev, color=c, linestyle=":", alpha=0.25)
                ax2.axvline(ev, color=c, linestyle=":", alpha=0.25)

        ax1.set_ylabel("Cache hit rate (recorded)")
        ax1.set_title(f"{inst} — Anthropic Haiku, real cache_read_tokens")
        ax1.set_ylim(-0.02, 1.02)
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=8, loc="lower right")

        ax2.set_ylabel("Uncached prompt tokens")
        ax2.set_xlabel("Step")
        ax2.grid(True, alpha=0.3)
        ax2.legend(fontsize=8, loc="upper left")

        fig.tight_layout()
        out_path = out_dir / f"cliff_{inst}.png"
        fig.savefig(out_path, dpi=140)
        plt.close(fig)


def plot_aggregate_cliffs(records: List[TrajRecord], out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_policy: Dict[str, list] = defaultdict(list)
    for t in records:
        for (_step, ratio, _b, _a) in cliff_ratios(t):
            by_policy[t.policy].append(ratio)

    if not by_policy:
        print("  no cliff events to plot")
        return

    policies = sorted(by_policy.keys())
    data = [by_policy[p] for p in policies]

    fig, ax = plt.subplots(figsize=(8, 5))
    parts = ax.boxplot(data, labels=policies, showfliers=True)
    ax.set_yscale("log")
    ax.set_ylabel("Cliff ratio = uncached(k+1) / uncached(k-1)\n(log scale, 1.0 = no cliff)")
    ax.set_xlabel("Compaction policy")
    ax.set_title("Per-eviction cliff ratios — recorded Anthropic Haiku trajectories")
    ax.axhline(1.0, color="gray", linestyle="--", alpha=0.5, label="no cliff")
    ax.axhline(3.0, color="orange", linestyle="--", alpha=0.5, label="Decision Gate A threshold")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)

    # Annotate medians
    for i, vals in enumerate(data, 1):
        if vals:
            med = statistics.median(vals)
            ax.annotate(f"med={med:.2f}\nn={len(vals)}", (i, med),
                        textcoords="offset points", xytext=(20, 0),
                        fontsize=8, va="center")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_per_instance_bars(records: List[TrajRecord], out_path: Path):
    """The headline figure: $ per task, faceted by instance, grouped by policy.
    Apples-to-apples comparison."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    by_inst: Dict[str, Dict[str, float]] = defaultdict(dict)
    for t in records:
        by_inst[t.instance_id][t.policy] = lifetime_cost_recomputed(t)["total"]

    instances = sorted(by_inst.keys())
    policies = sorted({p for d in by_inst.values() for p in d.keys()})

    fig, ax = plt.subplots(figsize=(11, 5))
    bw = 0.8 / max(len(policies), 1)
    x = np.arange(len(instances))
    colors = {"none": "#1f77b4", "fifo": "#d62728", "adaptive": "#2ca02c",
              "summarize": "#9467bd", "kv_adaptive": "#ff7f0e"}

    for i, p in enumerate(policies):
        vals = [by_inst[inst].get(p, 0.0) for inst in instances]
        ax.bar(x + i * bw, vals, bw, label=p, color=colors.get(p, "gray"))

    ax.set_xticks(x + bw * (len(policies) - 1) / 2)
    ax.set_xticklabels([i.split("__")[1] for i in instances], rotation=20, ha="right")
    ax.set_ylabel("Lifetime $ per task (Anthropic Haiku 4.5 prices)")
    ax.set_title("Per-instance lifetime cost — recorded Haiku trajectories, 5 SWE-bench instances, 4 policies")
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_zero_cache_steps_fraction(records: List[TrajRecord], out_path: Path):
    """For each policy, what fraction of its steps had cache_read == 0?
    This complements the cliff metric — captures `summarize`'s real cost
    (every post-compaction step starts cold)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_policy: Dict[str, list] = defaultdict(list)
    for t in records:
        cold = sum(1 for s in t.steps if s.cache_read_tokens == 0)
        by_policy[t.policy].append(cold / max(len(t.steps), 1))

    policies = sorted(by_policy.keys())
    means = [statistics.mean(by_policy[p]) for p in policies]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(policies, means)
    ax.set_ylabel("Fraction of steps with cache_read = 0")
    ax.set_title("How often is the cache cold? (lower is better)")
    for b, v in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0%}",
                ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_lifetime_cost(records: List[TrajRecord], out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    by_policy: Dict[str, List[float]] = defaultdict(list)
    by_policy_decomp: Dict[str, dict] = defaultdict(lambda: {
        "uncached": 0.0, "cached": 0.0, "output": 0.0, "cache_write": 0.0,
    })
    for t in records:
        c = lifetime_cost_recomputed(t)
        by_policy[t.policy].append(c["total"])
        by_policy_decomp[t.policy]["uncached"] += c["uncached_dollars"]
        by_policy_decomp[t.policy]["cached"] += c["cached_dollars"]
        by_policy_decomp[t.policy]["output"] += c["output_dollars"]
        by_policy_decomp[t.policy]["cache_write"] += c["cache_write_dollars"]

    policies = sorted(by_policy.keys())
    means = [statistics.mean(by_policy[p]) for p in policies]
    n = [len(by_policy[p]) for p in policies]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: mean lifetime $/task
    bars = ax1.bar(policies, means, color=["#1f77b4", "#2ca02c", "#d62728", "#ff7f0e", "#9467bd"][:len(policies)])
    ax1.set_ylabel("Mean lifetime $ per task (Haiku 4.5 prices)")
    ax1.set_title("Lifetime cost by policy")
    for b, v, k in zip(bars, means, n):
        ax1.text(b.get_x() + b.get_width() / 2, v, f"${v:.4f}\nn={k}",
                 ha="center", va="bottom", fontsize=9)
    ax1.grid(True, axis="y", alpha=0.3)

    # Right: stacked decomposition
    components = ["uncached", "cached", "output", "cache_write"]
    bottoms = np.zeros(len(policies))
    for comp in components:
        vals = np.array([by_policy_decomp[p][comp] / max(len(by_policy[p]), 1) for p in policies])
        ax2.bar(policies, vals, bottom=bottoms, label=comp)
        bottoms += vals
    ax2.set_ylabel("Mean $ per task (stacked decomposition)")
    ax2.set_title("Cost breakdown by policy")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="results/exp_haiku_5")
    ap.add_argument("--out", default="studies/lifetime_cost/out/recorded_cache")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    records = load_haiku_trajectories(Path(args.root))
    print(f"Loaded {len(records)} trajectories from {args.root}")

    # Per-trajectory CSV
    csv_path = out / "trajectories.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["instance_id", "policy", "budget", "n_steps", "n_evictions",
                    "total_prompt_tokens", "total_cached_tokens",
                    "total_completion_tokens", "lifetime_cost", "median_cliff", "max_cliff"])
        for t in records:
            cliffs = [r for _s, r, _b, _a in cliff_ratios(t)]
            w.writerow([
                t.instance_id, t.policy, t.budget, len(t.steps),
                len(detect_eviction_events(t)),
                sum(s.prompt_tokens for s in t.steps),
                sum(s.cache_read_tokens for s in t.steps),
                sum(s.completion_tokens for s in t.steps),
                f"{lifetime_cost_recomputed(t)['total']:.6f}",
                f"{statistics.median(cliffs):.3f}" if cliffs else "",
                f"{max(cliffs):.3f}" if cliffs else "",
            ])
    print(f"Wrote {csv_path}")

    # Aggregate cliff stats
    all_cliffs_by_policy: Dict[str, List[float]] = defaultdict(list)
    for t in records:
        for (_step, ratio, _b, _a) in cliff_ratios(t):
            all_cliffs_by_policy[t.policy].append(ratio)
    all_cliffs = [r for rs in all_cliffs_by_policy.values() for r in rs]

    summary = {
        "n_trajectories": len(records),
        "n_with_eviction_events": sum(1 for t in records if detect_eviction_events(t)),
        "n_total_eviction_events": sum(len(detect_eviction_events(t)) for t in records),
        "median_cliff_overall": statistics.median(all_cliffs) if all_cliffs else None,
        "p90_cliff_overall": (sorted(all_cliffs)[int(0.9 * (len(all_cliffs) - 1))]
                              if all_cliffs else None),
        "by_policy": {
            p: {
                "n_events": len(rs),
                "median_cliff": statistics.median(rs) if rs else None,
                "mean_cliff": statistics.mean(rs) if rs else None,
                "p90_cliff": (sorted(rs)[int(0.9 * (len(rs) - 1))] if rs else None),
            }
            for p, rs in sorted(all_cliffs_by_policy.items())
        },
    }
    summary_path = out / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {summary_path}")

    # Plots
    plot_cliff_per_trajectory(records, out / "per_trajectory")
    plot_aggregate_cliffs(records, out / "cliff_distribution.png")
    plot_lifetime_cost(records, out / "lifetime_cost.png")
    plot_per_instance_bars(records, out / "lifetime_cost_per_instance.png")
    plot_zero_cache_steps_fraction(records, out / "cold_step_fraction.png")
    print(f"Plots under {out}")

    # Decision gate A
    median = summary["median_cliff_overall"]
    print(f"\n=== Decision Gate A: median cliff > 3x ===")
    if median is None:
        print("FAIL: no eviction events found")
    elif median > 3.0:
        print(f"PASS: median cliff = {median:.2f}x")
    else:
        print(f"FAIL: median cliff = {median:.2f}x (need > 3x)")

    # Per-policy summary
    print("\n=== Per-policy cliffs (real Anthropic cache_read_tokens) ===")
    for p, s in summary["by_policy"].items():
        print(f"  {p:15s} n_events={s['n_events']:3d}  "
              f"median={s['median_cliff'] or 0:6.2f}x  "
              f"p90={s['p90_cliff'] or 0:6.2f}x")


if __name__ == "__main__":
    main()
