#!/usr/bin/env python3
"""
Generate figures from cache simulator CSV outputs.

Reads from results/ and writes PDFs to results/figures/.
Usage: python3 scripts/plot_results.py [--results-dir results]
"""

# CSV schemas (kept in sync with src/main.cpp):
#   mrc.csv:              cache_frac, cache_size_bytes, policy, miss_ratio,
#                         byte_miss_ratio, accesses_per_sec
#   alpha_sensitivity.csv: alpha, policy, miss_ratio, byte_miss_ratio,
#                         accesses_per_sec
#   shards_mrc.csv:       sampling_rate, cache_size_objects, miss_ratio,
#                         accesses_per_sec
#   one_hit_wonder.csv:   window_frac, ohw_ratio
#   exact_mrc.csv:        cache_size_objects, miss_ratio
#   shards_error.csv:     sampling_rate, mae, max_abs_error, num_points
# Readers below tolerate older CSVs that lack accesses_per_sec (REFACTOR-03):
# the column is used only if present; existing miss-ratio plots do not depend
# on it. Throughput/Pareto plots are deferred to Phase 5 (ANAL-01).

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# Clean plot style: no grid, legend outside, readable fonts
plt.rcParams.update({
    "font.size": 11,
    "font.family": "serif",
    "axes.grid": False,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.frameon": False,
    "figure.dpi": 150,
})

POLICY_COLORS = {
    "LRU": "#1f77b4",
    "FIFO": "#ff7f0e",
    "CLOCK": "#2ca02c",
    "S3-FIFO": "#d62728",
    "SIEVE": "#9467bd",
    "W-TinyLFU": "#8c564b",
    # Phase 4 Axis C (D-11 / ABLA-01) — S3-FIFO small-queue-ratio ablation.
    # Sequential red family so the 3 variants read as related on the ablation
    # figure. S3-FIFO-10 reuses the legacy S3-FIFO hex so the ablation panel
    # is visually consistent with the main MRC/alpha figures.
    "S3-FIFO-5":  "#ff7f7f",  # lighter red (lower small-queue ratio)
    "S3-FIFO-10": "#d62728",  # matches legacy "S3-FIFO" — the default alias
    "S3-FIFO-20": "#8b0000",  # darker red (higher small-queue ratio)
    # Phase 4 Axis D (D-12 / ABLA-02) — SIEVE visited-bit ablation variant.
    # Shares the legacy "SIEVE" purple so the ablation figure reads as
    # "same policy, promotion on/off"; the plot function distinguishes the
    # two variants via linestyle (solid for SIEVE, dashed for SIEVE-NoProm).
    "SIEVE-NoProm": "#9467bd",
    # Phase 4 Axis B (D-08 / DOOR-03) — W-TinyLFU + Doorkeeper variant.
    # Shares the legacy "W-TinyLFU" brown (#8c564b) so the ablation figure
    # reads as "same policy, doorkeeper on/off"; the plot function
    # distinguishes the two variants via dashed linestyle for W-TinyLFU+DK.
    "W-TinyLFU+DK": "#8c564b",
}

POLICY_MARKERS = {
    "LRU": "o",
    "FIFO": "s",
    "CLOCK": "^",
    "S3-FIFO": "D",
    "SIEVE": "v",
    "W-TinyLFU": "P",
    # Phase 4 Axis C — distinguishable markers for the 3 S3-FIFO variants.
    # S3-FIFO-10 reuses the legacy "D" so the ablation's baseline rate reads
    # identically to the main-figure S3-FIFO line.
    "S3-FIFO-5":  "<",
    "S3-FIFO-10": "D",
    "S3-FIFO-20": ">",
    # Phase 4 Axis D — same marker as legacy SIEVE ("v"); plot_ablation_sieve
    # disambiguates via dashed linestyle for SIEVE-NoProm.
    "SIEVE-NoProm": "v",
    # Phase 4 Axis B (D-08 / DOOR-03) — W-TinyLFU+DK uses a distinct marker
    # ("X" cross) from legacy W-TinyLFU's "P" (filled plus) because X remains
    # visually distinct at small markersize against the dashed linestyle that
    # carries the DK-on/off distinction — a same-marker pair (like SIEVE's)
    # reads poorly here since the 2 W-TinyLFU lines cross each other on some
    # workload×alpha combinations.
    "W-TinyLFU+DK": "X",
}


def _has_throughput(df):
    """True if the DataFrame carries the accesses_per_sec column."""
    return "accesses_per_sec" in df.columns


