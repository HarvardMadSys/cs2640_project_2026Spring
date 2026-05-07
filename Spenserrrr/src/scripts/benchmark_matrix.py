#!/usr/bin/env python3
"""Run and aggregate the presentation benchmark matrix.

The script is intentionally manifest-driven so it can reuse earlier timestamped
benchmark directories and resume cleanly after a long run.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOG_DIR = RESULTS / "benchmark_matrix_logs"
SUMMARY_CSV = RESULTS / "benchmark_matrix_summary.csv"
MANIFEST_CSV = RESULTS / "benchmark_matrix_manifest.csv"

SIZES = [1_000_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000]
WORKLOADS = ["uniform", "high_cardinality", "many_groups", "skewed"]
REGIMES = ["warm", "cold"]

# Existing timestamped runs from the previous benchmark work. These are reused
# rather than rerun to save overnight time and avoid extra noise.
EXISTING_SYNTHETIC_RUNS: Dict[Tuple[str, int, str], str] = {
    ("uniform", 1_000_000, "warm"): "20260425_195117",
    ("high_cardinality", 1_000_000, "warm"): "20260425_195201",
    ("many_groups", 1_000_000, "warm"): "20260425_195247",
    ("skewed", 1_000_000, "warm"): "20260425_195439",
    ("uniform", 1_000_000, "cold"): "20260425_202058",
    ("high_cardinality", 1_000_000, "cold"): "20260425_203803",
    ("many_groups", 1_000_000, "cold"): "20260425_203900",
    ("skewed", 1_000_000, "cold"): "20260425_204029",
    ("uniform", 5_000_000, "warm"): "20260425_202140",
    ("uniform", 5_000_000, "cold"): "20260425_202244",
    ("uniform", 10_000_000, "cold"): "20260425_210040",
    ("uniform", 20_000_000, "cold"): "20260425_210349",
    ("uniform", 50_000_000, "cold"): "20260425_211042",
    ("uniform", 50_000_000, "warm"): "20260425_213743",
}

EXISTING_EXTRA_RUNS: List["RunInfo"] = []


@dataclass(frozen=True)
class Task:
    dataset: str
    workload: str
    rows: int
    regime: str
    run_dir: Optional[str] = None
    optional: bool = False

    @property
    def label(self) -> str:
        millions = self.rows // 1_000_000
        optional = "_optional" if self.optional else ""
        return f"{self.dataset}_{self.workload}_{millions}m_{self.regime}{optional}"


@dataclass(frozen=True)
class RunInfo:
    dataset: str
    workload: str
    rows: int
    regime: str
    run_dir: str
    status: str
    seconds: float = 0.0
    optional: bool = False


def workload_params(workload: str, rows: int) -> Tuple[int, int]:
    if workload == "high_cardinality":
        return rows, 100
    if workload == "many_groups":
        return max(rows // 10, 1), 1000
    return max(rows // 10, 1), 100


def cache_args(regime: str) -> List[str]:
    enabled = regime == "warm"
    value = "true" if enabled else "false"
    return [
        "--cache-input",
        value,
        "--cache-sketch-table",
        value,
        "--cache-measured-results",
        value,
    ]


def build_tasks() -> List[Task]:
    tasks: List[Task] = []
    for rows in SIZES:
        for workload in WORKLOADS:
            for regime in REGIMES:
                run_dir = EXISTING_SYNTHETIC_RUNS.get((workload, rows, regime))
                tasks.append(Task("synthetic", workload, rows, regime, run_dir))
    tasks.append(Task("synthetic", "uniform", 100_000_000, "cold", optional=True))
    return tasks


def command_for(task: Task) -> List[str]:
    distinct, groups = workload_params(task.workload, task.rows)
    args = [
        "run",
        "--dataset",
        "synthetic",
        "--synthetic-workload",
        task.workload,
        "--rows",
        str(task.rows),
        "--distinct",
        str(distinct),
        "--groups",
        str(groups),
        "--days",
        "30",
        "--partitions",
        "8",
        "--relative-sd",
        "0.05",
        "--theta-lg-k",
        "12",
        "--hll-lg-k",
        "12",
        "--warmups",
        "1",
        "--runs",
        "3",
        "--skip-udaf",
        "--output-root",
        "results",
    ] + cache_args(task.regime)
    return ["sbt", " ".join(args)]


def result_dirs() -> set[str]:
    return {p.name for p in RESULTS.iterdir() if p.is_dir() and (p / "benchmark_results.csv").exists()}


def valid_run_dir(run_dir: str) -> bool:
    path = RESULTS / run_dir
    return (path / "benchmark_results.csv").exists() and (path / "materialization_results.csv").exists()


def run_task(task: Task) -> RunInfo:
    if task.run_dir and valid_run_dir(task.run_dir):
        print(f"REUSE {task.label}: {task.run_dir}", flush=True)
        return RunInfo(task.dataset, task.workload, task.rows, task.regime, task.run_dir, "reused", optional=task.optional)

    before = result_dirs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{task.label}.log"
    cmd = command_for(task)
    print(f"RUN {task.label}", flush=True)
    print(" ".join(cmd), flush=True)
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
            log.flush()
        exit_code = proc.wait()
    seconds = time.monotonic() - start
    after = result_dirs()
    new_dirs = sorted(after - before)
    if exit_code != 0 or not new_dirs:
        status = f"failed_exit_{exit_code}"
        run_dir = ""
    else:
        run_dir = new_dirs[-1]
        status = "completed"
    info = RunInfo(task.dataset, task.workload, task.rows, task.regime, run_dir, status, seconds, task.optional)
    print(f"DONE {task.label}: {status} {run_dir} {seconds:.1f}s", flush=True)
    return info


def load_manifest() -> Dict[Tuple[str, str, int, str, bool], RunInfo]:
    if not MANIFEST_CSV.exists():
        return {}
    rows = read_csv(MANIFEST_CSV)
    previous: Dict[Tuple[str, str, int, str, bool], RunInfo] = {}
    for row in rows:
        key = (
            row["dataset"],
            row["workload"],
            int(row["rows"]),
            row["regime"],
            row.get("optional", "false") == "true",
        )
        previous[key] = RunInfo(
            dataset=row["dataset"],
            workload=row["workload"],
            rows=int(row["rows"]),
            regime=row["regime"],
            run_dir=row["run_dir"],
            status=row["status"],
            seconds=float(row.get("seconds", "0") or 0.0),
            optional=row.get("optional", "false") == "true",
        )
    return previous


def read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def benchmark_lookup(rows: Iterable[dict]) -> Dict[Tuple[str, str], dict]:
    return {(row["query_name"], row["method"]): row for row in rows}


def materialization_lookup(rows: Iterable[dict]) -> Dict[Tuple[str, str], dict]:
    return {(row["query_name"], row["sketch_type"]): row for row in rows}


def value(row: Optional[dict], key: str) -> str:
    if not row:
        return ""
    return row.get(key, "")


def aggregate(infos: List[RunInfo]) -> None:
    manifest_fields = ["dataset", "workload", "rows", "regime", "run_dir", "status", "seconds", "optional"]
    with MANIFEST_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_fields)
        writer.writeheader()
        for info in infos:
            writer.writerow({
                "dataset": info.dataset,
                "workload": info.workload,
                "rows": info.rows,
                "regime": info.regime,
                "run_dir": info.run_dir,
                "status": info.status,
                "seconds": f"{info.seconds:.1f}",
                "optional": str(info.optional).lower(),
            })

    fields = [
        "dataset",
        "workload",
        "rows",
        "regime",
        "run_dir",
        "query_name",
        "num_groups",
        "exact_ms",
        "exact_materialization_baseline_ms",
        "spark_approx_ms",
        "theta_raw_ms",
        "hll_raw_ms",
        "theta_materialized_query_ms",
        "hll_materialized_query_ms",
        "theta_build_ms",
        "hll_build_ms",
        "theta_size_ratio",
        "hll_size_ratio",
        "theta_median_error",
        "hll_median_error",
        "theta_p95_error",
        "hll_p95_error",
        "theta_break_even_queries",
        "hll_break_even_queries",
    ]
    output_rows: List[dict] = []
    for info in infos:
        if not info.run_dir or not valid_run_dir(info.run_dir):
            continue
        run_path = RESULTS / info.run_dir
        bench = benchmark_lookup(read_csv(run_path / "benchmark_results.csv"))
        mat = materialization_lookup(read_csv(run_path / "materialization_results.csv"))
        query_names = sorted({key[0] for key in bench.keys()} | {key[0] for key in mat.keys()})
        for query in query_names:
            exact = bench.get((query, "exact_spark_sql"))
            approx = bench.get((query, "spark_approx_count_distinct"))
            theta_raw = bench.get((query, "datasketches_theta_partitioned"))
            hll_raw = bench.get((query, "datasketches_hll_partitioned"))
            theta_mat = mat.get((query, "theta"))
            hll_mat = mat.get((query, "hll"))
            output_rows.append({
                "dataset": info.dataset,
                "workload": info.workload,
                "rows": info.rows,
                "regime": info.regime,
                "run_dir": info.run_dir,
                "query_name": query,
                "num_groups": value(exact, "num_groups") or value(hll_mat, "num_sketch_rows"),
                "exact_ms": value(exact, "runtime_ms") or value(hll_mat, "exact_query_time_ms"),
                "exact_materialization_baseline_ms": value(hll_mat, "exact_query_time_ms") or value(theta_mat, "exact_query_time_ms"),
                "spark_approx_ms": value(approx, "runtime_ms"),
                "theta_raw_ms": value(theta_raw, "runtime_ms"),
                "hll_raw_ms": value(hll_raw, "runtime_ms"),
                "theta_materialized_query_ms": value(theta_mat, "sketch_query_time_ms"),
                "hll_materialized_query_ms": value(hll_mat, "sketch_query_time_ms"),
                "theta_build_ms": value(theta_mat, "build_time_ms"),
                "hll_build_ms": value(hll_mat, "build_time_ms"),
                "theta_size_ratio": value(theta_mat, "raw_to_sketch_size_ratio"),
                "hll_size_ratio": value(hll_mat, "raw_to_sketch_size_ratio"),
                "theta_median_error": value(theta_mat, "relative_error_median"),
                "hll_median_error": value(hll_mat, "relative_error_median"),
                "theta_p95_error": value(theta_mat, "relative_error_p95"),
                "hll_p95_error": value(hll_mat, "relative_error_p95"),
                "theta_break_even_queries": value(theta_mat, "break_even_queries"),
                "hll_break_even_queries": value(hll_mat, "break_even_queries"),
            })

    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    previous = load_manifest()
    infos: List[RunInfo] = []
    for info in [
        RunInfo("taxi", "taxi", 1_000_000, "warm", "20260425_204151", "reused"),
        RunInfo("taxi", "taxi", 1_000_000, "cold", "20260425_204246", "reused"),
    ]:
        infos.append(info)

    for task in build_tasks():
        key = (task.dataset, task.workload, task.rows, task.regime, task.optional)
        if key in previous:
            prev = previous[key]
            if prev.status in {"completed", "reused"} and prev.run_dir and valid_run_dir(prev.run_dir):
                print(f"RESUME {task.label}: {prev.status} {prev.run_dir}", flush=True)
                infos.append(prev)
                aggregate(infos)
                continue
            if prev.status.startswith("failed") or prev.status.startswith("skipped"):
                print(f"RESUME {task.label}: {prev.status}", flush=True)
                infos.append(prev)
                aggregate(infos)
                continue

        if task.optional:
            elapsed = time.monotonic() - start
            if elapsed > 7 * 60 * 60:
                infos.append(RunInfo(task.dataset, task.workload, task.rows, task.regime, "", "skipped_time_limit", optional=True))
                print(f"SKIP {task.label}: elapsed {elapsed:.1f}s", flush=True)
                continue
        info = run_task(task)
        infos.append(info)
        aggregate(infos)
        if info.status.startswith("failed"):
            print(f"Continuing after failure in {task.label}. See {LOG_DIR / (task.label + '.log')}", flush=True)

    aggregate(infos)
    print(f"Wrote {SUMMARY_CSV}", flush=True)
    print(f"Wrote {MANIFEST_CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
