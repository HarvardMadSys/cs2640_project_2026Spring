#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def infer_paths(json_path: Path) -> tuple[Path, str, Path]:
    json_path = json_path.resolve()
    directory = json_path.parent
    prefix = json_path.stem
    return directory, prefix, json_path


def load_fio_json(json_path: Path) -> dict:
    with json_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def choose_io_block(report: dict) -> tuple[str, dict]:
    if "read" in report and report["read"].get("total_ios", 0):
        return "read", report["read"]
    if "write" in report and report["write"].get("total_ios", 0):
        return "write", report["write"]
    if "trim" in report and report["trim"].get("total_ios", 0):
        return "trim", report["trim"]
    for key in ("read", "write", "trim"):
        if key in report:
            return key, report[key]
    raise ValueError("fio JSON does not contain a usable read/write/trim section")


def percentile_points(io_block: dict) -> tuple[list[float], list[float]]:
    percentile = io_block.get("clat_ns", {}).get("percentile", {})
    pairs = sorted((float(k), float(v) / 1000.0) for k, v in percentile.items())
    if not pairs:
        raise ValueError("no percentile data found in fio JSON")
    return [p for p, _ in pairs], [lat for _, lat in pairs]


def histogram_bins(io_block: dict) -> tuple[list[float], list[float]]:
    bins = io_block.get("clat_ns", {}).get("bins", {})
    if not bins:
        raise ValueError("no latency histogram data found in fio JSON")
    points = sorted((float(k) / 1000.0, float(v)) for k, v in bins.items())
    x = [p for p, _ in points]
    y = [c for _, c in points]
    return x, y


def read_bw_log(bw_log_path: Path) -> tuple[list[float], list[float]]:
    times_s: list[float] = []
    bw_mib_s: list[float] = []
    with bw_log_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, skipinitialspace=True)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                time_ms = float(row[0])
                bw_kib_s = float(row[1])
            except ValueError:
                continue
            times_s.append(time_ms / 1000.0)
            bw_mib_s.append(bw_kib_s / 1024.0)
    if not times_s:
        raise ValueError(f"no usable samples in {bw_log_path}")
    return times_s, bw_mib_s


