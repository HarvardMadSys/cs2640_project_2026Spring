#!/usr/bin/env python3
"""Sweep parameters of every benchmark target.

Two kinds of targets are discovered in build/benchmarks/:
  - compare_*  (standalone executable): run directly.
  - bench_*.so (plugin):                run via build/host.

Each target reads a config (`key=value` lines) from stdin and writes
google-benchmark JSON to stdout (and a human-readable table to stderr); see
benchmarks/benchmark.h::run_benchmarks().

For each target, this script sweeps `num_threads`, `num_keys`, `read_ratio`,
and `zipf_theta` (others fixed at defaults). Bench names follow the
convention "<backend>" for the steady-state mixed scenario and
"<backend>_<scenario>" for everything else; the script groups names by
scenario suffix and produces one plot per (target, axis, scenario, metric).
Output goes to benchmarks/results/<target>/<axis>/<scenario>_<metric>.{png,pdf}.

Usage (from any directory):
    .venv/bin/python benchmarks/run_benchmarks.py
    .venv/bin/python benchmarks/run_benchmarks.py --rerun
    .venv/bin/python benchmarks/run_benchmarks.py --target compare_backends
    .venv/bin/python benchmarks/run_benchmarks.py --skip read_ratio zipf_theta
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_BENCH_DIR = REPO_ROOT / "build" / "benchmarks"
HOST_BIN = REPO_ROOT / "build" / "host"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULTS = {
    "num_threads": 16,
    "num_keys": 1 << 22,
    "read_ratio": 0.5,
    "zipf_theta": 0.99,
    "seed": 2640,
}

SWEEPS = {
    "num_threads": [1, 2, 4, 8, 16, 24, 32, 48, 64],
    "num_keys": [1 << k for k in range(10, 25, 2)],
    "read_ratio": [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0],
    "zipf_theta": [0.0, 0.25, 0.5, 0.75, 0.99, 1.25, 1.5],
}

LOG_X_AXES = {"num_keys"}
SCENARIO_SUFFIXES = ("fill_presized", "fill_resize")

BACKEND_STYLES: dict[str, dict] = {
    "lockfree":   {"color": "#1F4E79", "marker": "o"},
    "sharded":    {"color": "#B85450", "marker": "s"},
    "spinlock":   {"color": "#355E3B", "marker": "^"},
    "end_to_end": {"color": "#6B4C9A", "marker": "D"},
}

BACKEND_LABELS: dict[str, str] = {
    "lockfree":   "Lock-free",
    "sharded":    "Sharded",
    "spinlock":   "Spinlock",
    "end_to_end": "End-to-end",
}

AXIS_LABELS: dict[str, str] = {
    "num_threads": "Threads",
    "num_keys":    "Number of Keys",
    "read_ratio":  "Read Ratio",
    "zipf_theta":  "Zipfian θ",
}

OP_LABELS: dict[str, str] = {"rd": "Read", "wr": "Write"}


def style_for(backend: str) -> dict:
    return BACKEND_STYLES.get(backend, {})


def label_for(backend: str) -> str:
    return BACKEND_LABELS.get(backend, backend)


def make_build() -> None:
    print("[make] build", file=sys.stderr)
    subprocess.run(
        ["make", "build"], cwd=REPO_ROOT, check=True,
        stdout=subprocess.DEVNULL, stderr=sys.stderr,
    )


def discover_targets() -> list[tuple[str, list[str]]]:
    """Return [(name, cmd_argv), ...] for every benchmark target."""
    targets = []
    if not BUILD_BENCH_DIR.exists():
        return targets
    for p in sorted(BUILD_BENCH_DIR.iterdir()):
        if (p.name.startswith("compare_") and p.is_file()
                and not p.suffix and os.access(p, os.X_OK)):
            targets.append((p.name, [str(p)]))
        elif p.name.startswith("bench_") and p.suffix == ".so":
            if not HOST_BIN.exists():
                continue
            targets.append((p.stem, [str(HOST_BIN), str(p)]))
    return targets


def run_target(cmd: list[str], cfg: dict, out_path: Path) -> dict:
    """Run a target with cfg piped to stdin, save JSON to out_path."""
    if not out_path.exists():
        config_text = "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
        with out_path.open("w") as f:
            subprocess.run(
                cmd, input=config_text, stdout=f,
                stderr=subprocess.DEVNULL, text=True, check=True,
            )
    with out_path.open() as f:
        return json.load(f)


def sweep_target(name: str, cmd: list[str],
                 axis: str, values: list) -> list[tuple]:
    sweep_dir = RESULTS_DIR / name / axis
    sweep_dir.mkdir(parents=True, exist_ok=True)
    print(f"[sweep] {name} :: {axis} = {values}", file=sys.stderr)
    rows = []
    for v in values:
        cfg = {**DEFAULTS, axis: v}
        out_path = sweep_dir / f"{axis}_{v}.json"
        cached = "cached" if out_path.exists() else "running"
        print(f"  {cached:>7}  {axis}={v}", file=sys.stderr)
        rows.append((v, run_target(cmd, cfg, out_path)))
    return rows


def split_bench_name(name: str) -> tuple[str, str]:
    """'lockfree' → ('lockfree', 'mixed'); 'lockfree_fill_resize' →
    ('lockfree', 'fill_resize')."""
    for sc in SCENARIO_SUFFIXES:
        suffix = "_" + sc
        if name.endswith(suffix):
            return name[:-len(suffix)], sc
    return name, "mixed"


def collect_grouped(rows: list) -> dict[str, list[str]]:
    """Map scenario -> sorted list of distinct backend labels seen."""
    groups: dict[str, set[str]] = defaultdict(set)
    for _, result in rows:
        for b in result.get("benchmarks", []):
            if b.get("run_type") != "iteration":
                continue
            base = b["name"].split("/", 1)[0]
            backend, scenario = split_bench_name(base)
            groups[scenario].add(backend)
    return {sc: sorted(s) for sc, s in groups.items()}


def find_bench(result: dict, base_name: str) -> dict | None:
    for b in result.get("benchmarks", []):
        if (b.get("run_type") == "iteration"
                and b["name"].startswith(base_name + "/")):
            return b
    return None


def configure_paper_style() -> None:
    """Set matplotlib rcParams for paper figures."""
    import matplotlib as mpl
    from cycler import cycler

    palette = [s["color"] for s in BACKEND_STYLES.values()] + [
        "#F0E442", "#56B4E9", "#999999",
    ]

    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Liberation Sans", "Arial", "Helvetica",
                            "DejaVu Sans"],
        "font.size": 10,
        "axes.titlesize": 10,
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "axes.prop_cycle": cycler(color=palette),
        "lines.linewidth": 1.4,
        "lines.markersize": 4,
        "lines.markeredgewidth": 0,
        "axes.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "0.25",
        "xtick.color": "0.25",
        "ytick.color": "0.25",
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": "0.88",
        "grid.linestyle": "-",
        "grid.linewidth": 0.4,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "legend.frameon": False,
        "legend.handlelength": 1.6,
        "legend.handletextpad": 0.5,
        "legend.borderaxespad": 0.4,
        "figure.figsize": (5.0, 3.2),
        "figure.dpi": 110,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.06,
    })


def _save(fig, base: Path) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    out = base.with_suffix(".png")
    fig.savefig(out)
    print(f"  wrote {out.relative_to(REPO_ROOT)}", file=sys.stderr)


def _autocap_y(ax, series: list[tuple[str, list[float]]],
               outlier_factor: float = 5.0,
               headroom: float = 1.3) -> None:
    """When one series's max is `outlier_factor`+ x larger than the next, cap
    the y-axis at `headroom` x the next-largest's max so the smaller series
    are visible. Annotate which series got clipped.
    """
    series_maxes = sorted(((label, max(ys)) for label, ys in series),
                          key=lambda t: t[1])
    if len(series_maxes) < 2:
        return
    _, top_max = series_maxes[-1]
    _, next_max = series_maxes[-2]
    if top_max <= outlier_factor * next_max:
        return
    cap = headroom * next_max
    ax.set_ylim(0, cap)
    clipped = [label for label, m in series_maxes if m > cap]
    ax.text(0.98, 0.96, f"clipped: {', '.join(clipped)}",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=8, style="italic", color="dimgray")


def plot_throughput(rows: list, axis: str, scenario: str,
                    backends: list[str], out_base: Path,
                    log_x: bool = False) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    plotted = False
    for backend in backends:
        bench_name = backend if scenario == "mixed" else f"{backend}_{scenario}"
        xs, ys = [], []
        for v, result in rows:
            b = find_bench(result, bench_name)
            if b is None:
                continue
            xs.append(v)
            ys.append(b.get("items_per_second", 0) / 1e6)
        if xs:
            ax.plot(xs, ys, label=label_for(backend), alpha=0.92, zorder=3,
                    **(style_for(backend) or {"marker": "o"}))
            plotted = True
    if not plotted:
        plt.close(fig)
        return
    if log_x:
        ax.set_xscale("log", base=2)
    ax.set_xlabel(AXIS_LABELS.get(axis, axis))
    ax.set_ylabel("Throughput (M ops/s)")
    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, out_base)
    plt.close(fig)


def plot_latency(rows: list, axis: str, scenario: str, backends: list[str],
                 op: str, percentile: str, out_base: Path,
                 log_x: bool = False) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    series: list[tuple[str, list[float]]] = []
    for backend in backends:
        bench_name = backend if scenario == "mixed" else f"{backend}_{scenario}"
        xs, ys = [], []
        for v, result in rows:
            b = find_bench(result, bench_name)
            if b is None:
                continue
            y = b.get(f"{op}_{percentile}")
            if y is None:
                continue
            xs.append(v)
            ys.append(y / 1000.0)  # ns -> µs
        if xs:
            ax.plot(xs, ys, label=label_for(backend), alpha=0.92, zorder=3,
                    **(style_for(backend) or {"marker": "o"}))
            series.append((backend, ys))
    if not series:
        plt.close(fig)
        return
    _autocap_y(ax, series)
    if log_x:
        ax.set_xscale("log", base=2)
    ax.set_xlabel(AXIS_LABELS.get(axis, axis))
    ax.set_ylabel(f"{OP_LABELS.get(op, op)} {percentile} Latency (µs)")
    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, out_base)
    plt.close(fig)


PERCENTILES = ("p50", "p99", "p999")


def run_sweep(name: str, cmd: list[str], axis: str, values: list) -> None:
    rows = sweep_target(name, cmd, axis, values)
    sweep_dir = RESULTS_DIR / name / axis
    log_x = axis in LOG_X_AXES

    grouped = collect_grouped(rows)
    for scenario, backends in grouped.items():
        plot_throughput(rows, axis, scenario, backends,
                        sweep_dir / f"{scenario}_throughput", log_x=log_x)
        for op in ("rd", "wr"):
            for pct in PERCENTILES:
                plot_latency(rows, axis, scenario, backends, op, pct,
                             sweep_dir / f"{scenario}_{op}_{pct}",
                             log_x=log_x)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rerun", action="store_true",
                        help="Discard cached JSON results before running.")
    parser.add_argument("--no-build", action="store_true",
                        help="Skip `make build` before running.")
    parser.add_argument("--target", nargs="+", default=None,
                        help="Only run targets whose name contains any of "
                             "these substrings.")
    parser.add_argument("--skip", nargs="+", default=[],
                        choices=list(SWEEPS),
                        help="Sweep axes to skip.")
    args = parser.parse_args()

    if not args.no_build:
        make_build()

    targets = discover_targets()
    if args.target:
        targets = [t for t in targets
                   if any(s in t[0] for s in args.target)]
    if not targets:
        sys.exit("No benchmark targets found in build/benchmarks/.")

    if args.rerun and RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)

    configure_paper_style()

    print(f"[targets] {[n for n, _ in targets]}", file=sys.stderr)
    for axis, values in SWEEPS.items():
        if axis in args.skip:
            print(f"[skip] sweep {axis}", file=sys.stderr)
            continue
        for name, cmd in targets:
            run_sweep(name, cmd, axis, values)


if __name__ == "__main__":
    main()
