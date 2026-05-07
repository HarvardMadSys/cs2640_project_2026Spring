#!/usr/bin/env python3
"""Plot QPS, total refault, and read_MB-per-query as a function of total
thread count for the three compacted wiki variants.

Reads the JSON written by cache_miss_probe.py at
  <HERE>/cache_behavior/cache_miss_probe.json
and emits a single PDF with three subplots side by side, one per metric:

  qps              linear y axis
  total_refault    symlog y so a zero-refault variant (QC02_SS02) and a
                   50M-refault variant (QC02_SS08) both appear cleanly
  read_MB/q        symlog y for the same reason

X axis is the run's total thread count (sum of the run_def threads),
plotted on a log-2 axis since the sweep doubles thread counts.

Usage:
  python plot_cache_behavior.py
  python plot_cache_behavior.py --variant wiki_QC02_SS08
  python plot_cache_behavior.py --json /path/to/cache_miss_probe.json --out /tmp/foo.pdf
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Apply shared paper style; this is a global rule from CLAUDE.md.
sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style, PALETTE  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import (  # noqa: E402
    FixedLocator, FixedFormatter, FuncFormatter, NullLocator,
)


def _humanize(v: float, _pos=None) -> str:
    """Y-axis label formatter: 0, 1.5K, 2.3M, 1.2B."""
    if v == 0:
        return "0"
    av = abs(v)
    if av >= 1e9:
        return f"{v/1e9:.1f}B"
    if av >= 1e6:
        return f"{v/1e6:.1f}M"
    if av >= 1e3:
        return f"{v/1e3:.1f}K"
    if av >= 1:
        return f"{v:.0f}"
    return f"{v:.2f}"

HERE = Path(__file__).resolve().parent
DEFAULT_JSON = HERE / "cache_behavior" / "cache_miss_probe.json"
DEFAULT_OUT_DIR = HERE / "cache_behavior"

VARIANTS = ["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"]
MARKERS = ["o", "s", "^"]


def extract_rows(data: dict, variant_filter: str | None) -> list[dict]:
    """Pull (variant, threads, qps, refault, read_mb) tuples from the JSON.

    `threads` is the sum of run_def[*].threads for the run, so AB8
    counts as 16 and A16 counts as 16. Skips runs that don't have a
    parseable run_def (defensive).
    """
    rows: list[dict] = []
    for variant, runs in (data.get("results") or {}).items():
        if variant_filter and variant != variant_filter:
            continue
        for run_tag, summary in runs.items():
            run_def = summary.get("run_def") or []
            try:
                threads = sum(int(rd["threads"]) for rd in run_def)
            except (KeyError, TypeError, ValueError):
                continue
            if threads <= 0:
                continue
            qps = float(summary.get("qps_total") or 0.0)
            refault = int(summary.get("total_refault") or 0)
            read_bytes = float(summary.get("read_bytes_per_query") or 0.0)
            rows.append({
                "variant": variant,
                "run_tag": run_tag,
                "threads": threads,
                "qps": qps,
                "refault": refault,
                "read_mb": read_bytes / 1e6,
            })
    return rows


def plot_metrics(rows: list[dict], variants: list[str], out_pdf: Path) -> bool:
    if not rows:
        return False

    single_variant = len(variants) == 1

    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.4))

    all_threads = sorted({r["threads"] for r in rows})

    for i, var in enumerate(variants):
        var_rows = sorted([r for r in rows if r["variant"] == var],
                          key=lambda r: r["threads"])
        if not var_rows:
            continue
        threads = [r["threads"] for r in var_rows]
        qps = [r["qps"] for r in var_rows]
        ref = [r["refault"] for r in var_rows]
        rmb = [r["read_mb"] for r in var_rows]
        color = PALETTE[i % len(PALETTE)]
        marker = MARKERS[i % len(MARKERS)]

        axs[0].plot(threads, qps, marker=marker, color=color, label=var,
                    linewidth=2, markersize=6)
        axs[1].plot(threads, ref, marker=marker, color=color, label=var,
                    linewidth=2, markersize=6)
        axs[2].plot(threads, rmb, marker=marker, color=color, label=var,
                    linewidth=2, markersize=6)

    axs[0].set_title("QPS")
    axs[0].set_ylabel("queries / sec")
    axs[1].set_title("total_refault (window)")
    axs[1].set_ylabel("refault count (# pages)")
    axs[2].set_title("read_MB per query")
    axs[2].set_ylabel("read (MB / q)")

    if not single_variant:
        # symlog only when we span variants whose refault/read differ by
        # many orders of magnitude (e.g. QC02_SS02 = 0, QC02_SS08 = 50M).
        # For a single-variant figure, linear y gives clean tick labels.
        axs[1].set_yscale("symlog", linthresh=1)
        axs[2].set_yscale("symlog", linthresh=1)

    # X axis: log-2 spacing so doublings (1,2,4,8) are evenly distributed,
    # but show the numbers themselves as plain integers, not 2^N.
    tick_labels = [str(t) for t in all_threads]
    for ax in axs:
        ax.set_xlabel("total threads")
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_locator(FixedLocator(all_threads))
        ax.xaxis.set_major_formatter(FixedFormatter(tick_labels))
        ax.xaxis.set_minor_locator(NullLocator())
        # Y axis: human-readable numeric labels (0, 1.5K, 2.3M, ...) on
        # both the linear and symlog axes, and make sure the labels are
        # actually drawn on every subplot.
        ax.yaxis.set_major_formatter(FuncFormatter(_humanize))
        ax.tick_params(axis="y", which="both", labelleft=True)
        ax.tick_params(axis="x", which="both", labelbottom=True)
        ax.grid(True, which="both", linestyle="--", alpha=0.4)
        if not single_variant:
            ax.legend(loc="best", fontsize=8)

    fig.tight_layout()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf)
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="cache_miss_probe.json path")
    ap.add_argument("--variant", default=None, choices=VARIANTS,
                    help="if set, only plot this variant; else all three")
    ap.add_argument("--out", default=None,
                    help="output PDF path (default: under cache_behavior/)")
    args = ap.parse_args()

    apply_style()

    json_path = Path(args.json)
    if not json_path.is_file():
        print(f"ERROR: cannot read {json_path}")
        return 1
    with json_path.open() as f:
        data = json.load(f)

    rows = extract_rows(data, args.variant)
    if not rows:
        print(f"no rows extracted (filter={args.variant!r})")
        return 1

    variants = [args.variant] if args.variant else VARIANTS

    if args.out:
        out_pdf = Path(args.out)
    else:
        suffix = f"_{args.variant}" if args.variant else ""
        out_pdf = DEFAULT_OUT_DIR / f"cache_miss_metrics{suffix}.pdf"

    if not plot_metrics(rows, variants, out_pdf):
        print("nothing to plot")
        return 1
    print(f"wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
