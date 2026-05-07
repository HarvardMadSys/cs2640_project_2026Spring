#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def save_figure(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close()


def parse_filebench_stdout(path: Path) -> List[Tuple[str, float]]:
    text = path.read_text(encoding="utf-8", errors="ignore")

    # Find the Per-Operation Breakdown section
    m = re.search(r"Per-Operation Breakdown\n", text)
    if not m:
        raise ValueError("Per-Operation Breakdown section not found in filebench output")

    section = text[m.end() :]
    lines = section.splitlines()

    ops: List[Tuple[str, float]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # stop if we hit summary or next section (allow timestamp prefixes)
        if re.search(r"\bIO Summary\b", line, flags=re.IGNORECASE):
            break

        # Lines typically look like:
        # readfile4            27925ops      465ops/s   7.3mb/s    0.880ms/op [0.011ms - 631.574ms]
        m2 = re.match(r"^(\S+)\s+.*?([0-9]*\.?[0-9]+)ms/op", line)
        if m2:
            opname = m2.group(1)
            ms_per_op = float(m2.group(2))
            ops.append((opname, ms_per_op))

    if not ops:
        raise ValueError("no per-operation ms/op values found in filebench output")
    return ops


def parse_per_operation_iops(path: Path) -> List[Tuple[str, float]]:
    text = path.read_text(encoding="utf-8", errors="ignore")

    m = re.search(r"Per-Operation Breakdown\n", text)
    if not m:
        raise ValueError("Per-Operation Breakdown section not found in filebench output")

    section = text[m.end() :]
    lines = section.splitlines()

    ops: List[Tuple[str, float]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.search(r"\bIO Summary\b", line, flags=re.IGNORECASE):
            break

        # Example:
        # readfile4            27925ops      465ops/s   7.3mb/s    0.880ms/op [...]
        m2 = re.match(r"^(\S+)\s+\d+ops\s+([0-9]*\.?[0-9]+)ops/s\b", line)
        if m2:
            opname = m2.group(1)
            ops_per_s = float(m2.group(2))
            ops.append((opname, ops_per_s))

    if not ops:
        raise ValueError("no per-operation ops/s values found in filebench output")
    return ops


def plot_ops(path: Path, output_path: Path) -> None:
    ops = parse_filebench_stdout(path)
    names = [n for n, _ in ops]
    values = [v for _, v in ops]

    fig, ax = plt.subplots(figsize=(10, 5))
    x = list(range(len(names)))
    ax.bar(x, values, width=0.6, align="center")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_title(f"Filebench per-operation latency (ms/op): {path.stem}")
    ax.set_ylabel("ms/op")
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    save_figure(output_path)


def build_output_path(input_path: Path, suffix: str, outdir: Path | None) -> Path:
    directory = outdir if outdir is not None else input_path.parent
    return directory / f"{input_path.stem}_{suffix}.png"


def build_compare_output_path(basedir: Path, runtype: str, suffix: str, outdir: Path | None) -> Path:
    directory = outdir if outdir is not None else basedir
    return directory / f"compare_{runtype}_{suffix}.png"


def parse_io_summary_iops(path: Path) -> float:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Example: "IO Summary: 363088 ops 6050.666 ops/s ..."
    m = re.search(r"IO\s+Summary:\s+[0-9.,]+\s+ops\s+([0-9.,]+)\s+ops/s", text, flags=re.IGNORECASE)
    if not m:
        raise ValueError(f"IO Summary ops/s not found in {path}")
    return float(m.group(1).replace(",", ""))


def compare_iops(basedir: Path, runtype: str, output_path: Path) -> None:
    fs_to_iops: List[Tuple[str, float]] = []
    for d in sorted(p for p in basedir.iterdir() if p.is_dir() and p.name.endswith(f"-{runtype}")):
        stdout_path = d / "results" / "filebench.stdout.txt"
        if not stdout_path.is_file():
            continue
        try:
            total_iops = parse_io_summary_iops(stdout_path)
        except ValueError:
            continue
        fs_name = d.name.split("-", 1)[0]
        fs_to_iops.append((fs_name, total_iops))

    if not fs_to_iops:
        raise ValueError(f"no filebench IOPS data found for runtype {runtype}")

    fig, ax = plt.subplots(figsize=(12, 6))
    x = list(range(len(fs_to_iops)))
    names = [fs_name for fs_name, _ in fs_to_iops]
    values = [iops for _, iops in fs_to_iops]
    ax.bar(x, values, width=0.6, align="center")

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_title(f"Filebench overall IOPS comparison: {runtype}")
    ax.set_ylabel("ops/s")
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    save_figure(output_path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate plots from filebench stdout.")
    parser.add_argument("input", type=str, help="Path to filebench stdout, or runtype name (e.g. varmail) for cross-filesystem comparison")
    parser.add_argument("--outdir", type=Path, default=None, help="Directory for generated PNGs (default: alongside the input or base dir for comparisons)")
    parser.add_argument("--basedir", type=Path, default=Path.cwd(), help="Base directory to search for filesystem-* runs when using a runtype (default: current working dir)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ops", help="Plot ms/op for each reported operation")
    subparsers.add_parser("iops", help="Compare IO Summary ops/s across filesystems for a runtype")
    subparsers.add_parser("all", help="Generate all charts for this mode")

    args = parser.parse_args(list(argv) if argv is not None else None)
    outdir = args.outdir
    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)

    inp = args.input
    input_path = Path(inp)

    # Per-file mode
    if input_path.is_file():
        commands = [args.command] if args.command != "all" else ["ops"]
        for command in commands:
            if command == "ops":
                plot_ops(input_path, build_output_path(input_path, "filebench_ops", outdir))
            elif command == "iops":
                raise SystemExit("'iops' command expects a runtype input (e.g. varmail), not a file path")
            else:
                raise SystemExit(f"unknown command: {command}")
        return 0

    # Comparison mode (input is runtype)
    runtype = inp
    commands = [args.command] if args.command != "all" else ["iops"]
    for command in commands:
        if command == "ops":
            raise SystemExit("'ops' command expects a file path input, not a runtype")
        elif command == "iops":
            compare_iops(args.basedir, runtype, build_compare_output_path(args.basedir, runtype, "filebench_iops", outdir))
        else:
            raise SystemExit(f"unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
