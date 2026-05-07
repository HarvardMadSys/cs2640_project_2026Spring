"""Sensitivity sweep for shared-global cache gains.

This script measures how much shared-global caching helps compared to
isolated local caches while varying:
1) shared-prefix reuse intensity
2) number of concurrent requests
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "results" / ".mplconfig"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PLOT_COLORS = {4: "#A78BFA", 8: "#2A9D8F", 16: "#E9C46A"}


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

from simulator.global_cache import SharedGlobalKVCacheSimulator, run_shared_events
from simulator.global_workload import (
    generate_concurrent_request_traces,
    interleave_request_traces,
    recompute_cost_for_key,
    summarize_events,
    traces_to_events,
)


def _run_local_baseline(
    traces: Mapping[str, Sequence[str]],
    policy: str,
    local_capacity: int,
) -> Dict[str, float]:
    total_accesses = 0
    hits = 0
    misses = 0
    recomputation_cost = 0

    for request_id, trace in traces.items():
        sim = SharedGlobalKVCacheSimulator(capacity=local_capacity, policy_name=policy)
        for key in trace:
            hit = sim.access(
                request_id=request_id,
                key=key,
                recompute_cost=recompute_cost_for_key(key),
                shared_prefix=key.startswith("shared_prefix_block_"),
            )
            total_accesses += 1
            if hit:
                hits += 1
            else:
                misses += 1
                recomputation_cost += recompute_cost_for_key(key)

    return {
        "total_accesses": total_accesses,
        "hits": hits,
        "misses": misses,
        "hit_rate": (hits / total_accesses) if total_accesses else 0.0,
        "miss_rate": (misses / total_accesses) if total_accesses else 0.0,
        "recomputation_cost": recomputation_cost,
    }


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(
            [
                "policy",
                "mode",
                "num_requests",
                "shared_prefix_reuse_prob",
                "capacity",
            ],
            as_index=False,
        )
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
        .sort_values(["policy", "mode", "num_requests", "shared_prefix_reuse_prob"])
    )
    return grouped.fillna(0.0)


def _build_delta_table(agg_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    grouping_cols = ["policy", "num_requests", "shared_prefix_reuse_prob"]
    for key_values, _ in agg_df.groupby(grouping_cols):
        key = dict(zip(grouping_cols, key_values))
        sub = agg_df[
            (agg_df["policy"] == key["policy"])
            & (agg_df["num_requests"] == key["num_requests"])
            & (agg_df["shared_prefix_reuse_prob"] == key["shared_prefix_reuse_prob"])
        ]
        local = sub[sub["mode"] == "local"].iloc[0]
        shared = sub[sub["mode"] == "shared_global"].iloc[0]

        rows.append(
            {
                **key,
                "n_seeds": int(shared["n_seeds"]),
                "shared_minus_local_hit_rate": shared["hit_rate_mean"] - local["hit_rate_mean"],
                "shared_minus_local_hit_rate_std": (
                    shared["hit_rate_std"] ** 2 + local["hit_rate_std"] ** 2
                )
                ** 0.5,
                "local_minus_shared_recompute": (
                    local["recomputation_cost_mean"] - shared["recomputation_cost_mean"]
                ),
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["policy", "num_requests", "shared_prefix_reuse_prob"]
    )


def _plot_sensitivity(
    delta_df: pd.DataFrame,
    value_col: str,
    ylabel: str,
    title: str,
    output_path: Path,
) -> None:
    policies = ["lru", "fifo"]
    request_counts = sorted(delta_df["num_requests"].unique())

    _set_plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8), sharex=True)
    for ax, policy in zip(axes, policies):
        subp = delta_df[delta_df["policy"] == policy]
        for request_count in request_counts:
            sub = subp[subp["num_requests"] == request_count].sort_values(
                "shared_prefix_reuse_prob"
            )
            ax.plot(
                sub["shared_prefix_reuse_prob"],
                sub[value_col],
                marker="o",
                linewidth=2.4,
                color=PLOT_COLORS[request_count],
                label=f"requests={request_count}",
            )

        ax.set_title(policy.upper())
        ax.set_xlabel("Shared Prefix Reuse Probability")
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel(ylabel)
    axes[0].legend(title="Concurrency")
    fig.suptitle(title)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    policies = ["lru", "fifo"]
    base_seeds = list(range(2026, 2026 + 15))

    total_capacity = 512
    request_length = 500
    request_counts = [4, 8, 16]
    shared_prefix_reuse_probs = [0.05, 0.20, 0.35]

    rows = []
    workload_rows = []

    for base_seed in base_seeds:
        for num_requests in request_counts:
            local_capacity = max(1, total_capacity // num_requests)
            for shared_prefix_reuse_prob in shared_prefix_reuse_probs:
                traces = generate_concurrent_request_traces(
                    num_requests=num_requests,
                    request_length=request_length,
                    shared_prefix_blocks=32,
                    unique_blocks_per_request=320,
                    shared_prefix_reuse_prob=shared_prefix_reuse_prob,
                    recency_reuse_prob=0.50,
                    seed=base_seed,
                )
                interleaved = interleave_request_traces(
                    traces,
                    mode="round_robin",
                    seed=base_seed + 999,
                )
                events = traces_to_events(interleaved)

                workload_summary = summarize_events(events)
                workload_summary.update(
                    {
                        "base_seed": base_seed,
                        "num_requests": num_requests,
                        "shared_prefix_reuse_prob": shared_prefix_reuse_prob,
                    }
                )
                workload_rows.append(workload_summary)

                for policy in policies:
                    shared = run_shared_events(
                        events=events,
                        policy_name=policy,
                        capacity=total_capacity,
                        workload_name="shared_global",
                    ).to_dict()
                    shared.update(
                        {
                            "policy": policy,
                            "mode": "shared_global",
                            "base_seed": base_seed,
                            "num_requests": num_requests,
                            "shared_prefix_reuse_prob": shared_prefix_reuse_prob,
                        }
                    )
                    rows.append(shared)

                    local = _run_local_baseline(
                        traces=traces,
                        policy=policy,
                        local_capacity=local_capacity,
                    )
                    local.update(
                        {
                            "workload": "local_isolated",
                            "policy": policy,
                            "capacity": local_capacity,
                            "unique_requests": num_requests,
                            "mode": "local",
                            "base_seed": base_seed,
                            "num_requests": num_requests,
                            "shared_prefix_reuse_prob": shared_prefix_reuse_prob,
                        }
                    )
                    rows.append(local)

    raw_df = pd.DataFrame(rows).sort_values(
        [
            "base_seed",
            "policy",
            "mode",
            "num_requests",
            "shared_prefix_reuse_prob",
        ]
    )
    agg_df = _aggregate(raw_df)
    delta_df = _build_delta_table(agg_df)

    workload_raw_df = pd.DataFrame(workload_rows).sort_values(
        ["base_seed", "num_requests", "shared_prefix_reuse_prob"]
    )
    workload_agg_df = (
        workload_raw_df.groupby(["num_requests", "shared_prefix_reuse_prob"], as_index=False)
        .agg(
            n_seeds=("base_seed", "nunique"),
            total_accesses_mean=("total_accesses", "mean"),
            unique_blocks_mean=("unique_blocks", "mean"),
            reuse_ratio_mean=("reuse_ratio", "mean"),
            shared_prefix_fraction_mean=("shared_prefix_fraction", "mean"),
            avg_recompute_cost_mean=("avg_recompute_cost", "mean"),
        )
        .sort_values(["num_requests", "shared_prefix_reuse_prob"])
    )

    assert (agg_df["n_seeds"] == len(base_seeds)).all()
    assert agg_df["hit_rate_mean"].between(0.0, 1.0).all()

    raw_csv_path = data_dir / "shared_global_sensitivity_raw.csv"
    agg_csv_path = data_dir / "shared_global_sensitivity.csv"
    delta_csv_path = data_dir / "shared_global_sensitivity_delta.csv"
    workload_raw_csv_path = data_dir / "shared_global_sensitivity_workload_summary_raw.csv"
    workload_agg_csv_path = data_dir / "shared_global_sensitivity_workload_summary.csv"

    raw_df.to_csv(raw_csv_path, index=False)
    agg_df.to_csv(agg_csv_path, index=False)
    delta_df.to_csv(delta_csv_path, index=False)
    workload_raw_df.to_csv(workload_raw_csv_path, index=False)
    workload_agg_df.to_csv(workload_agg_csv_path, index=False)

    fig_hit = fig_dir / "shared_global_sensitivity_hit_gain.png"
    _plot_sensitivity(
        delta_df=delta_df,
        value_col="shared_minus_local_hit_rate",
        ylabel="Shared - Local Hit Rate",
        title="Shared-Global Hit Rate Gain Sensitivity",
        output_path=fig_hit,
    )

    fig_recompute = fig_dir / "shared_global_sensitivity_recompute_reduction.png"
    _plot_sensitivity(
        delta_df=delta_df,
        value_col="local_minus_shared_recompute",
        ylabel="Local - Shared Recomputation Cost",
        title="Shared-Global Recomputation Reduction Sensitivity",
        output_path=fig_recompute,
    )

    print("Saved:", raw_csv_path)
    print("Saved:", agg_csv_path)
    print("Saved:", delta_csv_path)
    print("Saved:", workload_raw_csv_path)
    print("Saved:", workload_agg_csv_path)
    print("Saved:", fig_hit)
    print("Saved:", fig_recompute)
    print("\nShared-global sensitivity deltas:")
    print(delta_df.to_string(index=False))


if __name__ == "__main__":
    main()
