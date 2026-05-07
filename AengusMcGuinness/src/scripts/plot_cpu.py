#!/usr/bin/env python3
"""Plot server CPU utilization rows produced by scripts/measure_cpu.py."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import DefaultDict, Dict, List, Sequence, Tuple

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - runtime dependency guidance
    raise SystemExit(
        "matplotlib is required. Install it with: python3 -m pip install matplotlib"
    ) from exc


MARKERS = ["o", "s", "^", "D", "v", "P", "*"]
COLORS = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot benchmark server CPU utilization.")
    parser.add_argument(
        "--csv",
        required=True,
        help="Input CPU CSV produced by scripts/measure_cpu.py.",
    )
    parser.add_argument(
        "--outdir",
        default="plots/cpu",
        help="Directory where PNG files will be written.",
    )
    parser.add_argument(
        "--x",
        default="clients",
        help="CSV column to use on the x-axis.",
    )
    parser.add_argument(
        "--group",
        default="label",
        help="CSV column used to group plot lines.",
    )
    parser.add_argument(
        "--title",
        default="Server CPU Utilization",
        help="Base title for generated plots.",
    )
    return parser.parse_args()


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_float(row: Dict[str, str], column: str) -> float:
    value = row.get(column, "").strip()
    if not value:
        raise KeyError(f"missing column {column}")
    return float(value)


def aggregate(
    rows: Sequence[Dict[str, str]],
    x_column: str,
    group_column: str,
    metric: str,
) -> List[Tuple[str, List[Tuple[float, float]]]]:
    grouped: DefaultDict[Tuple[str, float], List[float]] = defaultdict(list)
    for row in rows:
        label = row.get(group_column, "").strip()
        if not label:
            raise KeyError(f"missing group column {group_column}")
        grouped[(label, as_float(row, x_column))].append(as_float(row, metric))

    by_label: DefaultDict[str, List[Tuple[float, float]]] = defaultdict(list)
    for (label, x_value), values in grouped.items():
        by_label[label].append((x_value, mean(values)))

    return [(label, sorted(points)) for label, points in sorted(by_label.items())]


def save_cpu_plot(
    datasets: Sequence[Tuple[str, List[Tuple[float, float]]]],
    x_column: str,
    outdir: Path,
    filename: str,
    title: str,
    ylabel: str,
) -> None:
    plt.figure(figsize=(8, 5))
    for index, (label, points) in enumerate(datasets):
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        plt.plot(
            xs,
            ys,
            marker=MARKERS[index % len(MARKERS)],
            color=COLORS[index % len(COLORS)],
            linewidth=2,
            label=label,
        )

    plt.title(title)
    plt.xlabel(x_column.replace("_", " ").title())
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    path = outdir / filename
    plt.savefig(path, dpi=200)
    plt.close()
    print(f"wrote {path}")


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rows = read_rows(csv_path)
    if not rows:
        raise SystemExit(f"no rows in {csv_path}")
    if args.x not in rows[0]:
        raise SystemExit(f"CSV does not contain x-axis column: {args.x}")
    if args.group not in rows[0]:
        raise SystemExit(f"CSV does not contain group column: {args.group}")

    avg = aggregate(rows, args.x, args.group, "avg_process_cpu_percent")
    max_values = aggregate(rows, args.x, args.group, "max_process_cpu_percent")

    save_cpu_plot(
        avg,
        args.x,
        outdir,
        f"avg_server_cpu_vs_{args.x}.png",
        f"{args.title}: Average CPU vs {args.x}",
        "Server process CPU (% of one core)",
    )
    save_cpu_plot(
        max_values,
        args.x,
        outdir,
        f"max_server_cpu_vs_{args.x}.png",
        f"{args.title}: Peak CPU vs {args.x}",
        "Server process CPU (% of one core)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
