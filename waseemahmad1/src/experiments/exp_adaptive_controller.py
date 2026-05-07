"""Evaluate adaptive cost-aware eviction under workload shift.

This version runs a more robust evaluation across:
- multiple cache capacities
- multiple shift severities
- 15 seeds
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "results" / ".mplconfig"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PLOT_COLORS = {"fifo": "#2A9D8F", "lru": "#A78BFA", "adaptive": "#6D28D9"}


def _set_plot_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "axes.facecolor": "#f8f9fb",
            "figure.facecolor": "#ffffff",
            "grid.color": "#d9dde5",
            "grid.alpha": 0.45,
            "axes.edgecolor": "#d0d4dc",
            "axes.titleweight": "semibold",
        }
    )

from simulator.global_cache import SharedGlobalKVCacheSimulator
from simulator.global_workload import generate_shifted_global_events, summarize_events


def _phase_stats(total: int, hits: int, misses: int, recomputation_cost: int) -> dict:
    return {
        "total_accesses": total,
        "hits": hits,
        "misses": misses,
        "hit_rate": (hits / total) if total else 0.0,
        "miss_rate": (misses / total) if total else 0.0,
        "recomputation_cost": recomputation_cost,
    }


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["policy", "phase", "capacity", "shift_level"], as_index=False)
        .agg(
            n_seeds=("base_seed", "nunique"),
            total_accesses_mean=("total_accesses", "mean"),
            hits_mean=("hits", "mean"),
            misses_mean=("misses", "mean"),
            hit_rate_mean=("hit_rate", "mean"),
            hit_rate_std=("hit_rate", "std"),
            recomputation_cost_mean=("recomputation_cost", "mean"),
            recomputation_cost_std=("recomputation_cost", "std"),
        )
        .sort_values(["shift_level", "phase", "capacity", "policy"])
    )
    return grouped.fillna(0.0)


def _plot_overall_vs_capacity(
    agg_df: pd.DataFrame,
    value_mean_col: str,
    value_std_col: str,
    ylabel: str,
    title: str,
    output: Path,
) -> None:
    overall = agg_df[agg_df["phase"] == "overall"]
    shift_levels = ["moderate", "hard"]
    policies = ["fifo", "lru", "adaptive"]

    _set_plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.0), sharey=True)
    for ax, shift_level in zip(axes, shift_levels):
        sub_shift = overall[overall["shift_level"] == shift_level]
        for policy in policies:
            sub = sub_shift[sub_shift["policy"] == policy].sort_values("capacity")
            ax.plot(
                sub["capacity"],
                sub[value_mean_col],
                marker="o",
                linewidth=2.5,
                markersize=6,
                color=PLOT_COLORS[policy],
                label=policy.upper(),
            )

        ax.set_title(f"Shift: {shift_level}")
        ax.set_xlabel("Capacity (blocks)")
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel(ylabel)
    axes[0].legend(title="Policy")
    fig.suptitle(title)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close()


def _plot_phase_bars(
    agg_df: pd.DataFrame,
    shift_level: str,
    capacity: int,
    output: Path,
    n_seeds: int,
) -> None:
    subset = agg_df[
        (agg_df["shift_level"] == shift_level)
        & (agg_df["capacity"] == capacity)
        & (agg_df["phase"].isin(["phase1", "phase2", "overall"]))
    ]

    policies = ["fifo", "lru", "adaptive"]
    phases = ["phase1", "phase2", "overall"]
    x = np.arange(len(phases))
    width = 0.25

    _set_plot_style()
    fig, ax = plt.subplots(figsize=(9.0, 5.8))
    for idx, policy in enumerate(policies):
        sub = subset[subset["policy"] == policy].set_index("phase").reindex(phases)
        means = sub["hit_rate_mean"].to_numpy()
        offset = (idx - 1) * width
        ax.bar(
            x + offset,
            means,
            width=width,
            alpha=0.92,
            color=PLOT_COLORS[policy],
            label=policy.upper(),
        )

    ax.set_xticks(x)
    ax.set_xticklabels([p.title() for p in phases])
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Hit Rate")
    ax.set_title(
        f"Adaptive vs Baselines by Phase ({shift_level}, capacity={capacity}, n={n_seeds})"
    )
    ax.grid(axis="y", alpha=0.3)
    ax.legend(title="Policy")
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close()


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    policies = ["fifo", "lru", "adaptive"]
    shift_levels = ["moderate", "hard"]
    capacities = [256, 384, 512]
    base_seeds = list(range(3026, 3026 + 15))

    rows = []
    workload_rows = []

    for shift_level in shift_levels:
        for base_seed in base_seeds:
            events, split_idx = generate_shifted_global_events(seed=base_seed, shift_level=shift_level)

            summary_row = summarize_events(events)
            summary_row["base_seed"] = base_seed
            summary_row["shift_level"] = shift_level
            summary_row["split_index"] = split_idx
            workload_rows.append(summary_row)

            for capacity in capacities:
                for policy in policies:
                    sim = SharedGlobalKVCacheSimulator(capacity=capacity, policy_name=policy)

                    phase_counters = {
                        "phase1": {"total": 0, "hits": 0, "misses": 0, "recompute": 0},
                        "phase2": {"total": 0, "hits": 0, "misses": 0, "recompute": 0},
                    }

                    for idx, (request_id, key, recompute_cost, shared_prefix) in enumerate(events):
                        phase = "phase1" if idx < split_idx else "phase2"
                        hit = sim.access(
                            request_id=request_id,
                            key=key,
                            recompute_cost=recompute_cost,
                            shared_prefix=shared_prefix,
                        )
                        phase_counters[phase]["total"] += 1
                        if hit:
                            phase_counters[phase]["hits"] += 1
                        else:
                            phase_counters[phase]["misses"] += 1
                            phase_counters[phase]["recompute"] += recompute_cost

                    overall_total = phase_counters["phase1"]["total"] + phase_counters["phase2"]["total"]
                    overall_hits = phase_counters["phase1"]["hits"] + phase_counters["phase2"]["hits"]
                    overall_misses = (
                        phase_counters["phase1"]["misses"] + phase_counters["phase2"]["misses"]
                    )
                    overall_recompute = (
                        phase_counters["phase1"]["recompute"] + phase_counters["phase2"]["recompute"]
                    )

                    for phase_name in ["phase1", "phase2"]:
                        stats = _phase_stats(
                            total=phase_counters[phase_name]["total"],
                            hits=phase_counters[phase_name]["hits"],
                            misses=phase_counters[phase_name]["misses"],
                            recomputation_cost=phase_counters[phase_name]["recompute"],
                        )
                        stats.update(
                            {
                                "policy": policy,
                                "phase": phase_name,
                                "capacity": capacity,
                                "shift_level": shift_level,
                                "base_seed": base_seed,
                            }
                        )
                        rows.append(stats)

                    overall_stats = _phase_stats(
                        total=overall_total,
                        hits=overall_hits,
                        misses=overall_misses,
                        recomputation_cost=overall_recompute,
                    )
                    overall_stats.update(
                        {
                            "policy": policy,
                            "phase": "overall",
                            "capacity": capacity,
                            "shift_level": shift_level,
                            "base_seed": base_seed,
                        }
                    )
                    rows.append(overall_stats)

    raw_df = pd.DataFrame(rows).sort_values(
        ["shift_level", "base_seed", "capacity", "phase", "policy"]
    )
    agg_df = _aggregate(raw_df)

    workload_raw_df = pd.DataFrame(workload_rows).sort_values(["shift_level", "base_seed"])
    workload_agg_df = (
        workload_raw_df.groupby("shift_level", as_index=False)
        .agg(
            n_seeds=("base_seed", "nunique"),
            total_accesses_mean=("total_accesses", "mean"),
            unique_blocks_mean=("unique_blocks", "mean"),
            unique_blocks_std=("unique_blocks", "std"),
            reuse_ratio_mean=("reuse_ratio", "mean"),
            reuse_ratio_std=("reuse_ratio", "std"),
            shared_prefix_fraction_mean=("shared_prefix_fraction", "mean"),
            shared_prefix_fraction_std=("shared_prefix_fraction", "std"),
            avg_recompute_cost_mean=("avg_recompute_cost", "mean"),
            avg_recompute_cost_std=("avg_recompute_cost", "std"),
            split_index_mean=("split_index", "mean"),
        )
        .sort_values("shift_level")
        .fillna(0.0)
    )

    overall = agg_df[agg_df["phase"] == "overall"]
    delta_rows = []
    for shift_level in shift_levels:
        for capacity in capacities:
            sub = overall[(overall["shift_level"] == shift_level) & (overall["capacity"] == capacity)]
            lru_row = sub[sub["policy"] == "lru"].iloc[0]
            for policy in policies:
                row = sub[sub["policy"] == policy].iloc[0]
                delta_rows.append(
                    {
                        "shift_level": shift_level,
                        "capacity": capacity,
                        "policy": policy,
                        "n_seeds": int(row["n_seeds"]),
                        "hit_rate_gain_vs_lru": row["hit_rate_mean"] - lru_row["hit_rate_mean"],
                        "recompute_reduction_vs_lru": (
                            lru_row["recomputation_cost_mean"] - row["recomputation_cost_mean"]
                        ),
                    }
                )
    delta_df = pd.DataFrame(delta_rows).sort_values(["shift_level", "capacity", "policy"])

    assert (agg_df["n_seeds"] == len(base_seeds)).all()
    assert agg_df["hit_rate_mean"].between(0.0, 1.0).all()

    raw_csv = data_dir / "adaptive_controller_raw.csv"
    agg_csv = data_dir / "adaptive_controller.csv"
    delta_csv = data_dir / "adaptive_controller_delta_vs_lru.csv"
    workload_raw_csv = data_dir / "adaptive_workload_summary_raw.csv"
    workload_agg_csv = data_dir / "adaptive_workload_summary.csv"

    raw_df.to_csv(raw_csv, index=False)
    agg_df.to_csv(agg_csv, index=False)
    delta_df.to_csv(delta_csv, index=False)
    workload_raw_df.to_csv(workload_raw_csv, index=False)
    workload_agg_df.to_csv(workload_agg_csv, index=False)

    fig_hit_vs_capacity = fig_dir / "adaptive_controller_overall_hit_rate_vs_capacity.png"
    _plot_overall_vs_capacity(
        agg_df,
        value_mean_col="hit_rate_mean",
        value_std_col="hit_rate_std",
        ylabel="Overall Hit Rate",
        title=f"Adaptive Robustness: Overall Hit Rate vs Capacity (n={len(base_seeds)} seeds)",
        output=fig_hit_vs_capacity,
    )

    fig_recompute_vs_capacity = fig_dir / "adaptive_controller_overall_recompute_vs_capacity.png"
    _plot_overall_vs_capacity(
        agg_df,
        value_mean_col="recomputation_cost_mean",
        value_std_col="recomputation_cost_std",
        ylabel="Overall Recomputation Cost",
        title=(
            f"Adaptive Robustness: Overall Recomputation vs Capacity "
            f"(n={len(base_seeds)} seeds)"
        ),
        output=fig_recompute_vs_capacity,
    )

    focus_capacity = 384
    fig_phase = fig_dir / "adaptive_controller_hit_rate_by_phase.png"
    _plot_phase_bars(
        agg_df,
        shift_level="hard",
        capacity=focus_capacity,
        output=fig_phase,
        n_seeds=len(base_seeds),
    )

    print("Saved:", raw_csv)
    print("Saved:", agg_csv)
    print("Saved:", delta_csv)
    print("Saved:", workload_raw_csv)
    print("Saved:", workload_agg_csv)
    print("Saved:", fig_hit_vs_capacity)
    print("Saved:", fig_recompute_vs_capacity)
    print("Saved:", fig_phase)
    print("\nShifted workload summary by severity:")
    print(workload_agg_df.to_string(index=False))
    print("\nAdaptive-controller comparison:")
    print(agg_df.to_string(index=False))
    print("\nDeltas vs LRU (overall):")
    print(delta_df.to_string(index=False))


if __name__ == "__main__":
    main()
