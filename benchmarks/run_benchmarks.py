#!/usr/bin/env python3
"""Sweep parameters of every benchmark target and produce comparison plots.

Each binary in build/benchmarks/ emits a single google-benchmark JSON to
stdout. The script sweeps each binary across configured axes, caches one
JSON per (target, axis, sweep_value) under results/<target>/, then renders
plots declared in PLOTS by overlaying series from multiple targets.

Usage:
    ./benchmarks/run_benchmarks.py
    ./benchmarks/run_benchmarks.py --rerun
    ./benchmarks/run_benchmarks.py --target compare_hp
    ./benchmarks/run_benchmarks.py --plot arch
    ./benchmarks/run_benchmarks.py --skip read_ratio zipf_theta
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
import matplotlib as mpl
import matplotlib.pyplot as plt
from cycler import cycler

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

TARGETS: dict[str, dict] = {
    "bench_sas":   {"pattern": "host_n_copies"},
    "compare_shm": {"pattern": "n_parallel",
                    "shm_path": "/dev/shm/sas_compare_shm"},
}

PLOTS: dict[str, dict] = {
    "backends": {
        "targets": ["compare_hp", "compare_ebr",
                    "compare_sharded", "compare_spinlock"],
        "axes": ["num_threads", "num_keys", "read_ratio", "zipf_theta"],
    },
    "arch": {
        "targets": ["bench_sas", "compare_shm"],
        "axes": ["num_threads", "num_keys"],
        "secondary_metric": "page_walks_per_op",
        "secondary_label": "Page walks per op",
    },
    "client_overhead": {
        "targets": ["compare_hp", "bench_end_to_end"],
        "axes": ["num_threads", "num_keys", "read_ratio", "zipf_theta"],
    },
}

SERIES_STYLES: dict[str, dict] = {
    "hp":         {"color": "#1F4E79", "marker": "o"},
    "ebr":        {"color": "#F0E442", "marker": "v"},
    "sharded":    {"color": "#B85450", "marker": "s"},
    "spinlock":   {"color": "#355E3B", "marker": "^"},
    "end_to_end": {"color": "#6B4C9A", "marker": "D"},
    "sas":        {"color": "#1F4E79", "marker": "o"},
    "shm":        {"color": "#B85450", "marker": "s"},
}

SERIES_LABELS: dict[str, str] = {
    "hp":         "Lock-free (HP)",
    "ebr":        "Lock-free (EBR)",
    "sharded":    "Sharded",
    "spinlock":   "Spinlock",
    "end_to_end": "End-to-end",
    "sas":        "SAS",
    "shm":        "SHM",
}

AXIS_LABELS: dict[str, str] = {
    "num_threads": "Threads",
    "num_keys":    "Number of Keys",
    "read_ratio":  "Read Ratio",
    "zipf_theta":  "Zipfian θ",
}

OP_LABELS: dict[str, str] = {"rd": "Read", "wr": "Write"}
PERCENTILES = ("p50", "p99", "p999")


def style_for(series: str) -> dict:
    return SERIES_STYLES.get(series, {})


def label_for(series: str) -> str:
    return SERIES_LABELS.get(series, series)


def make_build() -> None:
    print("[make] build", file=sys.stderr)
    subprocess.run(
        ["make", "build"], cwd=REPO_ROOT, check=True,
        stdout=subprocess.DEVNULL, stderr=sys.stderr,
    )


def discover_targets() -> list[str]:
    if not BUILD_BENCH_DIR.exists():
        return []
    out = []
    for p in sorted(BUILD_BENCH_DIR.iterdir()):
        if (p.name.startswith("compare_") and p.is_file()
                and not p.suffix and os.access(p, os.X_OK)):
            out.append(p.name)
        elif p.name.startswith("bench_") and p.suffix == ".so":
            if HOST_BIN.exists():
                out.append(p.stem)
    return out


def pattern_for(target: str) -> str:
    if target in TARGETS:
        return TARGETS[target].get("pattern", "auto")
    return "single" if target.startswith("compare_") else "plugin_single"


def target_path(target: str) -> Path:
    if target.startswith("bench_"):
        return BUILD_BENCH_DIR / f"{target}.so"
    return BUILD_BENCH_DIR / target


def exec_single(target: str, cfg: dict) -> dict:
    config_text = "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
    r = subprocess.run([str(target_path(target))], input=config_text,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {"benchmarks": []}


def exec_plugin_single(target: str, cfg: dict) -> dict:
    config_text = "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
    r = subprocess.run([str(HOST_BIN), str(target_path(target))],
                       input=config_text, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {"benchmarks": []}


def exec_host_n_copies(target: str, cfg: dict) -> dict:
    n = cfg["num_threads"]
    config_text = "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
    cmd = [str(HOST_BIN)] + [str(target_path(target))] * n
    r = subprocess.run(cmd, input=config_text, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {"benchmarks": []}


def exec_n_parallel(target: str, cfg: dict) -> dict:
    n = cfg["num_threads"]
    config_text = "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
    shm_path = TARGETS.get(target, {}).get("shm_path")
    if shm_path:
        try:
            os.remove(shm_path)
        except FileNotFoundError:
            pass
    procs = []
    for _ in range(n):
        p = subprocess.Popen([str(target_path(target))],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        procs.append(p)
    for p in procs:
        p.stdin.write(config_text)
        p.stdin.close()
    benches = []
    for p in procs:
        out = p.stdout.read()
        p.wait()
        if out.strip():
            benches.extend(json.loads(out)["benchmarks"])
    return {"context": {}, "benchmarks": benches}


EXECUTORS = {
    "single":         exec_single,
    "plugin_single":  exec_plugin_single,
    "host_n_copies":  exec_host_n_copies,
    "n_parallel":     exec_n_parallel,
}


def run_target_at_value(target: str, axis: str, value, out_path: Path) -> dict:
    if out_path.exists():
        return json.loads(out_path.read_text())
    cfg = {**DEFAULTS, axis: value}
    result = EXECUTORS[pattern_for(target)](target, cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    return result


def sweep_target(target: str, axis: str, values: list) -> list[tuple]:
    sweep_dir = RESULTS_DIR / target / axis
    sweep_dir.mkdir(parents=True, exist_ok=True)
    print(f"[sweep] {target} :: {axis} = {values}", file=sys.stderr)
    rows = []
    for v in values:
        out_path = sweep_dir / f"{axis}_{v}.json"
        cached = "cached" if out_path.exists() else "running"
        print(f"  {cached:>7}  {axis}={v}", file=sys.stderr)
        rows.append((v, run_target_at_value(target, axis, v, out_path)))
    return rows


def split_bench_name(name: str) -> tuple[str, str]:
    for sc in SCENARIO_SUFFIXES:
        suffix = "_" + sc
        if name.endswith(suffix):
            return name[:-len(suffix)], sc
    return name, "mixed"


def merge_rows(per_target_rows: dict[str, list[tuple]]) -> list[tuple]:
    by_value: dict[object, dict] = {}
    for _, rows in per_target_rows.items():
        for v, result in rows:
            by_value.setdefault(v, {"context": {}, "benchmarks": []})
            by_value[v]["benchmarks"].extend(result.get("benchmarks", []))
    return [(v, by_value[v]) for v in sorted(by_value)]


def collect_grouped(rows: list) -> dict[str, list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for _, result in rows:
        for b in result.get("benchmarks", []):
            if b.get("run_type") != "iteration":
                continue
            base = b["name"].split("/", 1)[0]
            series, scenario = split_bench_name(base)
            groups[scenario].add(series)
    return {sc: sorted(s) for sc, s in groups.items()}


def find_bench(result: dict, base_name: str) -> dict | None:
    for b in result.get("benchmarks", []):
        if (b.get("run_type") == "iteration"
                and b["name"].startswith(base_name + "/")):
            return b
    return None


def configure_style() -> None:
    palette = [s["color"] for s in SERIES_STYLES.values()] + [
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


def plot_throughput(rows: list, axis: str, scenario: str,
                    series_list: list[str], out_base: Path,
                    log_x: bool = False,
                    secondary_metric: str | None = None,
                    secondary_label: str | None = None) -> None:
    fig, ax = plt.subplots()
    plotted = False
    secondary_data: list[tuple[str, list, list]] = []
    for series in series_list:
        bench_name = series if scenario == "mixed" else f"{series}_{scenario}"
        xs, ys, sec = [], [], []
        for v, result in rows:
            b = find_bench(result, bench_name)
            if b is None:
                continue
            xs.append(v)
            ys.append(b.get("items_per_second", 0) / 1e6)
            if secondary_metric:
                sec.append(b.get(secondary_metric, 0))
        if xs:
            ax.plot(xs, ys, label=label_for(series), alpha=0.92, zorder=3,
                    **(style_for(series) or {"marker": "o"}))
            plotted = True
            if secondary_metric and any(s > 0 for s in sec):
                secondary_data.append((series, xs, sec))
    if not plotted:
        plt.close(fig)
        return
    if log_x:
        ax.set_xscale("log", base=2)
    ax.set_xlabel(AXIS_LABELS.get(axis, axis))
    ax.set_ylabel("Throughput (M ops/s)")

    if secondary_data:
        ax2 = ax.twinx()
        ax2.spines["right"].set_visible(True)
        ax2.spines["top"].set_visible(False)
        for series, xs, sec in secondary_data:
            base_style = style_for(series) or {"marker": "o"}
            sec_style = {**base_style, "marker": "x", "linestyle": "--"}
            ax2.plot(xs, sec, alpha=0.7, zorder=2, **sec_style)
        ax2.set_ylabel(secondary_label or secondary_metric)
        ax2.tick_params(axis="y", colors="0.25")
        ax2.spines["right"].set_color("0.25")
        ax2.spines["right"].set_linewidth(0.6)

    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, out_base)
    plt.close(fig)


def plot_latency(rows: list, axis: str, scenario: str, series_list: list[str],
                 op: str, percentile: str, out_base: Path,
                 log_x: bool = False) -> None:
    fig, ax = plt.subplots()
    plotted = False
    for series in series_list:
        bench_name = series if scenario == "mixed" else f"{series}_{scenario}"
        xs, ys = [], []
        for v, result in rows:
            b = find_bench(result, bench_name)
            if b is None:
                continue
            y = b.get(f"{op}_{percentile}")
            if not y:
                continue
            xs.append(v)
            ys.append(y / 1000.0)
        if xs:
            ax.plot(xs, ys, label=label_for(series), alpha=0.92, zorder=3,
                    **(style_for(series) or {"marker": "o"}))
            plotted = True
    if not plotted:
        plt.close(fig)
        return
    ax.set_yscale("log")
    if log_x:
        ax.set_xscale("log", base=2)
    ax.set_xlabel(AXIS_LABELS.get(axis, axis))
    ax.set_ylabel(f"{OP_LABELS.get(op, op)} {percentile} Latency (µs)")
    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, out_base)
    plt.close(fig)


def render_plot(plot_key: str, plot_spec: dict, axis: str,
                per_target_rows: dict[str, list[tuple]]) -> None:
    rows = merge_rows({t: per_target_rows[t] for t in plot_spec["targets"]
                       if t in per_target_rows})
    if not rows:
        return
    plot_dir = RESULTS_DIR / plot_key / axis
    plot_dir.mkdir(parents=True, exist_ok=True)
    log_x = axis in LOG_X_AXES

    grouped = collect_grouped(rows)
    sec_metric = plot_spec.get("secondary_metric")
    sec_label = plot_spec.get("secondary_label")
    for scenario, series_list in grouped.items():
        plot_throughput(rows, axis, scenario, series_list,
                        plot_dir / f"{scenario}_throughput", log_x=log_x,
                        secondary_metric=sec_metric,
                        secondary_label=sec_label)
        for op in ("rd", "wr"):
            for pct in PERCENTILES:
                plot_latency(rows, axis, scenario, series_list, op, pct,
                             plot_dir / f"{scenario}_{op}_{pct}",
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
    parser.add_argument("--plot", nargs="+", default=None,
                        help="Only render plots whose key matches one of "
                             "these names.")
    parser.add_argument("--skip", nargs="+", default=[],
                        choices=list(SWEEPS),
                        help="Sweep axes to skip.")
    args = parser.parse_args()

    if not args.no_build:
        make_build()

    if args.rerun and RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)

    for spec in TARGETS.values():
        shm_path = spec.get("shm_path")
        if shm_path:
            try:
                os.remove(shm_path)
            except FileNotFoundError:
                pass

    available = discover_targets()
    plot_specs = ({k: v for k, v in PLOTS.items() if k in args.plot}
                  if args.plot else PLOTS)
    needed = {t for spec in plot_specs.values() for t in spec["targets"]}
    targets = [t for t in available if t in needed]
    if args.target:
        targets = [t for t in targets if any(s in t for s in args.target)]
    if not targets:
        sys.exit(f"No matching targets in {BUILD_BENCH_DIR}.")

    configure_style()
    print(f"[targets] {targets}", file=sys.stderr)
    print(f"[plots]   {sorted(plot_specs)}", file=sys.stderr)

    per_target_per_axis: dict[str, dict[str, list[tuple]]] = defaultdict(dict)
    for axis, values in SWEEPS.items():
        if axis in args.skip:
            print(f"[skip] sweep {axis}", file=sys.stderr)
            continue
        if not any(axis in spec["axes"] for spec in plot_specs.values()):
            continue
        for target in targets:
            if not any(target in spec["targets"] and axis in spec["axes"]
                       for spec in plot_specs.values()):
                continue
            per_target_per_axis[target][axis] = sweep_target(target, axis,
                                                             values)

    for plot_key, spec in plot_specs.items():
        for axis in spec["axes"]:
            if axis in args.skip:
                continue
            render_plot(plot_key, spec, axis,
                        {t: per_target_per_axis[t][axis]
                         for t in spec["targets"]
                         if axis in per_target_per_axis.get(t, {})})


if __name__ == "__main__":
    main()
