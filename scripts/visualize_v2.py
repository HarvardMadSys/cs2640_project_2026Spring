#!/usr/bin/env python3
"""Visualize v2 experiment results.

Generates two paper figures from a v2_experiment_*.json file:
  1. hit_rate_over_steps.png  — line chart, all 3 policies, eviction marker
  2. before_after_eviction.png — bar chart, FIFO vs kv_adaptive

Usage:
    python scripts/visualize_v2.py results/v2_experiment_1775540387.json
    python scripts/visualize_v2.py results/v2_experiment_1775540387.json --out figures/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

COLORS = {
    "none": "#4e9af1",        # blue
    "kv_adaptive": "#3fb950", # green
    "fifo": "#f85149",        # red
}
LABELS = {
    "none": "No eviction",
    "kv_adaptive": "AdaptiveCache",
    "fifo": "FIFO",
}
LINESTYLES = {
    "none": "--",
    "kv_adaptive": "-",
    "fifo": "-.",
}


def _require_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        return plt, mpatches
    except ImportError:
        print("ERROR: matplotlib is required. Install with: pip install matplotlib", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Figure 1: Hit rate over steps
# ---------------------------------------------------------------------------

def plot_hit_rate_over_steps(results: dict, out_path: Path) -> None:
    plt, mpatches = _require_matplotlib()

    fig, ax = plt.subplots(figsize=(8, 4.5))

    first_eviction_step = None

    for policy, data in results.items():
        steps = data["steps"]
        xs = [s["step"] for s in steps]
        ys = [s["hit_rate"] * 100 for s in steps]

        ax.plot(
            xs, ys,
            label=LABELS.get(policy, policy),
            color=COLORS.get(policy, "#888"),
            linestyle=LINESTYLES.get(policy, "-"),
            linewidth=2.2,
            marker="o",
            markersize=5,
        )

        # Find first eviction step across any evicting policy
        for s in steps:
            if s["evicted"] > 0:
                if first_eviction_step is None or s["step"] < first_eviction_step:
                    first_eviction_step = s["step"]

    if first_eviction_step is not None:
        ax.axvline(
            x=first_eviction_step,
            color="#d29922",
            linestyle=":",
            linewidth=1.8,
            label=f"First eviction (step {first_eviction_step})",
        )

    ax.set_xlabel("Agent step", fontsize=12)
    ax.set_ylabel("Cache hit rate (%)", fontsize=12)
    ax.set_title("Prefix cache hit rate over conversation steps", fontsize=13, fontweight="bold")
    ax.set_ylim(-5, 105)
    ax.legend(fontsize=10, loc="lower right")
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 2: Before / after eviction bar chart
# ---------------------------------------------------------------------------

def plot_before_after_eviction(results: dict, out_path: Path) -> None:
    plt, mpatches = _require_matplotlib()

    # Find first eviction step (shared across all evicting policies)
    first_eviction = None
    for data in results.values():
        for s in data["steps"]:
            if s["evicted"] > 0:
                if first_eviction is None or s["step"] < first_eviction:
                    first_eviction = s["step"]

    if first_eviction is None:
        print("WARNING: no evictions found — skipping before/after chart")
        return

    policies = [p for p in results if p != "none"]

    before_means = {}
    after_means = {}
    for policy in policies:
        steps = results[policy]["steps"]
        before = [s["hit_rate"] for s in steps if s["step"] < first_eviction]
        after = [s["hit_rate"] for s in steps if s["step"] >= first_eviction]
        before_means[policy] = (sum(before) / len(before) * 100) if before else 0.0
        after_means[policy] = (sum(after) / len(after) * 100) if after else 0.0

    fig, ax = plt.subplots(figsize=(6, 4.5))

    n = len(policies)
    bar_width = 0.32
    x = range(n)

    before_bars = ax.bar(
        [i - bar_width / 2 for i in x],
        [before_means[p] for p in policies],
        width=bar_width,
        label="Before eviction",
        color=[COLORS.get(p, "#888") for p in policies],
        alpha=0.6,
    )
    after_bars = ax.bar(
        [i + bar_width / 2 for i in x],
        [after_means[p] for p in policies],
        width=bar_width,
        label="After eviction",
        color=[COLORS.get(p, "#888") for p in policies],
        alpha=1.0,
    )

    # Annotate bar values
    for bars in (before_bars, after_bars):
        for bar in bars:
            h = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + 1,
                f"{h:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
            )

    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS.get(p, p) for p in policies], fontsize=11)
    ax.set_ylabel("Cache hit rate (%)", fontsize=12)
    ax.set_title(
        f"Cache hit rate before vs. after first eviction (step {first_eviction})",
        fontsize=11,
        fontweight="bold",
    )
    ax.set_ylim(0, 115)

    # Custom legend: light = before, solid = after
    legend_patches = [
        mpatches.Patch(facecolor="#888", alpha=0.6, label="Before eviction"),
        mpatches.Patch(facecolor="#888", alpha=1.0, label="After eviction"),
    ]
    ax.legend(handles=legend_patches, fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"Saved: {out_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 3: Summary table printed to stdout
# ---------------------------------------------------------------------------

def print_summary(data: dict) -> None:
    results = data["results"]
    print(f"\n{'='*60}")
    print(f"Experiment summary  (budget={data['budget']}, steps={data['n_steps']})")
    print(f"{'='*60}")
    print(f"{'Policy':<14} {'Total Prompt':>13} {'Total Cached':>13} {'Mean Hit%':>10} {'Evictions':>10}")
    print(f"{'-'*60}")
    for policy, r in results.items():
        evictions = sum(s["evicted"] for s in r["steps"])
        print(
            f"{policy:<14} {r['total_prompt_tokens']:>13,} "
            f"{r['total_cached_tokens']:>13,} "
            f"{r['mean_hit_rate']:>9.1%}  {evictions:>9}"
        )
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Visualize v2 experiment results")
    parser.add_argument("json_file", help="Path to v2_experiment_*.json")
    parser.add_argument("--out", default=None, help="Output directory (default: same as json_file)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"ERROR: {json_path} not found", file=sys.stderr)
        sys.exit(1)

    data = json.loads(json_path.read_text())
    results = data["results"]

    out_dir = Path(args.out) if args.out else json_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = json_path.stem

    print_summary(data)
    plot_hit_rate_over_steps(results, out_dir / f"{stem}_hit_rate.png")
    plot_before_after_eviction(results, out_dir / f"{stem}_before_after.png")

    print("\nDone.")


if __name__ == "__main__":
    main()
