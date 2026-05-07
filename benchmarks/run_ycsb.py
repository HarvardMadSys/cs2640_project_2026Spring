#!/usr/bin/env python3
"""Sweep YCSB benchmarks for SAS and Lightning, render comparison bar plots.

For every (store, workload, thread-count) combination this script invokes the
corresponding `make ycsb-bench` target, parses the load/run Mops/s lines,
caches one JSON result per cell under results/ycsb/, then renders a per-
workload bar chart comparing SAS vs Lightning at each thread count.

Usage:
    ./benchmarks/run_ycsb.py
    ./benchmarks/run_ycsb.py --rerun
    ./benchmarks/run_ycsb.py --store sas
    ./benchmarks/run_ycsb.py --workload workloada workloadb
    ./benchmarks/run_ycsb.py --threads 1 24
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))
from run_benchmarks import configure_style

RESULTS_DIR = REPO_ROOT / "results" / "ycsb"
LIGHTNING_BUILD = REPO_ROOT / "external" / "lightning" / "build"
LIGHTNING_SOCKET = Path("/tmp/lightning")

WORKLOADS = ["workloada", "workloadb", "workloadc", "workloadd", "workloadf"]
THREAD_COUNTS = [1, 2, 4, 8]
STORES = ["sas", "lightning"]

DEFAULTS = {
    "YCSB_RECORDS": "10000",
    "YCSB_OPS": "1000000",
    "YCSB_BACKEND": "hybrid",
}

LINE_RE = re.compile(r"(load|run)\s+threads=\d+\s+ops=\d+\s+([\d.]+)\s+Mops/s")

STORE_STYLE = {
    "sas":       {"color": "#1F4E79", "label": "SAS"},
    "lightning": {"color": "#D98E04", "label": "Lightning"},
}

PHASE_LABELS = {"load": "Load", "run": "Run"}


def parse_output(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def make_invoke(env: dict[str, str], target: str) -> str:
    cmd = ["make", target] + [f"{k}={v}" for k, v in env.items()]
    print("[run]", " ".join(cmd), file=sys.stderr)
    r = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout)
        sys.stderr.write(r.stderr)
        raise RuntimeError(f"{target} failed (rc={r.returncode})")
    return r.stdout


def build_ycsb() -> None:
    print("[build] make ycsb", file=sys.stderr)
    subprocess.run(["make", "ycsb"], cwd=REPO_ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=sys.stderr)


def run_sas(workload: str, threads: int) -> dict[str, float]:
    out = make_invoke({
        **DEFAULTS,
        "YCSB_STORE":    "sas",
        "YCSB_WORKLOAD": workload,
        "YCSB_THREADS":  str(threads),
    }, "ycsb-bench-only")
    return parse_output(out)


def run_lightning(workload: str, threads: int) -> dict[str, float]:
    LIGHTNING_SOCKET.unlink(missing_ok=True)
    subprocess.run("rm -f /dev/shm/*", shell=True, stderr=subprocess.DEVNULL)
    daemon = subprocess.Popen(
        [str(LIGHTNING_BUILD / "store")],
        cwd=str(LIGHTNING_BUILD),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    try:
        base_env = {
            **DEFAULTS,
            "YCSB_STORE":    "lightning",
            "YCSB_WORKLOAD": workload,
            "YCSB_THREADS":  "1",
            "YCSB_NPROCS":   str(threads),
        }
        procs = []
        for i in range(threads):
            env = {**base_env, "YCSB_PROCID": str(i)}
            cmd = ["make", "ycsb-bench-only"] + [f"{k}={v}" for k, v in env.items()]
            if i == 0:
                print(f"[run × {threads}]", " ".join(cmd), file=sys.stderr)
            p = subprocess.Popen(cmd, cwd=REPO_ROOT,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            procs.append(p)
        totals: dict[str, float] = {}
        failed = False
        for i, p in enumerate(procs):
            stdout, stderr = p.communicate()
            if p.returncode != 0:
                sys.stderr.write(f"[procid={i} stdout]\n{stdout}")
                sys.stderr.write(f"[procid={i} stderr]\n{stderr}")
                failed = True
                continue
            phase = parse_output(stdout)
            for k, v in phase.items():
                totals[k] = totals.get(k, 0.0) + v
        if failed:
            raise RuntimeError("ycsb-bench failed in one or more lightning procs")
        return totals
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
        LIGHTNING_SOCKET.unlink(missing_ok=True)


def cache_path(store: str, workload: str, threads: int) -> Path:
    return RESULTS_DIR / store / workload / f"threads_{threads}.json"


def get_or_run(store: str, workload: str, threads: int, rerun: bool) -> dict:
    p = cache_path(store, workload, threads)
    if p.exists() and not rerun:
        return json.loads(p.read_text())
    fn = run_sas if store == "sas" else run_lightning
    result = fn(workload, threads)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result))
    return result


def plot_workload(workload: str, stores: list[str], threads_list: list[int],
                  cells: dict, out_base: Path) -> None:
    for phase in ("run", "load"):
        fig, ax = plt.subplots()
        x = np.arange(len(threads_list))
        width = 0.8 / max(1, len(stores))
        plotted = False
        for i, store in enumerate(stores):
            vals = [cells.get((store, t), {}).get(phase, 0.0) for t in threads_list]
            if not any(v > 0 for v in vals):
                continue
            offset = (i - (len(stores) - 1) / 2) * width
            style = STORE_STYLE.get(store, {"color": None, "label": store})
            ax.bar(x + offset, vals, width, label=style["label"],
                   color=style["color"], zorder=3)
            plotted = True
        if not plotted:
            plt.close(fig)
            continue
        ax.set_xticks(x)
        ax.set_xticklabels([str(t) for t in threads_list])
        ax.set_xlabel("Threads")
        ax.set_ylabel("Throughput (M ops/s)")
        ax.set_title(f"{workload.replace('workload', 'YCSB-').upper()} "
                     f"({PHASE_LABELS[phase]})")
        ax.legend(loc="best")
        fig.tight_layout()
        out = out_base.with_name(f"{out_base.stem}_{phase}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out)
        print(f"[plot] {out.relative_to(REPO_ROOT)}", file=sys.stderr)
        plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--rerun", action="store_true",
                   help="Re-run cached cells.")
    p.add_argument("--store", choices=STORES, action="append",
                   help="Restrict to these stores (default: both).")
    p.add_argument("--workload", choices=WORKLOADS, action="append",
                   help="Restrict to these workloads (default: a/b/c/d/f).")
    p.add_argument("--threads", type=int, action="append",
                   help="Restrict to these thread counts (default: 1, 24, 64).")
    args = p.parse_args()

    stores = args.store or STORES
    workloads = args.workload or WORKLOADS
    threads_list = args.threads or THREAD_COUNTS

    configure_style()
    build_ycsb()

    cells: dict[tuple, dict] = {}
    for workload in workloads:
        for store in stores:
            for threads in threads_list:
                cells[(store, workload, threads)] = get_or_run(
                    store, workload, threads, args.rerun)

    for workload in workloads:
        per = {(s, t): cells.get((s, workload, t), {})
               for s in stores for t in threads_list}
        plot_workload(workload, stores, threads_list, per,
                      RESULTS_DIR / workload)


if __name__ == "__main__":
    main()
