#!/usr/bin/env python3
"""Visualize throughput benchmark results produced by benchmark_throughput.py.

Input file-name convention (matches Makefile):
    {trace}_cache_{size}_{algo}_concurrency_{N}_output_{M}.json

Plots produced (per trace, per output_length):
    - throughput vs cache size, subplot per concurrency, line per algorithm
    - throughput vs concurrency, subplot per cache size, line per algorithm
    - latency percentiles (avg/p50/p95/p99), subplot per cache size, bar per algorithm
    - speedup of each non-baseline algorithm over the baseline, grouped by concurrency
"""

import argparse
import json
import os
import re
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np


ALGORITHM_DISPLAY_NAMES = {
    "LRU": "LRU",
    "LCD": "LCD",
    "RandomQuickDemotion": "Random Quick Demotion",
    "RandomQuickDemotionGhost": "Random Quick Demotion (Ghost)",
    "RandomHyperbolic": "Random Hyperbolic",
}

ALGO_COLORS = {
    "LRU": "#3498db",
    "RandomQuickDemotionGhost": "#e74c3c",
    "RandomQuickDemotion": "#f39c12",
    "LCD": "#2ecc71",
    "RandomHyperbolic": "#9b59b6",
}

FILENAME_RE = re.compile(
    r"^(?P<trace>.+?)_cache_(?P<size>[^_]+)_(?P<algo>[^_]+)"
    r"_concurrency_(?P<conc>\d+)_output_(?P<out>\d+)\.json$"
)


def parse_cache_size(s):
    s = s.lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("m"):
        return int(float(s[:-1]) * 1024 * 1024)
    if s.endswith("g"):
        return int(float(s[:-1]) * 1024 * 1024 * 1024)
    return int(s)


def display_algo(algo):
    return ALGORITHM_DISPLAY_NAMES.get(algo, algo)


def load_results(dirpath):
    """Return {trace: {out_len: {conc: {cache_size: {algo: metrics}}}}}."""
    results = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    )
    if not os.path.isdir(dirpath):
        print(f"Warning: throughput directory {dirpath} does not exist")
        return results

    for fname in sorted(os.listdir(dirpath)):
        if not fname.endswith(".json"):
            continue
        m = FILENAME_RE.match(fname)
        if not m:
            print(f"Skipping (unparsed filename): {fname}")
            continue
        trace = m.group("trace")
        size = m.group("size")
        algo = m.group("algo")
        conc = int(m.group("conc"))
        out_len = int(m.group("out"))
        try:
            with open(os.path.join(dirpath, fname), "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: failed to load {fname}: {e}")
            continue
        results[trace][out_len][conc][size][algo] = data.get("metrics", {})
    return results


def _algos_in(subtree):
    algos = set()
    def walk(node):
        if isinstance(node, dict):
            if node and all(isinstance(v, (int, float)) or v is None for v in node.values()):
                # leaf metrics dict
                return
            for k, v in node.items():
                if isinstance(v, dict) and v and all(
                    not isinstance(vv, dict) for vv in v.values()
                ):
                    algos.add(k)
                else:
                    walk(v)
    walk(subtree)
    return algos


def plot_throughput_vs_cache(results, output_dir):
    for trace, by_out in sorted(results.items()):
        for out_len, by_conc in sorted(by_out.items()):
            concs = sorted(by_conc.keys())
            n = len(concs)
            fig, axes = plt.subplots(1, n, figsize=(5 * n, 5), squeeze=False)
            fig.suptitle(
                f"Throughput vs Cache Size: {trace} (output_length={out_len})",
                fontsize=14, fontweight="bold",
            )
            for idx, conc in enumerate(concs):
                ax = axes[0, idx]
                by_size = by_conc[conc]
                sizes = sorted(by_size.keys(), key=parse_cache_size)
                algos = sorted({a for s in sizes for a in by_size[s].keys()})
                for algo in algos:
                    xs, ys = [], []
                    for size in sizes:
                        m = by_size[size].get(algo, {})
                        tps = m.get("throughput_tokens_per_sec")
                        if tps is not None:
                            xs.append(size)
                            ys.append(tps)
                    if ys:
                        ax.plot(
                            xs, ys, marker="o", linewidth=2, markersize=7,
                            label=display_algo(algo),
                            color=ALGO_COLORS.get(algo),
                        )
                ax.set_title(f"Concurrency {conc}")
                ax.set_xlabel("Cache Size")
                ax.set_ylabel("Throughput (tokens/sec)")
                ax.grid(alpha=0.3)
                ax.legend()
            plt.tight_layout()
            path = os.path.join(
                output_dir, f"{trace}_output_{out_len}_throughput_vs_cache.png"
            )
            plt.savefig(path, dpi=150, bbox_inches="tight")
            print(f"Saved: {path}")
            plt.close()


