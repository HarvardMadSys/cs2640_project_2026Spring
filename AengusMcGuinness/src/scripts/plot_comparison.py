#!/usr/bin/env python3
"""Generate transport comparison plots for the CS2640 final report.

Reads multiple benchmark CSVs (one per transport) and produces overlay plots
with one line per transport on the same axes.  Designed to compare TCP,
two-sided RDMA, and one-sided RDMA side-by-side.

Usage
-----
Basic three-way comparison (client count sweep):

    python3 scripts/plot_comparison.py \\
      --csv "TCP"           experiments/tcp_cloudlab_clients.csv \\
      --csv "Two-Sided RDMA" experiments/rdma_two_sided_clients.csv \\
      --csv "One-Sided RDMA" experiments/rdma_one_sided_clients.csv \\
      --x clients \\
      --outdir plots/comparison

One-sided metadata overhead comparison:

    python3 scripts/plot_comparison.py \\
      --csv "One-Sided (no metadata)"  experiments/rdma_one_sided_clients.csv \\
      --csv "One-Sided (LRU metadata)" experiments/rdma_one_sided_metadata.csv \\
      --x clients \\
      --outdir plots/one_sided_metadata \\
      --title "One-Sided RDMA: LRU Metadata Overhead"

The --csv flag can be repeated as many times as needed.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

try:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required: pip3 install matplotlib"
    ) from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class CsvAction(argparse.Action):
    """Collect (label, path) pairs from repeated --csv 'Label' path args."""
    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ARG002
        existing = getattr(namespace, self.dest, None)
        items: List[Tuple[str, str]] = list(existing) if existing else []
        # values is a 2-element list [label, path] because nargs=2.
        # Cast through list() to avoid Pyright's Optional subscript warning.
        pair = list(values or [])  # type: ignore[arg-type]
        items.append((str(pair[0]), str(pair[1])))
        setattr(namespace, self.dest, items)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Overlay benchmark results from multiple transports."
    )
    p.add_argument(
        "--csv",
        nargs=2,
        metavar=("LABEL", "PATH"),
        action=CsvAction,
        dest="csvs",
        required=True,
        help='Repeat for each transport: --csv "TCP" experiments/tcp.csv',
    )
    p.add_argument(
        "--x",
        default="clients",
        help="CSV column for the x-axis (default: clients)",
    )
    p.add_argument(
        "--outdir",
        default="plots/comparison",
        help="Output directory for PNG files (default: plots/comparison)",
    )
    p.add_argument(
        "--title",
        default="Transport Comparison",
        help="Base title for all plots",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def col(row: Dict[str, str], name: str) -> float:
    val = row.get(name, "").strip()
    if not val:
        raise KeyError(f"Column '{name}' missing or empty in row: {row}")
    return float(val)


def sorted_by(rows: List[Dict[str, str]], x_col: str) -> List[Dict[str, str]]:
    return sorted(rows, key=lambda r: col(r, x_col))


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

MARKERS = ["o", "s", "^", "D", "v", "P", "*"]
COLORS  = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"]


def save(fig: plt.Figure, path: Path) -> None:  # type: ignore[name-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {path}")


# ---------------------------------------------------------------------------
# Individual plots
# ---------------------------------------------------------------------------

def plot_throughput(
    datasets: List[Tuple[str, List[Dict[str, str]]]],
    x_col: str,
    outdir: Path,
    title: str,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, (label, rows) in enumerate(datasets):
        xs = [col(r, x_col)           for r in rows]
        ys = [col(r, "throughput_rps") for r in rows]
        ax.plot(xs, ys,
                marker=MARKERS[i % len(MARKERS)],
                color=COLORS[i % len(COLORS)],
                linewidth=2, label=label)

    ax.set_title(f"{title}: Throughput vs {x_col}")
    ax.set_xlabel(x_col.replace("_", " ").title())
    ax.set_ylabel("Throughput (requests / sec)")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v/1e3:.0f}k"))
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    save(fig, outdir / f"throughput_vs_{x_col}.png")


def plot_latency(
    datasets: List[Tuple[str, List[Dict[str, str]]]],
    x_col: str,
    percentile: str,
    outdir: Path,
    title: str,
) -> None:
    col_name = f"{percentile}_latency_us"
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, (label, rows) in enumerate(datasets):
        xs = [col(r, x_col)   for r in rows]
        ys = [col(r, col_name) for r in rows]
        ax.plot(xs, ys,
                marker=MARKERS[i % len(MARKERS)],
                color=COLORS[i % len(COLORS)],
                linewidth=2, label=label)

    pct_label = percentile.upper().replace("_", " ")
    ax.set_title(f"{title}: {pct_label} Latency vs {x_col}")
    ax.set_xlabel(x_col.replace("_", " ").title())
    ax.set_ylabel("Latency (µs)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    save(fig, outdir / f"{percentile}_latency_vs_{x_col}.png")


def plot_all_latency_percentiles(
    datasets: List[Tuple[str, List[Dict[str, str]]]],
    x_col: str,
    outdir: Path,
    title: str,
) -> None:
    """One subplot per percentile, all transports overlaid."""
    percentiles = [("p50", "p50"), ("p95", "p95"), ("p99", "p99"), ("mean", "mean")]
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True)
    axes = axes.flatten()

    for ax_idx, (pct_key, pct_label) in enumerate(percentiles):
        ax = axes[ax_idx]
        col_name = f"{pct_key}_latency_us"
        for i, (label, rows) in enumerate(datasets):
            xs = [col(r, x_col)    for r in rows]
            ys = [col(r, col_name) for r in rows]
            ax.plot(xs, ys,
                    marker=MARKERS[i % len(MARKERS)],
                    color=COLORS[i % len(COLORS)],
                    linewidth=2, label=label)
        ax.set_title(f"{pct_label.upper()} latency")
        ax.set_ylabel("µs")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    for ax in axes:
        ax.set_xlabel(x_col.replace("_", " ").title())

    fig.suptitle(f"{title}: Latency Percentiles vs {x_col}", fontsize=13)
    fig.tight_layout()
    save(fig, outdir / f"latency_percentiles_vs_{x_col}.png")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    outdir = Path(args.outdir)

    # Load and sort all datasets.
    datasets: List[Tuple[str, List[Dict[str, str]]]] = []
    for label, path in args.csvs:
        try:
            rows = load_csv(path)
        except FileNotFoundError:
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            return 1
        if not rows:
            print(f"ERROR: no rows in {path}", file=sys.stderr)
            return 1
        if args.x not in rows[0]:
            print(f"ERROR: column '{args.x}' not found in {path}", file=sys.stderr)
            return 1
        datasets.append((label, sorted_by(rows, args.x)))
        print(f"Loaded {len(rows)} rows from '{path}' as '{label}'")

    print(f"\nGenerating plots in {outdir}/")

    # Throughput overlay.
    plot_throughput(datasets, args.x, outdir, args.title)

    # Individual percentile overlays.
    for pct in ("mean", "p50", "p95", "p99"):
        plot_latency(datasets, args.x, pct, outdir, args.title)

    # Combined 2×2 latency panel.
    plot_all_latency_percentiles(datasets, args.x, outdir, args.title)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
