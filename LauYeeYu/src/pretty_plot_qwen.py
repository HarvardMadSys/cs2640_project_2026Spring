#!/usr/bin/env python3
"""Generate presentation-quality line plots for qwen_traceA and qwen_traceB."""

import os
import argparse
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from visualize_results import (
    ALGORITHM_ORDER,
    ALGORITHM_DISPLAY_NAMES,
    get_algorithm_display_name as _base_display_name,
    collect_all_data,
)

# Per-presentation overrides for algorithm display names.
DISPLAY_NAME_OVERRIDES = {
    "RandomQuickDemotion": "CHAFRA",
    "RandomCompute":          "CHAFRA (w/o quick demotion)",
}

# Algorithms intentionally hidden from these presentation plots.
HIDDEN_ALGORITHMS = {"LHD_compute", "GDSF_compute", "Belady"}

# Cache sizes (in blocks) shown on the x-axis: 4k, 6k, 8k, 10k, 20k, 80k.
ALLOWED_CACHE_SIZES = {4096, 6144, 8192, 10240, 20480, 81920}

# Local plotting order — the shared ALGORITHM_ORDER lists RandomComputeAdmission
# (not produced by the current simulator) instead of RandomQuickDemotion (the
# CHAFRA policy that we actually want to show).
LOCAL_ALGORITHM_ORDER = [
    "RandomQuickDemotion" if a == "RandomComputeAdmission" else a
    for a in ALGORITHM_ORDER
]


def get_algorithm_display_name(algo_name):
    if algo_name in DISPLAY_NAME_OVERRIDES:
        return DISPLAY_NAME_OVERRIDES[algo_name]
    return _base_display_name(algo_name)

TRACES = {
    "qwen_traceA_blksz_16_pos": "Qwen To-C Trace",
    "qwen_traceB_blksz_16_pos": "Qwen To-B Trace",
    "qwen_coder_blksz_16_pos": "Qwen Coder Trace",
    "qwen_thinking_blksz_16_pos": "Qwen Thinking Trace",
}

# Curated palette + linestyle/marker per algorithm. Online policies use solid
# lines, oracles use dashed so they read as a separate class at a glance.
ALGO_STYLE = {
    "LRU":                     {"color": "#9E9E9E", "ls": "-",  "marker": "o"},
    "S3FIFO":                  {"color": "#1f77b4", "ls": "-",  "marker": "s"},
    "ARC":                     {"color": "#17becf", "ls": "-",  "marker": "^"},
    "GDSF_compute":            {"color": "#2ca02c", "ls": "-",  "marker": "D"},
    "LHD_compute":             {"color": "#bcbd22", "ls": "-",  "marker": "v"},
    "RandomCompute":           {"color": "#ff7f0e", "ls": "-",  "marker": "P"},
    "RandomQuickDemotion":     {"color": "#e377c2", "ls": "-",  "marker": "X"},
    "Belady":                  {"color": "#8c564b", "ls": "--", "marker": "h"},
    "BeladyCompute":           {"color": "#d62728", "ls": "--", "marker": "*"},
    "Optimal":                 {"color": "#000000", "ls": ":",  "marker": "p"},
}


def format_blocks(n):
    """Format cache-size in nominal 'k'/'M' units (4096 -> '4k', 81920 -> '80k')."""
    if n >= 1_000_000:
        return f"{round(n / 1_048_576)}M"
    if n >= 1_000:
        # Round to nearest power-of-two-ish nominal size used by the harness.
        return f"{round(n / 1024)}k"
    return f"{n}"


def plot_trace(trace_name, display_name, trace_data, output_dir):
    cache_sizes = sorted(cs for cs in trace_data if cs in ALLOWED_CACHE_SIZES)

    algorithms = set()
    for cs_data in trace_data.values():
        algorithms.update(cs_data.keys())
    sorted_algos = [a for a in LOCAL_ALGORITHM_ORDER
                    if a in algorithms and a not in HIDDEN_ALGORITHMS]

    fig, ax = plt.subplots(figsize=(11, 6.5))

    for algo in sorted_algos:
        sizes, ratios = [], []
        for cs in cache_sizes:
            if algo in trace_data[cs]:
                sizes.append(cs)
                ratios.append(trace_data[cs][algo]["ratio"] * 100)
        if not sizes:
            continue
        style = ALGO_STYLE.get(algo, {"color": None, "ls": "-", "marker": "o"})
        ax.plot(
            sizes, ratios,
            label=get_algorithm_display_name(algo),
            color=style["color"],
            linestyle=style["ls"],
            marker=style["marker"],
            markersize=9,
            markeredgecolor="white",
            markeredgewidth=1.2,
            linewidth=2.4,
            alpha=0.95,
        )

    ax.set_xscale("log")
    ax.set_xticks(cache_sizes)
    ax.set_xticklabels(
        [format_blocks(s) for s in cache_sizes],
        rotation=30,
        ha="right",
    )
    ax.xaxis.set_minor_locator(mticker.NullLocator())
    ax.set_xlim(cache_sizes[0] * 0.9, cache_sizes[-1] * 1.1)

    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.set_xlabel("Cache size (blocks)", fontsize=18, labelpad=10)
    ax.set_ylabel("Compute saved", fontsize=18, labelpad=10)
    ax.set_title(display_name, fontsize=22, fontweight="bold", pad=14)

    ax.tick_params(axis="both", labelsize=14, length=6, width=1.1)
    ax.grid(True, which="major", axis="both", linestyle="--",
            linewidth=0.8, alpha=0.35)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(1.2)

    leg = ax.legend(
        loc="upper left",
        frameon=True,
        fontsize=13,
        ncol=1,
        handlelength=2.8,
        borderpad=0.7,
        labelspacing=0.55,
    )
    leg.get_frame().set_edgecolor("#cccccc")
    leg.get_frame().set_linewidth(0.8)
    leg.get_frame().set_alpha(0.92)

    fig.tight_layout()
    out_path = os.path.join(output_dir, f"{trace_name}_pretty.png")
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    fig.savefig(out_path.replace(".png", ".pdf"), bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out_path}")
    print(f"  wrote {out_path.replace('.png', '.pdf')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--logs-dir", default="logs")
    p.add_argument("--output-dir", default="plots")
    args = p.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans"})

    all_data = collect_all_data(args.logs_dir)
    for trace_name, display_name in TRACES.items():
        if trace_name not in all_data:
            print(f"skip {trace_name}: not found in {args.logs_dir}")
            continue
        print(f"plotting {display_name}")
        plot_trace(trace_name, display_name, all_data[trace_name], args.output_dir)


if __name__ == "__main__":
    main()