def plot_throughput_vs_concurrency(results, output_dir):
    for trace, by_out in sorted(results.items()):
        for out_len, by_conc in sorted(by_out.items()):
            by_size_conc = defaultdict(lambda: defaultdict(dict))
            for conc, by_size in by_conc.items():
                for size, by_algo in by_size.items():
                    by_size_conc[size][conc] = by_algo
            sizes = sorted(by_size_conc.keys(), key=parse_cache_size)
            n = len(sizes)
            ncols = min(n, 3)
            nrows = (n + ncols - 1) // ncols if n else 1
            fig, axes = plt.subplots(
                nrows, ncols, figsize=(5 * ncols, 4 * nrows), squeeze=False
            )
            fig.suptitle(
                f"Throughput vs Concurrency: {trace} (output_length={out_len})",
                fontsize=14, fontweight="bold",
            )
            for idx, size in enumerate(sizes):
                ax = axes[idx // ncols, idx % ncols]
                concs = sorted(by_size_conc[size].keys())
                algos = sorted({a for c in concs for a in by_size_conc[size][c].keys()})
                for algo in algos:
                    xs, ys = [], []
                    for c in concs:
                        m = by_size_conc[size][c].get(algo, {})
                        tps = m.get("throughput_tokens_per_sec")
                        if tps is not None:
                            xs.append(c)
                            ys.append(tps)
                    if ys:
                        ax.plot(
                            xs, ys, marker="o", linewidth=2, markersize=7,
                            label=display_algo(algo),
                            color=ALGO_COLORS.get(algo),
                        )
                ax.set_title(f"Cache {size}")
                ax.set_xlabel("Concurrency")
                ax.set_ylabel("Throughput (tokens/sec)")
                ax.grid(alpha=0.3)
                ax.legend()
            for idx in range(n, nrows * ncols):
                axes[idx // ncols, idx % ncols].axis("off")
            plt.tight_layout()
            path = os.path.join(
                output_dir, f"{trace}_output_{out_len}_throughput_vs_concurrency.png"
            )
            plt.savefig(path, dpi=150, bbox_inches="tight")
            print(f"Saved: {path}")
            plt.close()


def plot_latency(results, output_dir):
    metrics = ["avg_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms"]
    labels = {
        "avg_latency_ms": "Avg",
        "p50_latency_ms": "P50",
        "p95_latency_ms": "P95",
        "p99_latency_ms": "P99",
    }
    for trace, by_out in sorted(results.items()):
        for out_len, by_conc in sorted(by_out.items()):
            for conc, by_size in sorted(by_conc.items()):
                sizes = sorted(by_size.keys(), key=parse_cache_size)
                algos = sorted({a for s in sizes for a in by_size[s].keys()})
                if not sizes or not algos:
                    continue
                fig, axes = plt.subplots(2, 2, figsize=(12, 8))
                fig.suptitle(
                    f"Latency: {trace} (output_length={out_len}, concurrency={conc})",
                    fontsize=13, fontweight="bold",
                )
                x = np.arange(len(sizes))
                width = 0.8 / len(algos)
                for idx, metric in enumerate(metrics):
                    ax = axes[idx // 2, idx % 2]
                    for i, algo in enumerate(algos):
                        vals = [by_size[s].get(algo, {}).get(metric, 0) for s in sizes]
                        ax.bar(
                            x + (i - (len(algos) - 1) / 2) * width, vals, width,
                            label=display_algo(algo),
                            color=ALGO_COLORS.get(algo), alpha=0.85,
                        )
                    ax.set_xticks(x)
                    ax.set_xticklabels(sizes)
                    ax.set_xlabel("Cache Size")
                    ax.set_ylabel(f"{labels[metric]} Latency (ms)")
                    ax.set_title(labels[metric])
                    ax.grid(axis="y", alpha=0.3)
                    ax.legend()
                plt.tight_layout()
                path = os.path.join(
                    output_dir,
                    f"{trace}_output_{out_len}_concurrency_{conc}_latency.png",
                )
                plt.savefig(path, dpi=150, bbox_inches="tight")
                print(f"Saved: {path}")
                plt.close()


def plot_speedup(results, output_dir, baseline="LRU"):
    for trace, by_out in sorted(results.items()):
        for out_len, by_conc in sorted(by_out.items()):
            concs = sorted(by_conc.keys())
            all_algos = set()
            for c in concs:
                for _, by_algo in by_conc[c].items():
                    all_algos.update(by_algo.keys())
            others = sorted(a for a in all_algos if a != baseline)
            if not others or baseline not in all_algos:
                continue
            for algo in others:
                fig, ax = plt.subplots(figsize=(10, 6))
                sizes_set = set()
                for c in concs:
                    sizes_set.update(by_conc[c].keys())
                sizes = sorted(sizes_set, key=parse_cache_size)
                x = np.arange(len(sizes))
                n = len(concs)
                width = 0.8 / max(n, 1)
                for i, c in enumerate(concs):
                    speedups = []
                    for s in sizes:
                        base = by_conc[c].get(s, {}).get(baseline, {}).get(
                            "throughput_tokens_per_sec", 0
                        )
                        cur = by_conc[c].get(s, {}).get(algo, {}).get(
                            "throughput_tokens_per_sec", 0
                        )
                        speedups.append(cur / base if base > 0 else float("nan"))
                    bars = ax.bar(
                        x + (i - (n - 1) / 2) * width, speedups, width,
                        label=f"concurrency={c}", alpha=0.85,
                    )
                    for bar, val in zip(bars, speedups):
                        if np.isfinite(val):
                            ax.text(
                                bar.get_x() + bar.get_width() / 2, val,
                                f"{val:.2f}x", ha="center", va="bottom", fontsize=8,
                            )
                ax.axhline(y=1.0, color="black", linestyle="--", linewidth=1)
                ax.set_xticks(x)
                ax.set_xticklabels(sizes)
                ax.set_xlabel("Cache Size")
                ax.set_ylabel(
                    f"Throughput speedup ({display_algo(algo)} / {display_algo(baseline)})"
                )
                ax.set_title(f"{trace} — output_length={out_len}")
                ax.grid(axis="y", alpha=0.3)
                ax.legend()
                plt.tight_layout()
                path = os.path.join(
                    output_dir,
                    f"{trace}_output_{out_len}_speedup_{algo}_vs_{baseline}.png",
                )
                plt.savefig(path, dpi=150, bbox_inches="tight")
                print(f"Saved: {path}")
                plt.close()


def main():
    p = argparse.ArgumentParser(description="Visualize throughput benchmark results")
    p.add_argument("--throughput-dir", default="logs/throughput",
                   help="Directory containing throughput JSON files")
    p.add_argument("--output-dir", default="plots/throughput",
                   help="Directory to save plots")
    p.add_argument("--baseline", default="LRU",
                   help="Baseline algorithm for speedup plots (default: LRU)")
    args = p.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print(f"Loading throughput results from {args.throughput_dir}")
    results = load_results(args.throughput_dir)
    if not results:
        print("No results found.")
        return

    print(f"Found results for {len(results)} traces")

    plot_throughput_vs_cache(results, args.output_dir)
    plot_throughput_vs_concurrency(results, args.output_dir)
    plot_latency(results, args.output_dir)
    plot_speedup(results, args.output_dir, baseline=args.baseline)

    print(f"\nAll plots saved to {args.output_dir}")


if __name__ == "__main__":
    main()