def plot_mrc(results_dir, figures_dir):
    """Miss ratio vs. cache size (all 5 policies)."""
    path = os.path.join(results_dir, "mrc.csv")
    if not os.path.exists(path):
        print(f"  Skipping MRC plot: {path} not found")
        return

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for policy in df["policy"].unique():
        sub = df[df["policy"] == policy].sort_values("cache_frac")
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        ax.plot(sub["cache_frac"] * 100, sub["miss_ratio"],
                marker=marker, markersize=5, label=policy,
                color=color, linewidth=1.5)

    ax.set_xlabel("Cache Size (% of working set)")
    ax.set_ylabel("Miss Ratio")
    ax.set_title("Miss Ratio Curves")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "mrc.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_byte_mrc(results_dir, figures_dir):
    """Object miss ratio vs. byte miss ratio side by side."""
    path = os.path.join(results_dir, "mrc.csv")
    if not os.path.exists(path):
        print(f"  Skipping byte MRC plot: {path} not found")
        return

    df = pd.read_csv(path)
    if "byte_miss_ratio" not in df.columns:
        print(f"  Skipping byte MRC plot: no byte_miss_ratio column")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)

    for policy in df["policy"].unique():
        sub = df[df["policy"] == policy].sort_values("cache_frac")
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        ax1.plot(sub["cache_frac"] * 100, sub["miss_ratio"],
                 marker=marker, markersize=5, label=policy,
                 color=color, linewidth=1.5)
        ax2.plot(sub["cache_frac"] * 100, sub["byte_miss_ratio"],
                 marker=marker, markersize=5, label=policy,
                 color=color, linewidth=1.5)

    ax1.set_xlabel("Cache Size (% of working set)")
    ax1.set_ylabel("Miss Ratio")
    ax1.set_title("Object Miss Ratio")
    ax1.set_ylim(bottom=0)

    ax2.set_xlabel("Cache Size (% of working set)")
    ax2.set_title("Byte Miss Ratio")
    ax2.set_ylim(bottom=0)
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left")

    out = os.path.join(figures_dir, "byte_mrc.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_alpha_sensitivity(results_dir, figures_dir):
    """Miss ratio vs. Zipf alpha (all 5 policies)."""
    path = os.path.join(results_dir, "alpha_sensitivity.csv")
    if not os.path.exists(path):
        print(f"  Skipping alpha plot: {path} not found")
        return

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for policy in df["policy"].unique():
        sub = df[df["policy"] == policy].sort_values("alpha")
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        ax.plot(sub["alpha"], sub["miss_ratio"],
                marker=marker, markersize=5, label=policy,
                color=color, linewidth=1.5)

    ax.set_xlabel("Zipf Alpha")
    ax.set_ylabel("Miss Ratio (1% cache)")
    ax.set_title("Alpha Sensitivity")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "alpha_sensitivity.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_ohw(results_dir, figures_dir):
    """One-hit-wonder ratio vs. window length."""
    path = os.path.join(results_dir, "one_hit_wonder.csv")
    if not os.path.exists(path):
        print(f"  Skipping OHW plot: {path} not found")
        return

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df["window_frac"] * 100, df["ohw_ratio"],
            marker="o", color="#1f77b4", linewidth=1.5, markersize=6)
    ax.set_xlabel("Window Size (% of trace)")
    ax.set_ylabel("One-Hit-Wonder Ratio")
    ax.set_title("One-Hit Wonders vs. Window Length")
    ax.set_ylim(0, 1)

    out = os.path.join(figures_dir, "ohw.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_shards(results_dir, figures_dir):
    """SHARDS approximate MRC vs. exact MRC overlay."""
    shards_path = os.path.join(results_dir, "shards_mrc.csv")
    exact_path = os.path.join(results_dir, "exact_mrc.csv")

    if not os.path.exists(shards_path):
        print(f"  Skipping SHARDS plot: {shards_path} not found")
        return

    df = pd.read_csv(shards_path)

    fig, ax = plt.subplots(figsize=(7, 4.5))

    # Detect column name (cache_size_objects or legacy cache_size)
    size_col = "cache_size_objects" if "cache_size_objects" in df.columns else "cache_size"

    # Plot each sampling rate
    rate_colors = {0.001: "#ff7f0e", 0.01: "#2ca02c", 0.1: "#9467bd"}
    for rate in sorted(df["sampling_rate"].unique()):
        sub = df[df["sampling_rate"] == rate].sort_values(size_col)
        color = rate_colors.get(rate, "gray")
        ax.plot(sub[size_col], sub["miss_ratio"],
                label=f"SHARDS {rate*100:.1f}%",
                color=color, linewidth=1.2, alpha=0.8)

    # Overlay exact MRC if available
    if os.path.exists(exact_path):
        exact = pd.read_csv(exact_path)
        exact_col = "cache_size_objects" if "cache_size_objects" in exact.columns else "cache_size"
        ax.plot(exact[exact_col], exact["miss_ratio"],
                label="Exact", color="black", linewidth=2, linestyle="--")

    ax.set_xlabel("Cache Size (objects)")
    ax.set_ylabel("Miss Ratio")
    ax.set_title("SHARDS Approximate vs. Exact MRC")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "shards_mrc.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_workload(traces_dir, figures_dir, workload="congress"):
    """Size distribution histogram and endpoint type breakdown."""
    # Look for the trace CSV
    trace_path = os.path.join(traces_dir, f"{workload}_trace.csv")
    if not os.path.exists(trace_path):
        print(f"  Skipping workload plot: {trace_path} not found")
        return

    df = pd.read_csv(trace_path)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    # Size distribution histogram
    sizes = df["size"]
    ax1.hist(sizes, bins=50, color="#1f77b4", edgecolor="white", linewidth=0.3)
    ax1.axvline(sizes.median(), color="#d62728", linestyle="--", linewidth=1.2,
                label=f"Median ({int(sizes.median())} B)")
    ax1.axvline(sizes.mean(), color="#ff7f0e", linestyle="--", linewidth=1.2,
                label=f"Mean ({int(sizes.mean())} B)")
    ax1.set_xlabel("Response Size (bytes)")
    ax1.set_ylabel("Count")
    ax1.set_title("Response Size Distribution")
    ax1.legend()

    # Endpoint type breakdown
    def get_endpoint_type(key):
        if isinstance(key, str):
            return key.split("/")[0]
        return "unknown"

    df["endpoint_type"] = df["key"].apply(get_endpoint_type)
    counts = df["endpoint_type"].value_counts()
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    ax2.bar(counts.index, counts.values, color=colors[:len(counts)],
            edgecolor="white", linewidth=0.5)
    ax2.set_xlabel("Endpoint Type")
    ax2.set_ylabel("Request Count")
    ax2.set_title("Requests by Endpoint Type")

    # Add percentage labels on bars
    total = counts.sum()
    for i, (idx, val) in enumerate(counts.items()):
        ax2.text(i, val + total * 0.01, f"{val/total*100:.1f}%",
                 ha="center", fontsize=9)

    fig.tight_layout()
    out = os.path.join(figures_dir, "workload.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_shards_error(results_dir, figures_dir):
    """Bar chart of SHARDS MAE and max error by sampling rate."""
    path = os.path.join(results_dir, "shards_error.csv")
    if not os.path.exists(path):
        return  # silently skip, only exists after --shards-exact

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(5, 4))
    x = np.arange(len(df))
    width = 0.35
    labels = [f"{r*100:.1f}%" for r in df["sampling_rate"]]

    ax.bar(x - width/2, df["mae"], width, label="MAE", color="#1f77b4")
    ax.bar(x + width/2, df["max_abs_error"], width, label="Max Error", color="#d62728")
    ax.set_xlabel("Sampling Rate")
    ax.set_ylabel("Absolute Error (miss ratio)")
    ax.set_title("SHARDS Approximation Error")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()

    out = os.path.join(figures_dir, "shards_error.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_shards_convergence(results_dir, figures_dir):
    """SHARDS self-convergence MAE vs. compared rate (D-01/D-02). Annotates
    n_samples_compared and flags <200 with asterisk per D-01 caveat."""
    path = os.path.join(results_dir, "shards_convergence.csv")
    if not os.path.exists(path):
        return  # silently skip — only exists after `make shards-large`
    df = pd.read_csv(path).sort_values("compared_rate")
    if df.empty:
        return

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df["compared_rate"] * 100, df["mae"],
            marker="o", color="#1f77b4", linewidth=1.5, markersize=7)
    for _, row in df.iterrows():
        n = int(row["n_samples_compared"])
        label = f"n={n}"
        if n < 200:
            label += "*"  # D-01 caveat: below paper-recommended 200-sample floor
        ax.annotate(label,
                    xy=(row["compared_rate"] * 100, row["mae"]),
                    xytext=(0, 10), textcoords="offset points",
                    ha="center", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("Compared Sampling Rate (%, log scale)")
    ax.set_ylabel(f"MAE vs. {df['reference_rate'].iloc[0] * 100:.0f}% reference")
    ax.set_title("SHARDS Self-Convergence at 1M Accesses")
    ax.set_ylim(bottom=0)
    # Add a small footnote about the caveat marker if any rate triggers it
    if (df["n_samples_compared"] < 200).any():
        fig.text(0.5, -0.02,
                 "* compared rate below paper-recommended 200-sample floor (D-01)",
                 ha="center", fontsize=8, style="italic")

    out = os.path.join(figures_dir, "shards_convergence.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_shards_mrc_overlay(results_dir, figures_dir):
    """PITFALLS M3 money-shot: SHARDS approximations overlaid on exact MRC.
    Reads shards_mrc.csv (1M-scale, 4 rates) + exact_mrc.csv (50K oracle) +
    shards_mrc_50k.csv (50K SHARDS, for the oracle-regime overlay).
    """
    shards_path = os.path.join(results_dir, "shards_mrc.csv")
    exact_path = os.path.join(results_dir, "exact_mrc.csv")
    if not os.path.exists(shards_path):
        return  # only exists after `make shards-large`

    shards_df = pd.read_csv(shards_path)
    exact_df = pd.read_csv(exact_path) if os.path.exists(exact_path) else None
    shards50k_path = os.path.join(results_dir, "shards_mrc_50k.csv")
    shards50k_df = pd.read_csv(shards50k_path) if os.path.exists(shards50k_path) else None

    fig, ax = plt.subplots(figsize=(8, 5))

    # Exact MRC (50K oracle) — solid black baseline
    if exact_df is not None:
        exact_col = "cache_size_objects" if "cache_size_objects" in exact_df.columns else "cache_size"
        ax.plot(exact_df[exact_col], exact_df["miss_ratio"],
                color="black", linewidth=2.0, label="Exact MRC (50K oracle)",
                linestyle="-", zorder=10)

    # 50K SHARDS overlays — dotted lines, thin/transparent to distinguish from 1M
    if shards50k_df is not None:
        size_col50 = "cache_size_objects" if "cache_size_objects" in shards50k_df.columns else "cache_size"
        for rate in sorted(shards50k_df["sampling_rate"].unique()):
            sub = shards50k_df[shards50k_df["sampling_rate"] == rate].sort_values(size_col50)
            ax.plot(sub[size_col50], sub["miss_ratio"],
                    linestyle=":", linewidth=1.0, alpha=0.5,
                    label=f"SHARDS {rate*100:g}% (50K)")

    # 1M SHARDS overlays — dashed lines, full opacity
    size_col = "cache_size_objects" if "cache_size_objects" in shards_df.columns else "cache_size"
    for rate in sorted(shards_df["sampling_rate"].unique()):
        sub = shards_df[shards_df["sampling_rate"] == rate].sort_values(size_col)
        ax.plot(sub[size_col], sub["miss_ratio"],
                linestyle="--", linewidth=1.5,
                label=f"SHARDS {rate*100:g}% (1M)")

    ax.set_xlabel("Cache Size (objects)")
    ax.set_ylabel("Miss Ratio")
    ax.set_title("SHARDS Approximation vs. Exact MRC")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=9)
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "shards_mrc_overlay.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_ablation_s3fifo(figures_dir, congress_dir="results/congress",
                          court_dir="results/court"):
    """S3-FIFO small-queue-ratio ablation figure (D-11 / ABLA-01).

    2-workload-panel grid sharing y-axis: Congress | Court. Each panel plots
    miss_ratio vs. Zipf alpha for the 3 small_frac variants (S3-FIFO-5,
    S3-FIFO-10, S3-FIFO-20) at fixed 1% cache (D-13, via the simulator's
    --alpha-sweep code path that hardcodes wb/100 at src/main.cpp alpha loop).

    Reads results/{congress,court}/ablation_s3fifo.csv produced by
    `make ablation-s3fifo`. Both CSVs must exist for the figure to render;
    if either is missing the function silently skips (standard Phase 4 plot
    convention — lets `make plots` run standalone after partial sweeps).

    Writes results/<workload>/figures/ablation_s3fifo.pdf. Because the
    existing `make plots` pipeline derives `figures_dir` from `--workload`,
    this function is called once per invocation — the figure content is
    identical across workload invocations (same data, same renderer) but
    the PDF is written into each per-workload figures_dir so either
    workload's figure set is self-contained.
    """
    cong_path = os.path.join(congress_dir, "ablation_s3fifo.csv")
    court_path = os.path.join(court_dir, "ablation_s3fifo.csv")
    if not (os.path.exists(cong_path) and os.path.exists(court_path)):
        print(f"  Skipping S3-FIFO ablation plot: both "
              f"{cong_path} and {court_path} required")
        return

    c_df = pd.read_csv(cong_path); c_df["workload"] = "Congress"
    k_df = pd.read_csv(court_path); k_df["workload"] = "Court"
    df = pd.concat([c_df, k_df], ignore_index=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, wl in zip([ax1, ax2], ["Congress", "Court"]):
        sub_all = df[df["workload"] == wl]
        # Sort policies so the legend order is 5 -> 10 -> 20 rather than
        # lexicographic (which would put 10 before 5 due to string sort).
        policies_sorted = sorted(
            sub_all["policy"].unique(),
            key=lambda p: int(p.split("-")[-1]) if p.split("-")[-1].isdigit() else 0,
        )
        for policy in policies_sorted:
            sub = sub_all[sub_all["policy"] == policy].sort_values("alpha")
            color = POLICY_COLORS.get(policy, "gray")
            marker = POLICY_MARKERS.get(policy, "x")
            ax.plot(sub["alpha"], sub["miss_ratio"],
                    marker=marker, markersize=5, label=policy,
                    color=color, linewidth=1.5)
        ax.set_xlabel("Zipf Alpha")
        ax.set_title(wl)
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("Miss Ratio (1% cache)")
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left", title="small_frac")
    fig.suptitle("S3-FIFO small-queue-ratio sensitivity (D-11 / ABLA-01)")

    out = os.path.join(figures_dir, "ablation_s3fifo.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_ablation_sieve(figures_dir, congress_dir="results/congress",
                        court_dir="results/court"):
    """SIEVE visited-bit ablation figure (D-12 / ABLA-02).

    2-workload-panel grid (Congress | Court) with shared y-axis. Each panel
    plots miss_ratio vs. Zipf alpha for the 2 variants (SIEVE with lazy
    promotion-on-hit, SIEVE-NoProm without) at fixed 1% cache (D-13, via
    the simulator's --alpha-sweep path that hardcodes wb/100). The two
    variants share the same purple color (POLICY_COLORS["SIEVE"] ==
    POLICY_COLORS["SIEVE-NoProm"] == "#9467bd") so the ablation reads as
    "same policy, promotion on/off"; linestyle carries the distinction —
    solid for SIEVE, dashed for SIEVE-NoProm.

    Reads results/{congress,court}/ablation_sieve.csv produced by
    `make ablation-sieve`. Both CSVs must exist for the figure to render;
    if either is missing the function silently skips (Pattern S8 — lets
    `make plots` run standalone after partial sweeps).

    Writes results/<workload>/figures/ablation_sieve.pdf. Same figure
    content across Congress and Court invocations (the figure reads both
    CSVs regardless of which workload's figures_dir it's writing to);
    per-workload dirs get their own copy so each workload's figure set
    is self-contained.
    """
    cong_path = os.path.join(congress_dir, "ablation_sieve.csv")
    court_path = os.path.join(court_dir, "ablation_sieve.csv")
    if not (os.path.exists(cong_path) and os.path.exists(court_path)):
        print(f"  Skipping SIEVE ablation plot: both "
              f"{cong_path} and {court_path} required")
        return

    c_df = pd.read_csv(cong_path); c_df["workload"] = "Congress"
    k_df = pd.read_csv(court_path); k_df["workload"] = "Court"
    df = pd.concat([c_df, k_df], ignore_index=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, wl in zip([ax1, ax2], ["Congress", "Court"]):
        sub_all = df[df["workload"] == wl]
        # Sort so legend order is SIEVE, then SIEVE-NoProm (baseline first).
        policies_sorted = sorted(
            sub_all["policy"].unique(),
            key=lambda p: (1 if p.endswith("NoProm") else 0, p),
        )
        for policy in policies_sorted:
            sub = sub_all[sub_all["policy"] == policy].sort_values("alpha")
            color = POLICY_COLORS.get(policy, "gray")
            marker = POLICY_MARKERS.get(policy, "x")
            # D-12 visual: SIEVE solid, SIEVE-NoProm dashed (same color).
            linestyle = "--" if policy.endswith("NoProm") else "-"
            ax.plot(sub["alpha"], sub["miss_ratio"],
                    marker=marker, markersize=5, label=policy,
                    color=color, linewidth=1.5, linestyle=linestyle)
        ax.set_xlabel("Zipf Alpha")
        ax.set_title(wl)
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("Miss Ratio (1% cache)")
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left",
               title="visited-bit promotion")
    fig.suptitle("SIEVE visited-bit ablation (D-12 / ABLA-02)")

    out = os.path.join(figures_dir, "ablation_sieve.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_ablation_doorkeeper(figures_dir, congress_dir="results/congress",
                              court_dir="results/court"):
    """W-TinyLFU Doorkeeper ablation figure (D-08 / DOOR-03).

    2-workload-panel grid (Congress | Court) with shared y-axis. Each panel
    plots miss_ratio vs. Zipf alpha for the 2 W-TinyLFU variants (baseline
    without Doorkeeper vs. with Doorkeeper pre-CMS filter) at fixed 1% cache
    (D-13, via the simulator's --alpha-sweep path that hardcodes wb/100).
    The two variants share the same brown color (POLICY_COLORS["W-TinyLFU"]
    == POLICY_COLORS["W-TinyLFU+DK"] == "#8c564b") so the ablation reads as
    "same policy, doorkeeper on/off"; linestyle carries the distinction —
    solid for baseline W-TinyLFU, dashed for W-TinyLFU+DK.

    Reads results/{congress,court}/ablation_doorkeeper.csv produced by
    `make ablation-doorkeeper`. Both CSVs must exist for the figure to
    render; if either is missing the function silently skips (Pattern S8 —
    lets `make plots` run standalone after partial sweeps).

    Writes results/<workload>/figures/ablation_doorkeeper.pdf. Same figure
    content across Congress and Court invocations (the figure reads both
    CSVs regardless of which workload's figures_dir it's writing to);
    per-workload dirs get their own copy so each workload's figure set
    is self-contained.
    """
    cong_path = os.path.join(congress_dir, "ablation_doorkeeper.csv")
    court_path = os.path.join(court_dir, "ablation_doorkeeper.csv")
    if not (os.path.exists(cong_path) and os.path.exists(court_path)):
        print(f"  Skipping Doorkeeper ablation plot: both "
              f"{cong_path} and {court_path} required")
        return

    c_df = pd.read_csv(cong_path); c_df["workload"] = "Congress"
    k_df = pd.read_csv(court_path); k_df["workload"] = "Court"
    df = pd.concat([c_df, k_df], ignore_index=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, wl in zip([ax1, ax2], ["Congress", "Court"]):
        sub_all = df[df["workload"] == wl]
        # Sort so legend order is W-TinyLFU, then W-TinyLFU+DK (baseline first).
        policies_sorted = sorted(
            sub_all["policy"].unique(),
            key=lambda p: (1 if p.endswith("+DK") else 0, p),
        )
        for policy in policies_sorted:
            sub = sub_all[sub_all["policy"] == policy].sort_values("alpha")
            color = POLICY_COLORS.get(policy, "gray")
            marker = POLICY_MARKERS.get(policy, "x")
            # D-08 visual: legacy W-TinyLFU solid, +DK dashed (same color).
            linestyle = "--" if policy.endswith("+DK") else "-"
            ax.plot(sub["alpha"], sub["miss_ratio"],
                    marker=marker, markersize=5, label=policy,
                    color=color, linewidth=1.5, linestyle=linestyle)
        ax.set_xlabel("Zipf Alpha")
        ax.set_title(wl)
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("Miss Ratio (1% cache)")
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left",
               title="doorkeeper")
    fig.suptitle("W-TinyLFU Doorkeeper ablation (D-08 / DOOR-03)")

    out = os.path.join(figures_dir, "ablation_doorkeeper.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ==================== Phase 5 — Cross-Workload Comparison (ANAL-01) ====================
# The 4 functions below read Plan 05-04's aggregated CSVs and emit figures into
# results/compare/figures/ (NOT the current --workload's figures dir) per D-09.
# All 4 silently skip when aggregated CSVs are missing, matching the existing
# plot_shards_mrc_overlay partial-data pattern.
#
# Critical invariant (T-05-05-01): these functions MUST reference the locked
# POLICY_COLORS and POLICY_MARKERS dicts at lines 45-94 via .get(policy, fallback).
# Never redeclare; never override locally; never add new keys.

_COMPARE_FIGURES_DIR = os.path.join("results", "compare", "figures")
_COMPARE_AGGREGATED_DIR = os.path.join("results", "compare", "aggregated")


def _load_aggregated(stem):
    """Return (congress_df, court_df) for mrc or alpha_sensitivity.
    Returns (None, None) when either aggregated CSV is missing (graceful skip).
    """
    cong_path = os.path.join(_COMPARE_AGGREGATED_DIR, "congress", f"{stem}_aggregated.csv")
    crt_path = os.path.join(_COMPARE_AGGREGATED_DIR, "court", f"{stem}_aggregated.csv")
    if not os.path.exists(cong_path) or not os.path.exists(crt_path):
        return None, None
    return pd.read_csv(cong_path), pd.read_csv(crt_path)


def plot_compare_mrc_2panel(figures_dir=_COMPARE_FIGURES_DIR):
    """Canonical DOC-02 figure: Congress | Court MRC with ±1σ CI bands (D-03/D-04 fig 1).

    Reads results/compare/aggregated/{congress,court}/mrc_aggregated.csv.
    ±1σ bands via fill_between(mean-std, mean+std, alpha=0.2).
    """
    cong_df, crt_df = _load_aggregated("mrc")
    if cong_df is None:
        print(f"  Skipping compare_mrc_2panel: {_COMPARE_AGGREGATED_DIR} not populated")
        return
    os.makedirs(figures_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, df, title in [(ax1, cong_df, "Congress"), (ax2, crt_df, "Court")]:
        for policy in sorted(df["policy"].unique()):
            sub = df[df["policy"] == policy].sort_values("cache_frac")
            color = POLICY_COLORS.get(policy, "gray")
            marker = POLICY_MARKERS.get(policy, "x")
            ax.plot(sub["cache_frac"] * 100, sub["mean"],
                    marker=marker, markersize=5, label=policy,
                    color=color, linewidth=1.5)
            # D-03: ±1σ CI band via fill_between (std is sample stdev from Plan 05-04).
            ax.fill_between(sub["cache_frac"] * 100,
                            sub["mean"] - sub["std"],
                            sub["mean"] + sub["std"],
                            color=color, alpha=0.2)
        ax.set_xlabel("Cache Size (% of working set)")
        ax.set_title(title)
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("Miss Ratio")
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.suptitle("Cross-Workload MRC Comparison (5-seed mean ± 1σ)")

    out = os.path.join(figures_dir, "compare_mrc_2panel.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_compare_policy_delta(figures_dir=_COMPARE_FIGURES_DIR):
    """Court − Congress miss_ratio per policy vs cache_frac (D-04 fig 2).

    Answers: does this policy generalize across workloads?
    """
    cong_df, crt_df = _load_aggregated("mrc")
    if cong_df is None:
        print(f"  Skipping compare_policy_delta: aggregated CSVs missing")
        return
    os.makedirs(figures_dir, exist_ok=True)

    # Merge on (cache_frac, policy) to compute delta.
    merged = cong_df.merge(crt_df, on=["cache_frac", "policy"],
                           suffixes=("_cong", "_court"))
    merged["delta"] = merged["mean_court"] - merged["mean_cong"]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for policy in sorted(merged["policy"].unique()):
        sub = merged[merged["policy"] == policy].sort_values("cache_frac")
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        ax.plot(sub["cache_frac"] * 100, sub["delta"],
                marker=marker, markersize=5, label=policy,
                color=color, linewidth=1.5)
    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Cache Size (% of working set)")
    ax.set_ylabel("Court − Congress miss ratio")
    ax.set_title("Cross-Workload Policy Delta")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")

    out = os.path.join(figures_dir, "compare_policy_delta.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_compare_mrc_overlay(figures_dir=_COMPARE_FIGURES_DIR):
    """Single-panel overlay: solid=Congress, dashed=Court, 12 lines (D-04 fig 3).

    Denser view of workload-invariance vs workload-specific behavior.
    """
    cong_df, crt_df = _load_aggregated("mrc")
    if cong_df is None:
        print(f"  Skipping compare_mrc_overlay: aggregated CSVs missing")
        return
    os.makedirs(figures_dir, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 5))
    for policy in sorted(cong_df["policy"].unique()):
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        c_sub = cong_df[cong_df["policy"] == policy].sort_values("cache_frac")
        k_sub = crt_df[crt_df["policy"] == policy].sort_values("cache_frac")
        ax.plot(c_sub["cache_frac"] * 100, c_sub["mean"],
                marker=marker, markersize=5, color=color, linewidth=1.5,
                linestyle="-", label=f"{policy} (Congress)")
        ax.plot(k_sub["cache_frac"] * 100, k_sub["mean"],
                marker=marker, markersize=5, color=color, linewidth=1.5,
                linestyle="--", label=f"{policy} (Court)")
    ax.set_xlabel("Cache Size (% of working set)")
    ax.set_ylabel("Miss Ratio")
    ax.set_title("Cross-Workload MRC Overlay (solid=Congress, dashed=Court)")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "compare_mrc_overlay.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def plot_winner_per_regime_bar(figures_dir=_COMPARE_FIGURES_DIR):
    """Grouped bar chart: 4 regimes × 2 workloads, winning policy per cell
    (D-04 fig 4). Regime definitions per D-01:

      * Small Cache:  cache_frac == 0.001 on both workloads (miss_ratio)
      * High Skew:    mean miss_ratio averaged across α ∈ {1.0, 1.1, 1.2} per workload
      * Mixed Sizes:  Court byte_miss_ratio at cache_frac == 0.01 (single-seed
                      fallback — read from results/court/mrc.csv since Plan 05-04
                      aggregates miss_ratio, not byte_miss_ratio; Congress has
                      uniform sizes so is N/A for this regime)
      * OHW Regime:   cache_frac == 0.01 on both workloads (miss_ratio)
    """
    mrc_cong, mrc_crt = _load_aggregated("mrc")
    alpha_cong, alpha_crt = _load_aggregated("alpha_sensitivity")
    if mrc_cong is None or alpha_cong is None:
        print(f"  Skipping winner_per_regime_bar: aggregated CSVs missing")
        return

    # Mixed Sizes regime: read Court single-seed byte_miss_ratio at 1%.
    court_single_path = os.path.join("results", "court", "mrc.csv")
    court_single_df = None
    if os.path.exists(court_single_path):
        court_single_df = pd.read_csv(court_single_path)

    os.makedirs(figures_dir, exist_ok=True)

    def winner_on(df, filter_fn, value_col="mean"):
        """Return (winner_policy, winner_value) on df rows matching filter_fn,
        using argmin of `value_col` grouped by policy."""
        sub = df[df.apply(filter_fn, axis=1)]
        if sub.empty:
            return (None, float("nan"))
        per_policy = sub.groupby("policy")[value_col].mean()
        winner = per_policy.idxmin()
        return (winner, float(per_policy.min()))

    # Compute winners per regime per workload.
    regimes = [
        ("Small Cache", "small"),
        ("High Skew", "skew"),
        ("Mixed Sizes", "mixed"),
        ("OHW Regime", "ohw"),
    ]

    cong_winners = []
    cong_values = []
    crt_winners = []
    crt_values = []

    for label, key in regimes:
        if key == "small":
            cw, cv = winner_on(mrc_cong, lambda r: abs(r["cache_frac"] - 0.001) < 1e-9)
            kw, kv = winner_on(mrc_crt,  lambda r: abs(r["cache_frac"] - 0.001) < 1e-9)
        elif key == "skew":
            cw, cv = winner_on(alpha_cong,
                               lambda r: round(float(r["alpha"]), 2) in {1.0, 1.1, 1.2})
            kw, kv = winner_on(alpha_crt,
                               lambda r: round(float(r["alpha"]), 2) in {1.0, 1.1, 1.2})
        elif key == "mixed":
            # Congress N/A — uniform sizes workload per D-01.
            cw, cv = (None, float("nan"))
            if court_single_df is not None:
                court_1pct = court_single_df[
                    (court_single_df["cache_frac"].round(4) == 0.01)
                ]
                if not court_1pct.empty:
                    best_idx = court_1pct["byte_miss_ratio"].idxmin()
                    kw = court_1pct.loc[best_idx, "policy"]
                    kv = float(court_1pct.loc[best_idx, "byte_miss_ratio"])
                else:
                    kw, kv = (None, float("nan"))
            else:
                kw, kv = (None, float("nan"))
        elif key == "ohw":
            cw, cv = winner_on(mrc_cong, lambda r: abs(r["cache_frac"] - 0.01) < 1e-9)
            kw, kv = winner_on(mrc_crt,  lambda r: abs(r["cache_frac"] - 0.01) < 1e-9)
        cong_winners.append(cw)
        cong_values.append(cv)
        crt_winners.append(kw)
        crt_values.append(kv)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(regimes))
    width = 0.35

    # Bar colors: winner's POLICY_COLORS entry (gray fallback for unknown/None).
    cong_colors = [POLICY_COLORS.get(w, "lightgray") for w in cong_winners]
    crt_colors = [POLICY_COLORS.get(w, "lightgray") for w in crt_winners]
    # Replace NaN bar heights with 0 for rendering (N/A cells).
    cong_bars = [0 if np.isnan(v) else v for v in cong_values]
    crt_bars = [0 if np.isnan(v) else v for v in crt_values]

    b1 = ax.bar(x - width/2, cong_bars, width, color=cong_colors,
                edgecolor="black", linewidth=0.5, label="Congress")
    b2 = ax.bar(x + width/2, crt_bars, width, color=crt_colors,
                edgecolor="black", linewidth=0.5, label="Court", alpha=0.85)

    # Annotate each bar with the winning policy name.
    for rect, policy in zip(b1, cong_winners):
        if policy is None:
            ax.text(rect.get_x() + rect.get_width()/2, 0.005, "N/A",
                    ha="center", va="bottom", fontsize=8, style="italic")
        else:
            ax.text(rect.get_x() + rect.get_width()/2, rect.get_height(),
                    policy, ha="center", va="bottom", fontsize=8, rotation=0)
    for rect, policy in zip(b2, crt_winners):
        if policy is None:
            ax.text(rect.get_x() + rect.get_width()/2, 0.005, "N/A",
                    ha="center", va="bottom", fontsize=8, style="italic")
        else:
            ax.text(rect.get_x() + rect.get_width()/2, rect.get_height(),
                    policy, ha="center", va="bottom", fontsize=8, rotation=0)

    ax.set_xticks(x)
    ax.set_xticklabels([r[0] for r in regimes], rotation=10, ha="right")
    ax.set_ylabel("Winner Miss Ratio (lower = better)")
    ax.set_title("Winner per Regime (D-01 regime definitions; 5-seed mean)")
    ax.legend()

    out = os.path.join(figures_dir, "winner_per_regime_bar.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


def main():
    parser = argparse.ArgumentParser(description="Plot cache simulation results")
    parser.add_argument("--workload", default="congress",
                        help="Workload subdir under results/ (default: congress)")
    parser.add_argument("--results-dir", default=None,
                        help="Directory containing CSV outputs "
                             "(overrides --workload-derived path)")
    parser.add_argument("--traces-dir", default="traces",
                        help="Directory containing raw trace CSVs")
    args = parser.parse_args()

    # Derive results_dir from --workload unless --results-dir is passed
    # explicitly (D-05 back-compat).
    results_dir = args.results_dir or os.path.join("results", args.workload)

    figures_dir = os.path.join(results_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)

    print(f"Generating figures for workload '{args.workload}' from {results_dir}...")
    plot_mrc(results_dir, figures_dir)
    plot_byte_mrc(results_dir, figures_dir)
    plot_alpha_sensitivity(results_dir, figures_dir)
    plot_ohw(results_dir, figures_dir)
    plot_shards(results_dir, figures_dir)
    plot_shards_error(results_dir, figures_dir)
    plot_shards_convergence(results_dir, figures_dir)
    plot_shards_mrc_overlay(results_dir, figures_dir)
    # Phase 4 ablation figures: 2-workload panel layouts. Each reads both
    # Congress + Court ablation CSVs and writes into the current workload's
    # figures_dir, so `make plots WORKLOAD=congress` and `make plots
    # WORKLOAD=court` each refresh their own copy. Silently skipped when
    # either source CSV is missing.
    plot_ablation_s3fifo(figures_dir)
    plot_ablation_sieve(figures_dir)
    plot_ablation_doorkeeper(figures_dir)
    # Phase 5 cross-workload comparison figures (D-04). Land in results/compare/figures/
    # regardless of --workload. Silently skipped when results/compare/aggregated/
    # is missing (i.e., Plan 05-04 hasn't been run).
    plot_compare_mrc_2panel()
    plot_compare_policy_delta()
    plot_compare_mrc_overlay()
    plot_winner_per_regime_bar()
    plot_workload(args.traces_dir, figures_dir, args.workload)
    print("Done.")


if __name__ == "__main__":
    main()
