#!/usr/bin/env python3
"""Aggregate repeated final-trial CSVs and produce presentation-ready plots.

The experiment runners are easiest to use when each trial writes to a separate
directory:

    experiments/final_trials/trial_1/
    experiments/final_trials/trial_2/
    experiments/final_trials/trial_3/

This script reads those directories, computes means and sample standard
deviations across trials, writes aggregate CSVs, and generates error-bar plots
for the main report/presentation figures.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import DefaultDict, Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")

try:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError as exc:  # pragma: no cover - runtime dependency guidance
    raise SystemExit("matplotlib is required: python3 -m pip install matplotlib") from exc


BENCHMARK_FILES = {
    "TCP": "tcp_cloudlab_clients.csv",
    "Two-Sided RDMA": "rdma_two_sided_clients.csv",
    "One-Sided RDMA": "rdma_one_sided_clients.csv",
    "One-Sided RDMA + Metadata": "rdma_one_sided_metadata.csv",
}

BENCHMARK_METRICS = [
    "elapsed_seconds",
    "throughput_rps",
    "mean_latency_us",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "measured_ok_responses",
    "measured_errors",
    "warmup_ok_responses",
    "warmup_errors",
]

CPU_METRICS = [
    "duration_s",
    "samples",
    "avg_process_cpu_percent",
    "max_process_cpu_percent",
    "avg_host_cpu_percent",
    "max_host_cpu_percent",
]

NETWORK_METRICS = [
    "rx_bytes_delta",
    "tx_bytes_delta",
    "total_bytes_delta",
    "rx_bytes_per_operation",
    "tx_bytes_per_operation",
    "total_bytes_per_operation",
]

MARKERS = ["o", "s", "^", "D", "v", "P"]
COLORS = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate final repeated trials and generate error-bar plots."
    )
    parser.add_argument(
        "--trials-dir",
        default="experiments/final_trials",
        help="Directory containing trial_1, trial_2, ... subdirectories.",
    )
    parser.add_argument(
        "--summary-dir",
        default="experiments/final_summary",
        help="Directory for aggregate CSVs.",
    )
    parser.add_argument(
        "--plots-dir",
        default="plots/final_summary",
        help="Directory for generated PNG plots.",
    )
    parser.add_argument(
        "--trial-glob",
        default="trial_*",
        help="Glob used inside --trials-dir to find trial directories.",
    )
    parser.add_argument(
        "--min-trials",
        type=int,
        default=2,
        help="Warn when fewer than this many samples exist for a point.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"wrote {path}")


def as_float(row: Dict[str, str], column: str) -> float:
    value = row.get(column, "").strip()
    if value == "":
        return math.nan
    return float(value)


def fmt(value: float) -> str:
    if math.isnan(value):
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6g}"


def sample_stdev(values: Sequence[float]) -> float:
    clean = [value for value in values if not math.isnan(value)]
    if len(clean) < 2:
        return 0.0
    return stdev(clean)


def aggregate_rows(
    rows: Sequence[Dict[str, str]],
    key_columns: Sequence[str],
    metric_columns: Sequence[str],
) -> List[Dict[str, object]]:
    grouped: DefaultDict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(column, "") for column in key_columns)
        grouped[key].append(row)

    output: List[Dict[str, object]] = []
    for key, group_rows in sorted(grouped.items(), key=lambda item: tuple(float(x) if x.replace(".", "", 1).isdigit() else x for x in item[0])):
        first = group_rows[0]
        out: Dict[str, object] = {column: first.get(column, "") for column in first.keys()}
        out["trial_count"] = len(group_rows)

        for metric in metric_columns:
            if metric not in first:
                continue
            values = [as_float(row, metric) for row in group_rows]
            clean = [value for value in values if not math.isnan(value)]
            if not clean:
                continue
            out[metric] = fmt(mean(clean))
            out[f"{metric}_stddev"] = fmt(sample_stdev(clean))

        output.append(out)
    return output


def collect_benchmark_rows(
    trials: Sequence[Path],
    filename: str,
    label: str,
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for trial in trials:
        path = trial / filename
        if not path.exists():
            print(f"skip: missing {path}")
            continue
        for row in read_csv(path):
            row = dict(row)
            row["trial"] = trial.name
            row["label"] = label
            rows.append(row)
    return rows


def aggregate_all(trials: Sequence[Path], summary_dir: Path) -> Dict[str, Path]:
    outputs: Dict[str, Path] = {}

    combined_benchmark_rows: List[Dict[str, str]] = []
    for label, filename in BENCHMARK_FILES.items():
        rows = collect_benchmark_rows(trials, filename, label)
        if not rows:
            continue
        combined_benchmark_rows.extend(rows)
        aggregate = aggregate_rows(rows, ["clients"], BENCHMARK_METRICS)
        out_path = summary_dir / filename
        write_csv(out_path, aggregate)
        outputs[filename] = out_path

    if combined_benchmark_rows:
        combined = aggregate_rows(combined_benchmark_rows, ["label", "clients"], BENCHMARK_METRICS)
        out_path = summary_dir / "transport_clients_summary.csv"
        write_csv(out_path, combined)
        outputs["transport_clients_summary.csv"] = out_path

    cpu_rows: List[Dict[str, str]] = []
    net_rows: List[Dict[str, str]] = []
    for trial in trials:
        cpu_path = trial / "cpu_utilization.csv"
        if cpu_path.exists():
            for row in read_csv(cpu_path):
                row = dict(row)
                row["trial"] = trial.name
                cpu_rows.append(row)
        else:
            print(f"skip: missing {cpu_path}")

        net_path = trial / "network_utilization.csv"
        if net_path.exists():
            for row in read_csv(net_path):
                row = dict(row)
                row["trial"] = trial.name
                net_rows.append(row)
        else:
            print(f"skip: missing {net_path}")

    if cpu_rows:
        aggregate = aggregate_rows(cpu_rows, ["label", "transport", "clients", "metadata"], CPU_METRICS)
        out_path = summary_dir / "cpu_utilization.csv"
        write_csv(out_path, aggregate)
        outputs["cpu_utilization.csv"] = out_path

    if net_rows:
        aggregate = aggregate_rows(net_rows, ["label", "transport", "clients", "metadata", "netdev"], NETWORK_METRICS)
        out_path = summary_dir / "network_utilization.csv"
        write_csv(out_path, aggregate)
        outputs["network_utilization.csv"] = out_path

    return outputs


def points(
    rows: Sequence[Dict[str, str]],
    label: str,
    metric: str,
) -> Tuple[List[float], List[float], List[float]]:
    selected = [row for row in rows if row.get("label") == label]
    selected.sort(key=lambda row: as_float(row, "clients"))
    xs = [as_float(row, "clients") for row in selected]
    ys = [as_float(row, metric) for row in selected]
    es = [as_float(row, f"{metric}_stddev") if f"{metric}_stddev" in row else 0.0 for row in selected]
    return xs, ys, es


def save(fig: plt.Figure, path: Path) -> None:  # type: ignore[name-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {path}")


def plot_transport_summary(summary_csv: Path, plots_dir: Path) -> None:
    rows = read_csv(summary_csv)
    labels = ["TCP", "Two-Sided RDMA", "One-Sided RDMA"]

    fig, ax = plt.subplots(figsize=(8, 5))
    for index, label in enumerate(labels):
        xs, ys, es = points(rows, label, "throughput_rps")
        ax.errorbar(
            xs,
            ys,
            yerr=es,
            marker=MARKERS[index],
            color=COLORS[index],
            linewidth=2,
            capsize=4,
            label=label,
        )
    ax.set_title("Transport Comparison: Throughput vs Clients")
    ax.set_xlabel("Clients")
    ax.set_ylabel("Throughput (requests / sec)")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda value, _: f"{value / 1e3:.0f}k"))
    ax.grid(True, alpha=0.3)
    ax.legend()
    save(fig, plots_dir / "comparison" / "throughput_vs_clients_errorbars.png")

    for metric, title, filename in [
        ("mean_latency_us", "Mean Latency", "mean_latency_vs_clients_errorbars.png"),
        ("p99_latency_us", "P99 Latency", "p99_latency_vs_clients_errorbars.png"),
    ]:
        fig, ax = plt.subplots(figsize=(8, 5))
        for index, label in enumerate(labels):
            xs, ys, es = points(rows, label, metric)
            ax.errorbar(
                xs,
                ys,
                yerr=es,
                marker=MARKERS[index],
                color=COLORS[index],
                linewidth=2,
                capsize=4,
                label=label,
            )
        ax.set_title(f"Transport Comparison: {title} vs Clients")
        ax.set_xlabel("Clients")
        ax.set_ylabel("Latency (us)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        save(fig, plots_dir / "comparison" / filename)


def plot_metadata_summary(summary_csv: Path, plots_dir: Path) -> None:
    rows = read_csv(summary_csv)
    labels = ["One-Sided RDMA", "One-Sided RDMA + Metadata"]

    for metric, title, filename in [
        ("mean_latency_us", "Mean Latency", "mean_latency_vs_clients_errorbars.png"),
        ("p99_latency_us", "P99 Latency", "p99_latency_vs_clients_errorbars.png"),
        ("throughput_rps", "Throughput", "throughput_vs_clients_errorbars.png"),
    ]:
        fig, ax = plt.subplots(figsize=(8, 5))
        for index, label in enumerate(labels):
            xs, ys, es = points(rows, label, metric)
            ax.errorbar(
                xs,
                ys,
                yerr=es,
                marker=MARKERS[index],
                color=COLORS[index],
                linewidth=2,
                capsize=4,
                label=label,
            )
        ax.set_title(f"One-Sided RDMA Metadata Overhead: {title} vs Clients")
        ax.set_xlabel("Clients")
        ax.set_ylabel("Throughput (requests / sec)" if metric == "throughput_rps" else "Latency (us)")
        if metric == "throughput_rps":
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda value, _: f"{value / 1e3:.0f}k"))
        ax.grid(True, alpha=0.3)
        ax.legend()
        save(fig, plots_dir / "metadata_overhead" / filename)


def plot_grouped_summary(
    csv_path: Path,
    plots_dir: Path,
    metric: str,
    error_metric: str,
    title: str,
    ylabel: str,
    filename: str,
) -> None:
    rows = read_csv(csv_path)
    labels = sorted({row["label"] for row in rows})

    fig, ax = plt.subplots(figsize=(8, 5))
    for index, label in enumerate(labels):
        selected = [row for row in rows if row.get("label") == label]
        selected.sort(key=lambda row: as_float(row, "clients"))
        xs = [as_float(row, "clients") for row in selected]
        ys = [as_float(row, metric) for row in selected]
        es = [as_float(row, error_metric) if error_metric in row else 0.0 for row in selected]
        ax.errorbar(
            xs,
            ys,
            yerr=es,
            marker=MARKERS[index % len(MARKERS)],
            color=COLORS[index % len(COLORS)],
            linewidth=2,
            capsize=4,
            label=label,
        )

    ax.set_title(title)
    ax.set_xlabel("Clients")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend()
    subdir = "network" if "Bytes" in title else "cpu"
    save(fig, plots_dir / subdir / filename)


def warn_low_trials(summary_path: Path, min_trials: int) -> None:
    rows = read_csv(summary_path)
    low = [row for row in rows if int(float(row.get("trial_count", "0") or 0)) < min_trials]
    if low:
        print(f"warning: {summary_path} has {len(low)} points below {min_trials} trials")


def main() -> int:
    args = parse_args()
    trials_dir = Path(args.trials_dir)
    summary_dir = Path(args.summary_dir)
    plots_dir = Path(args.plots_dir)

    trials = sorted(path for path in trials_dir.glob(args.trial_glob) if path.is_dir())
    if not trials:
        raise SystemExit(f"no trial directories found under {trials_dir}/{args.trial_glob}")

    print("Final trial directories:")
    for trial in trials:
        print(f"  {trial}")

    outputs = aggregate_all(trials, summary_dir)
    for path in outputs.values():
        warn_low_trials(path, args.min_trials)

    transport_summary = outputs.get("transport_clients_summary.csv")
    if transport_summary:
        plot_transport_summary(transport_summary, plots_dir)
        plot_metadata_summary(transport_summary, plots_dir)

    cpu_summary = outputs.get("cpu_utilization.csv")
    if cpu_summary:
        plot_grouped_summary(
            cpu_summary,
            plots_dir,
            "avg_process_cpu_percent",
            "avg_process_cpu_percent_stddev",
            "Server CPU Utilization: Average CPU vs Clients",
            "Server process CPU (% of one core)",
            "avg_server_cpu_vs_clients_errorbars.png",
        )

    network_summary = outputs.get("network_utilization.csv")
    if network_summary:
        plot_grouped_summary(
            network_summary,
            plots_dir,
            "total_bytes_per_operation",
            "total_bytes_per_operation_stddev",
            "Server NIC Bytes Per Operation: Total Bytes/Op vs Clients",
            "Total NIC bytes / operation",
            "total_bytes_per_operation_vs_clients_errorbars.png",
        )

    print("\nDone.")
    print(f"Aggregate CSVs: {summary_dir}")
    print(f"Plots: {plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
