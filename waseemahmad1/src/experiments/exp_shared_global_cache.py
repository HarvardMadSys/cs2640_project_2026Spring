"""Compare local per-request cache vs MOONCAKE-style shared global cache."""

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

PLOT_COLORS = {"local": "#9CA3AF", "shared_global": "#A78BFA"}


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
        df.groupby(["mode", "policy", "capacity"], as_index=False)
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
        .sort_values(["mode", "policy"])
    )
    return grouped.fillna(0.0)


def _plot_mode_policy_bars(
    df: pd.DataFrame,
    mean_col: str,
    std_col: str,
    ylabel: str,
    title: str,
    output: Path,
) -> None:
    policies = ["lru", "fifo"]
    modes = ["local", "shared_global"]

    x = np.arange(len(policies))
    width = 0.34

    _set_plot_style()
    fig, ax = plt.subplots(figsize=(8.8, 5.6))
    for idx, mode in enumerate(modes):
        sub = df[df["mode"] == mode].set_index("policy").reindex(policies)
        means = sub[mean_col].to_numpy()
        offset = (idx - 0.5) * width
        ax.bar(
            x + offset,
            means,
            width=width,
            label=mode.replace("_", " ").title(),
            color=PLOT_COLORS[mode],
            alpha=0.92,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([p.upper() for p in policies])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    if "rate" in ylabel.lower():
        ax.set_ylim(0.0, 1.0)
    ax.legend(title="Mode")
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close()


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    policies = ["lru", "fifo"]
    base_seeds = list(range(2026, 2026 + 15))

    num_requests = 8
    request_length = 500
    total_capacity = 512
    local_capacity = total_capacity // num_requests

    rows = []
    workload_rows = []

    for base_seed in base_seeds:
        traces = generate_concurrent_request_traces(
            num_requests=num_requests,
            request_length=request_length,
            shared_prefix_blocks=32,
            unique_blocks_per_request=320,
            shared_prefix_reuse_prob=0.30,
            recency_reuse_prob=0.50,
            seed=base_seed,
        )
        interleaved = interleave_request_traces(traces, mode="round_robin", seed=base_seed + 999)
        events = traces_to_events(interleaved)

        summary_row = summarize_events(events)
        summary_row["base_seed"] = base_seed
        workload_rows.append(summary_row)

        for policy in policies:
            shared = run_shared_events(
                events=events,
                policy_name=policy,
                capacity=total_capacity,
                workload_name="shared_global",
            ).to_dict()
            shared["base_seed"] = base_seed
            shared["mode"] = "shared_global"
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
                    "base_seed": base_seed,
                    "mode": "local",
                }
            )
            rows.append(local)

    raw_df = pd.DataFrame(rows).sort_values(["base_seed", "mode", "policy"])
    agg_df = _aggregate(raw_df)

    workload_raw_df = pd.DataFrame(workload_rows).sort_values("base_seed")
    workload_agg_df = (
        workload_raw_df.agg(
            {
                "total_accesses": "mean",
                "unique_blocks": ["mean", "std"],
                "unique_requests": "mean",
                "reuse_ratio": ["mean", "std"],
                "shared_prefix_fraction": ["mean", "std"],
                "avg_recompute_cost": ["mean", "std"],
            }
        )
        .transpose()
        .reset_index()
        .rename(columns={"index": "metric", "mean": "value_mean", "std": "value_std"})
        .fillna(0.0)
    )

    delta_rows = []
    for policy in policies:
        sub = agg_df[agg_df["policy"] == policy]
        local = sub[sub["mode"] == "local"].iloc[0]
        shared = sub[sub["mode"] == "shared_global"].iloc[0]
        delta_rows.append(
            {
                "policy": policy,
                "shared_minus_local_hit_rate": shared["hit_rate_mean"] - local["hit_rate_mean"],
                "shared_minus_local_recompute": shared["recomputation_cost_mean"]
                - local["recomputation_cost_mean"],
            }
        )
    delta_df = pd.DataFrame(delta_rows)

    assert (agg_df["n_seeds"] == len(base_seeds)).all()
    assert agg_df["hit_rate_mean"].between(0.0, 1.0).all()

    raw_csv = data_dir / "shared_global_compare_raw.csv"
    agg_csv = data_dir / "shared_global_compare.csv"
    delta_csv = data_dir / "shared_global_compare_delta.csv"
    workload_raw_csv = data_dir / "shared_global_workload_summary_raw.csv"
    workload_agg_csv = data_dir / "shared_global_workload_summary.csv"

    raw_df.to_csv(raw_csv, index=False)
    agg_df.to_csv(agg_csv, index=False)
    delta_df.to_csv(delta_csv, index=False)
    workload_raw_df.to_csv(workload_raw_csv, index=False)
    workload_agg_df.to_csv(workload_agg_csv, index=False)

    fig_hit = fig_dir / "shared_global_compare_hit_rate.png"
    _plot_mode_policy_bars(
        agg_df,
        mean_col="hit_rate_mean",
        std_col="hit_rate_std",
        ylabel="Hit Rate",
        title=(
            "Local vs Shared Global Cache Hit Rate "
            f"(total capacity={total_capacity}, n={len(base_seeds)} seeds)"
        ),
        output=fig_hit,
    )

    fig_rec = fig_dir / "shared_global_compare_recompute_cost.png"
    _plot_mode_policy_bars(
        agg_df,
        mean_col="recomputation_cost_mean",
        std_col="recomputation_cost_std",
        ylabel="Recomputation Cost",
        title=(
            "Local vs Shared Global Recomputation Cost "
            f"(total capacity={total_capacity}, n={len(base_seeds)} seeds)"
        ),
        output=fig_rec,
    )

    print("Saved:", raw_csv)
    print("Saved:", agg_csv)
    print("Saved:", delta_csv)
    print("Saved:", workload_raw_csv)
    print("Saved:", workload_agg_csv)
    print("Saved:", fig_hit)
    print("Saved:", fig_rec)
    print("\nShared global workload summary:")
    print(workload_agg_df.to_string(index=False))
    print("\nLocal vs shared comparison:")
    print(agg_df.to_string(index=False))
    print("\nShared - local deltas:")
    print(delta_df.to_string(index=False))


if __name__ == "__main__":
    main()
