#!/usr/bin/env python3
"""Plot benchmark CSV data produced by kv_benchmark.

The script generates baseline graphs for throughput and latency percentiles.
It expects one CSV row per benchmark run and can sort/group by a chosen x-axis
column, such as clients or get_ratio.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Sequence

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - runtime dependency guidance
    raise SystemExit(
        "matplotlib is required. Install it with: python3 -m pip install matplotlib"
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot kv_benchmark CSV results.")
    parser.add_argument("--csv", required=True, help="Input CSV file produced by kv_benchmark")
    parser.add_argument(
        "--outdir",
        default="plots",
        help="Directory where PNG files will be written (default: plots)",
    )
    parser.add_argument(
        "--x",
        default="clients",
        help="CSV column to use on the x-axis (default: clients)",
    )
    parser.add_argument(
        "--title-prefix",
        default="KV Benchmark",
        help="Prefix for graph titles (default: KV Benchmark)",
    )
    return parser.parse_args()


def read_rows(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def as_float(row: Dict[str, str], column: str) -> float:
    value = row.get(column, "")
    if value == "":
        raise KeyError(f"missing column {column}")
    return float(value)


def as_numeric_sorted_rows(rows: Sequence[Dict[str, str]], x_column: str) -> List[Dict[str, str]]:
    return sorted(rows, key=lambda row: as_float(row, x_column))


def ensure_outdir(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)


def plot_throughput(rows: Sequence[Dict[str, str]], x_column: str, outdir: Path, title_prefix: str) -> None:
    x_values = [as_float(row, x_column) for row in rows]
    throughput = [as_float(row, "throughput_rps") for row in rows]

    plt.figure(figsize=(8, 5))
    plt.plot(x_values, throughput, marker="o", linewidth=2)
    plt.title(f"{title_prefix}: Throughput vs {x_column}")
    plt.xlabel(x_column)
    plt.ylabel("Throughput (requests/sec)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(outdir / f"throughput_vs_{x_column}.png", dpi=200)
    plt.close()


def plot_latency_percentiles(rows: Sequence[Dict[str, str]], x_column: str, outdir: Path, title_prefix: str) -> None:
    x_values = [as_float(row, x_column) for row in rows]
    p50 = [as_float(row, "p50_latency_us") for row in rows]
    p95 = [as_float(row, "p95_latency_us") for row in rows]
    p99 = [as_float(row, "p99_latency_us") for row in rows]
    mean = [as_float(row, "mean_latency_us") for row in rows]

    plt.figure(figsize=(8, 5))
    plt.plot(x_values, mean, marker="o", linewidth=2, label="mean")
    plt.plot(x_values, p50, marker="o", linewidth=2, label="p50")
    plt.plot(x_values, p95, marker="o", linewidth=2, label="p95")
    plt.plot(x_values, p99, marker="o", linewidth=2, label="p99")
    plt.title(f"{title_prefix}: Latency vs {x_column}")
    plt.xlabel(x_column)
    plt.ylabel("Latency (microseconds)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outdir / f"latency_vs_{x_column}.png", dpi=200)
    plt.close()


def plot_error_counts(rows: Sequence[Dict[str, str]], x_column: str, outdir: Path, title_prefix: str) -> None:
    x_values = [as_float(row, x_column) for row in rows]
    measured_errors = [as_float(row, "measured_errors") for row in rows]
    warmup_errors = [as_float(row, "warmup_errors") for row in rows]

    plt.figure(figsize=(8, 5))
    plt.plot(x_values, measured_errors, marker="o", linewidth=2, label="measured errors")
    plt.plot(x_values, warmup_errors, marker="o", linewidth=2, label="warmup errors")
    plt.title(f"{title_prefix}: Errors vs {x_column}")
    plt.xlabel(x_column)
    plt.ylabel("Error count")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outdir / f"errors_vs_{x_column}.png", dpi=200)
    plt.close()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    outdir = Path(args.outdir)

    rows = read_rows(csv_path)
    if not rows:
        raise SystemExit(f"no data rows found in {csv_path}")

    if args.x not in rows[0]:
        raise SystemExit(f"CSV does not contain x-axis column: {args.x}")

    rows = as_numeric_sorted_rows(rows, args.x)
    ensure_outdir(outdir)

    plot_throughput(rows, args.x, outdir, args.title_prefix)
    plot_latency_percentiles(rows, args.x, outdir, args.title_prefix)
    plot_error_counts(rows, args.x, outdir, args.title_prefix)

    print(f"Wrote plots to {outdir}")
    print(f"- {outdir / f'throughput_vs_{args.x}.png'}")
    print(f"- {outdir / f'latency_vs_{args.x}.png'}")
    print(f"- {outdir / f'errors_vs_{args.x}.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
