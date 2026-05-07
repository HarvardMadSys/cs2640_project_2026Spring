"""Sweep cache sizes and compare policies on one workload with multi-seed averages."""

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
    generate_long_context_workload,
    make_default_workload_seeds,
    summarize_trace,
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
        )
        .sort_values(["policy", "capacity"])
    )
    return grouped.fillna(0.0)


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    capacities = [32, 64, 128, 256, 512]
    policies = ["lru", "fifo"]
    workload_name = "long_context"
    base_seeds = list(range(42, 42 + 15))

    rows = []
    workload_summary_rows = []

    for base_seed in base_seeds:
        workload_seed = make_default_workload_seeds(seed=base_seed)[workload_name]
        trace = generate_long_context_workload(seed=workload_seed)

        summary_row = summarize_trace(trace)
        summary_row["workload"] = workload_name
        summary_row["base_seed"] = base_seed
        summary_row["workload_seed"] = workload_seed
        workload_summary_rows.append(summary_row)

        for cap in capacities:
            for policy in policies:
                result = run_trace(
                    trace=trace,
                    policy_name=policy,
                    capacity=cap,
                    workload_name=workload_name,
                )
                row = result.to_dict()
                row["base_seed"] = base_seed
                row["workload_seed"] = workload_seed
                rows.append(row)

    raw_df = pd.DataFrame(rows).sort_values(["base_seed", "policy", "capacity"])
    agg_df = _aggregate_results(raw_df)

    assert (agg_df["n_seeds"] == len(base_seeds)).all()
    assert agg_df["hit_rate_mean"].between(0.0, 1.0).all()

    workload_raw_df = pd.DataFrame(workload_summary_rows).sort_values("base_seed")
    workload_agg_df = pd.DataFrame(
        [
            {
                "workload": workload_name,
                "n_seeds": len(base_seeds),
                "total_accesses_mean": workload_raw_df["total_accesses"].mean(),
                "unique_blocks_mean": workload_raw_df["unique_blocks"].mean(),
                "unique_blocks_std": workload_raw_df["unique_blocks"].std(),
                "reuse_ratio_mean": workload_raw_df["reuse_ratio"].mean(),
                "reuse_ratio_std": workload_raw_df["reuse_ratio"].std(),
                "avg_accesses_per_unique_block_mean": workload_raw_df[
                    "avg_accesses_per_unique_block"
                ].mean(),
            }
        ]
    ).fillna(0.0)
    assert workload_agg_df["reuse_ratio_mean"].between(0.0, 1.0).all()

    raw_csv_path = data_dir / "cache_size_sweep_raw.csv"
    agg_csv_path = data_dir / "cache_size_sweep.csv"
    workload_raw_csv_path = data_dir / "cache_size_sweep_workload_summary_raw.csv"
    workload_agg_csv_path = data_dir / "cache_size_sweep_workload_summary.csv"

    raw_df.to_csv(raw_csv_path, index=False)
    agg_df.to_csv(agg_csv_path, index=False)
    workload_raw_df.to_csv(workload_raw_csv_path, index=False)
    workload_agg_df.to_csv(workload_agg_csv_path, index=False)

    fig_path = fig_dir / "cache_size_sweep_hit_rate.png"
    _set_plot_style()
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    for policy in policies:
        sub = agg_df[agg_df["policy"] == policy]
        ax.plot(
            sub["capacity"],
            sub["hit_rate_mean"],
            marker="o",
            linewidth=2.5,
            markersize=6,
            color=PLOT_COLORS[policy],
            label=policy.upper(),
        )

    ax.set_title(f"Hit Rate vs Cache Size ({workload_name}, n={len(base_seeds)} seeds)")
    ax.set_xlabel("Cache Capacity (blocks)")
    ax.set_ylabel("Hit Rate")
    ax.set_ylim(0, 1.0)
    ax.grid(True, alpha=0.3)
    ax.legend(title="Policy")
    plt.tight_layout()
    plt.savefig(fig_path, dpi=150)
    plt.close()

    print("Saved:", raw_csv_path)
    print("Saved:", agg_csv_path)
    print("Saved:", workload_raw_csv_path)
    print("Saved:", workload_agg_csv_path)
    print("Saved:", fig_path)
    print("\nWorkload summary across seeds:")
    print(workload_agg_df.to_string(index=False))
    print("\nCache size sweep (mean/std across seeds):")
    print(agg_df.to_string(index=False))


if __name__ == "__main__":
    main()