def bucket_bandwidth_series(
    times_s: list[float], bw_mib_s: list[float], bucket_seconds: float | None
) -> tuple[list[float], list[float]]:
    if bucket_seconds is None:
        return times_s, bw_mib_s
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be > 0")

    bucket_sum: dict[int, float] = {}
    bucket_count: dict[int, int] = {}
    for t, bw in zip(times_s, bw_mib_s):
        bucket_idx = int(t // bucket_seconds)
        bucket_sum[bucket_idx] = bucket_sum.get(bucket_idx, 0.0) + bw
        bucket_count[bucket_idx] = bucket_count.get(bucket_idx, 0) + 1

    xs: list[float] = []
    ys: list[float] = []
    for idx in sorted(bucket_sum):
        xs.append((idx + 0.5) * bucket_seconds)
        ys.append(bucket_sum[idx] / bucket_count[idx])
    return xs, ys


def save_figure(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close()


def plot_latency_percentiles(json_path: Path, output_path: Path) -> None:
    data = load_fio_json(json_path)
    _, io_block = choose_io_block(data["jobs"][0])
    percentiles, lat_us = percentile_points(io_block)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(percentiles, lat_us, marker="o", linewidth=2)
    ax.set_title(f"Latency Percentiles: {json_path.stem}")
    ax.set_xlabel("Percentile")
    ax.set_ylabel("Completion latency (us)")
    ax.set_yscale("log")
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    ax.set_xticks(percentiles)
    ax.set_xticklabels([f"{p:g}" for p in percentiles])
    save_figure(output_path)


def plot_latency_histogram(json_path: Path, output_path: Path) -> None:
    data = load_fio_json(json_path)
    _, io_block = choose_io_block(data["jobs"][0])
    lat_us, counts = histogram_bins(io_block)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.step(lat_us, counts, where="mid", linewidth=1.8)
    ax.set_title(f"Latency Histogram: {json_path.stem}")
    ax.set_xlabel("Completion latency (us)")
    ax.set_ylabel("I/O count")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    save_figure(output_path)


def plot_bandwidth_over_time(json_path: Path, output_path: Path, bucket_seconds: float | None = None) -> None:
    directory, prefix, _ = infer_paths(json_path)
    bw_log_path = directory / f"{prefix}_bw.1.log"
    times_s, bw_mib_s = read_bw_log(bw_log_path)
    times_s, bw_mib_s = bucket_bandwidth_series(times_s, bw_mib_s, bucket_seconds)
    avg_bw = sum(bw_mib_s) / len(bw_mib_s)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(times_s, bw_mib_s, linewidth=1.8)
    ax.axhline(avg_bw, linestyle="--", linewidth=1.2, alpha=0.7, label=f"avg {avg_bw:.2f} MiB/s")
    ax.set_title(f"Bandwidth Over Time: {json_path.stem}")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Bandwidth (MiB/s)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.margins(x=0)
    ax.legend()
    save_figure(output_path)


def build_output_path(json_path: Path, suffix: str, outdir: Path | None) -> Path:
    directory = outdir if outdir is not None else json_path.parent
    return directory / f"{json_path.stem}_{suffix}.png"


def build_compare_output_path(basedir: Path, runtype: str, suffix: str, outdir: Path | None) -> Path:
    directory = outdir if outdir is not None else basedir
    return directory / f"compare_{runtype}_{suffix}.png"


def compare_percentiles(basedir: Path, runtype: str, outpath: Path) -> None:
    matches: list[tuple[str, list[float], list[float]]] = []
    for d in sorted(p for p in basedir.iterdir() if p.is_dir() and p.name.endswith(f"-{runtype}")):
        json_path = d / "results" / f"{runtype}{'4k' if 'rand' in runtype else ''}.json"
        if not json_path.is_file():
            continue
        data = load_fio_json(json_path)
        _, io_block = choose_io_block(data["jobs"][0])
        try:
            perc, lat = percentile_points(io_block)
        except ValueError:
            continue
        label = d.name.split("-", 1)[0]
        matches.append((label, perc, lat))

    if not matches:
        raise ValueError("no matching percentile data found for comparison")

    fig, ax = plt.subplots(figsize=(9, 5))
    for label, perc, lat in matches:
        ax.plot(perc, lat, marker="o", linewidth=1.6, label=label)
    ax.set_title(f"Latency Percentiles Comparison: {runtype}")
    ax.set_xlabel("Percentile")
    ax.set_ylabel("Completion latency (us)")
    ax.set_yscale("log")
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    ax.legend()
    save_figure(outpath)


def compare_histograms(basedir: Path, runtype: str, outpath: Path) -> None:
    matches: list[tuple[str, list[float], list[float]]] = []
    for d in sorted(p for p in basedir.iterdir() if p.is_dir() and p.name.endswith(f"-{runtype}")):
        json_path = d / "results" / f"{runtype}{'4k' if 'rand' in runtype else ''}.json"
        if not json_path.is_file():
            continue
        data = load_fio_json(json_path)
        _, io_block = choose_io_block(data["jobs"][0])
        try:
            x, y = histogram_bins(io_block)
        except ValueError:
            continue
        label = d.name.split("-", 1)[0]
        matches.append((label, x, y))

    if not matches:
        raise ValueError("no matching histogram data found for comparison")

    fig, ax = plt.subplots(figsize=(10, 5))
    for label, x, y in matches:
        ax.step(x, y, where="mid", linewidth=1.4, label=label)
    ax.set_title(f"Latency Histogram Comparison: {runtype}")
    ax.set_xlabel("Completion latency (us)")
    ax.set_ylabel("I/O count")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    ax.legend()
    save_figure(outpath)


def compare_bandwidth(basedir: Path, runtype: str, outpath: Path, bucket_seconds: float | None = None) -> None:
    matches: list[tuple[str, list[float], list[float]]] = []
    for d in sorted(p for p in basedir.iterdir() if p.is_dir() and p.name.endswith(f"-{runtype}")):
        json_path = d / "results" / f"{runtype}{'4k' if 'rand' in runtype else ''}.json"
        if not json_path.is_file():
            continue
        directory = json_path.parent
        prefix = json_path.stem
        try:
            times_s, bw_mib_s = read_bw_log(directory / f"{prefix}_bw.1.log")
            times_s, bw_mib_s = bucket_bandwidth_series(times_s, bw_mib_s, bucket_seconds)
        except Exception:
            continue
        label = d.name.split("-", 1)[0]
        matches.append((label, times_s, bw_mib_s))

    if not matches:
        raise ValueError("no matching bandwidth logs found for comparison")

    fig, ax = plt.subplots(figsize=(10, 5))
    for label, times_s, bw_mib_s in matches:
        avg_bw = sum(bw_mib_s) / len(bw_mib_s)
        (line,) = ax.plot(times_s, bw_mib_s, linewidth=1.6, label=f"{label} ({avg_bw:.2f} MiB/s avg)")
        ax.axhline(avg_bw, linestyle="--", linewidth=1.0, alpha=0.35, color=line.get_color())
    ax.set_title(f"Bandwidth Over Time Comparison: {runtype}")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Bandwidth (MiB/s)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.margins(x=0)
    ax.legend()
    save_figure(outpath)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate fsbench plots from fio JSON and logs.")
    parser.add_argument("input", type=str, help="Path to fio JSON (e.g. results/seqread.json) or a runtype name (e.g. seqread) to compare filesystems")
    parser.add_argument("--outdir", type=Path, default=None, help="Directory for generated PNGs (default: alongside the JSON or base dir for comparisons)")
    parser.add_argument("--basedir", type=Path, default=Path.cwd(), help="Base directory to search for filesystem-* runs when using a runtype (default: current working dir)")
    parser.add_argument("--bucket-seconds", type=float, default=None, help="Average bandwidth samples into buckets of this duration in seconds (bandwidth plots only)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("percentiles", help="Generate latency percentile chart")
    subparsers.add_parser("histogram", help="Generate latency histogram chart")
    subparsers.add_parser("bandwidth", help="Generate bandwidth-over-time chart")
    subparsers.add_parser("all", help="Generate all charts")

    args = parser.parse_args(list(argv) if argv is not None else None)
    outdir = args.outdir
    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)

    inp = args.input
    inp_path = Path(inp)

    # If input is an existing JSON file, run the per-file plotting behaviour
    if inp_path.is_file():
        json_path = inp_path
        commands = [args.command] if args.command != "all" else ["percentiles", "histogram", "bandwidth"]
        for command in commands:
            if command == "percentiles":
                plot_latency_percentiles(json_path, build_output_path(json_path, "latency_percentiles", outdir))
            elif command == "histogram":
                plot_latency_histogram(json_path, build_output_path(json_path, "latency_histogram", outdir))
            elif command == "bandwidth":
                plot_bandwidth_over_time(
                    json_path,
                    build_output_path(json_path, "bandwidth_over_time", outdir),
                    bucket_seconds=args.bucket_seconds,
                )
            else:
                raise SystemExit(f"unknown command: {command}")
        return 0

    # Otherwise treat the input as a runtype and compare across filesystems
    runtype = inp
    commands = [args.command] if args.command != "all" else ["percentiles", "histogram", "bandwidth"]
    for command in commands:
        if command == "percentiles":
            outpath = build_compare_output_path(args.basedir, runtype, "latency_percentiles", outdir)
            compare_percentiles(args.basedir, runtype, outpath)
        elif command == "histogram":
            outpath = build_compare_output_path(args.basedir, runtype, "latency_histogram", outdir)
            compare_histograms(args.basedir, runtype, outpath)
        elif command == "bandwidth":
            outpath = build_compare_output_path(args.basedir, runtype, "bandwidth_over_time", outdir)
            compare_bandwidth(args.basedir, runtype, outpath, bucket_seconds=args.bucket_seconds)
        else:
            raise SystemExit(f"unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())