"""Compare LRU and FIFO across default workloads with multi-seed averages."""

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

PLOT_COLORS = {"lru": "#A78BFA", "fifo": "#2A9D8F"}


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

from simulator.runner import run_trace
from simulator.workload import (
    generate_default_workloads,
    make_default_workload_seeds,
    summarize_workloads,
)


def _aggregate_results(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["workload", "policy", "capacity"], as_index=False)
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
        .sort_values(["workload", "policy"])
    )
    return grouped.fillna(0.0)


def _aggregate_workload_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby("workload", as_index=False)
        .agg(
            n_seeds=("base_seed", "nunique"),
            total_accesses_mean=("total_accesses", "mean"),
            unique_blocks_mean=("unique_blocks", "mean"),
            unique_blocks_std=("unique_blocks", "std"),
            reuse_ratio_mean=("reuse_ratio", "mean"),
            reuse_ratio_std=("reuse_ratio", "std"),
            avg_accesses_per_unique_block_mean=("avg_accesses_per_unique_block", "mean"),
        )
        .sort_values("workload")
    )
    return grouped.fillna(0.0)


def _plot_grouped_bars(
    df: pd.DataFrame,
    value_mean_col: str,
    value_std_col: str,
    ylabel: str,
    title: str,
    output_path: Path,
) -> None:
    workloads = sorted(df["workload"].unique())
    policies = ["lru", "fifo"]

    x = np.arange(len(workloads))
    width = 0.35
    _set_plot_style()
    fig, ax = plt.subplots(figsize=(8.5, 5.5))

    for idx, policy in enumerate(policies):
        sub = df[df["policy"] == policy].set_index("workload").reindex(workloads)
        means = sub[value_mean_col].to_numpy()
        offset = (idx - 0.5) * width
        ax.bar(
            x + offset,
            means,
            width=width,
            label=policy.upper(),
            color=PLOT_COLORS[policy],
            alpha=0.92,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(workloads)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.4)
    if "rate" in ylabel.lower():
        ax.set_ylim(0.0, 1.0)
    ax.legend(title="Policy")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    policies = ["lru", "fifo"]
    capacity = 128
    base_seeds = list(range(42, 42 + 15))

    rows = []
    workload_summary_rows = []

    for base_seed in base_seeds:
        workloads = generate_default_workloads(seed=base_seed)
        workload_seeds = make_default_workload_seeds(seed=base_seed)

        for summary_row in summarize_workloads(workloads):
            summary_row["base_seed"] = base_seed
            summary_row["workload_seed"] = workload_seeds[summary_row["workload"]]
            workload_summary_rows.append(summary_row)

        for workload_name, trace in workloads.items():
            for policy in policies:
                result = run_trace(
                    trace=trace,
                    policy_name=policy,
                    capacity=capacity,
                    workload_name=workload_name,
                )
                row = result.to_dict()
                row["base_seed"] = base_seed
                row["workload_seed"] = workload_seeds[workload_name]
                rows.append(row)

    raw_df = pd.DataFrame(rows).sort_values(["base_seed", "workload", "policy"])
    agg_df = _aggregate_results(raw_df)

    workload_raw_df = pd.DataFrame(workload_summary_rows).sort_values(["base_seed", "workload"])
    workload_agg_df = _aggregate_workload_summary(workload_raw_df)

    assert (agg_df["n_seeds"] == len(base_seeds)).all()
    assert agg_df["hit_rate_mean"].between(0.0, 1.0).all()
    assert workload_agg_df["reuse_ratio_mean"].between(0.0, 1.0).all()

    raw_csv_path = data_dir / "policy_compare_raw.csv"
    agg_csv_path = data_dir / "policy_compare.csv"
    workload_raw_csv_path = data_dir / "policy_compare_workload_summary_raw.csv"
    workload_agg_csv_path = data_dir / "policy_compare_workload_summary.csv"

    raw_df.to_csv(raw_csv_path, index=False)
    agg_df.to_csv(agg_csv_path, index=False)
    workload_raw_df.to_csv(workload_raw_csv_path, index=False)
    workload_agg_df.to_csv(workload_agg_csv_path, index=False)

    fig_hit = fig_dir / "policy_compare_hit_rate.png"
    _plot_grouped_bars(
        agg_df,
        value_mean_col="hit_rate_mean",
        value_std_col="hit_rate_std",
        ylabel="Hit Rate",
        title=f"Hit Rate by Workload and Policy (capacity={capacity}, n={len(base_seeds)} seeds)",
        output_path=fig_hit,
    )

    fig_rec = fig_dir / "policy_compare_recompute_cost.png"
    _plot_grouped_bars(
        agg_df,
        value_mean_col="recomputation_cost_mean",
        value_std_col="recomputation_cost_std",
        ylabel="Recomputation Cost",
        title=f"Recomputation Cost by Workload and Policy (capacity={capacity}, n={len(base_seeds)} seeds)",
        output_path=fig_rec,
    )

    print("Saved:", raw_csv_path)
    print("Saved:", agg_csv_path)
    print("Saved:", workload_raw_csv_path)
    print("Saved:", workload_agg_csv_path)
    print("Saved:", fig_hit)
    print("Saved:", fig_rec)
    print("\nWorkload summaries (mean across seeds):")
    print(workload_agg_df.to_string(index=False))
    print("\nPolicy comparison (mean/std across seeds):")
    print(agg_df.to_string(index=False))


if __name__ == "__main__":
    main()
