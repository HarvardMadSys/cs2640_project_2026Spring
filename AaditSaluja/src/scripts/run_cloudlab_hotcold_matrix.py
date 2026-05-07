#!/usr/bin/env python3
"""Run the CloudLab oracle hot/cold matrix and summarize it.

The runner is intentionally serial. It randomizes the order of scenario/variant
runs to reduce run-order bias, but does not run benchmarks concurrently.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


SCENARIOS = [
    (0.70, 0.00),
    (0.70, 0.10),
    (0.70, 0.20),
    (0.90, 0.00),
    (0.90, 0.10),
    (0.90, 0.20),
]

BASE_STORAGE_OPTS = [
    "hot_prefixes=hot",
    "virtual_cold_dirs=true",
    "index_batch_bytes=1048576",
    "index_batch_records=512",
    "cold_data_batch_bytes=1048576",
    "cold_data_backend=cephfs",
    "ceph_layout_scope=segments",
]

LAYOUT_STORAGE_OPTS = [
    "ceph_stripe_unit=1048576",
    "ceph_stripe_count=1",
    "ceph_object_size=1048576",
]


@dataclass(frozen=True)
class Job:
    scenario: str
    cold_fraction: float
    cold_access_fraction: float
    variant: str
    repeat: int
    seed: int


def variant_args(variant: str) -> list[str]:
    if variant == "native":
        return ["--storage", "native"]
    if variant == "hybrid":
        return storage_args(BASE_STORAGE_OPTS)
    if variant == "hybrid_layout":
        return storage_args(BASE_STORAGE_OPTS + LAYOUT_STORAGE_OPTS)
    raise ValueError(f"unknown variant: {variant}")


def storage_args(options: list[str]) -> list[str]:
    args = ["--storage", "oracle_cold_segments"]
    for option in options:
        args.extend(["--storage-opt", option])
    return args


def scenario_name(cold_fraction: float, cold_access_fraction: float) -> str:
    cold = int(round(cold_fraction * 100))
    access = int(round(cold_access_fraction * 100))
    return f"cold{cold}_access{access}"


def job_stem(job: Job) -> str:
    return f"{job.scenario}__{job.variant}__r{job.repeat}"


def wait_for_health(timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_output = ""
    while time.time() < deadline:
        completed = subprocess.run(
            ["ceph", "-s"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        last_output = completed.stdout
        if completed.returncode == 0 and "health: HEALTH_OK" in completed.stdout:
            return
        time.sleep(5)
    raise RuntimeError(f"Ceph did not become HEALTH_OK:\n{last_output}")


def run_job(job: Job, args: argparse.Namespace, out_dir: Path) -> dict[str, object]:
    stem = job_stem(job)
    output = out_dir / f"{stem}.json"
    csv_output = out_dir / f"{stem}.csv"
    command = [
        "./src/scripts/run_posix_bench.sh",
        "--suite",
        "custom",
        "--workload",
        "oracle_hotcold_mix",
        "--file-count",
        str(args.file_count),
        "--file-size",
        str(args.file_size),
        "--dirs",
        str(args.dirs),
        "--ops",
        str(args.ops),
        "--workers",
        str(args.workers),
        "--seed",
        str(job.seed),
        "--oracle-cold-fraction",
        f"{job.cold_fraction:.2f}",
        "--oracle-cold-access-fraction",
        f"{job.cold_access_fraction:.2f}",
        "--segment-size",
        str(args.segment_size),
        *variant_args(job.variant),
    ]
    env = {
        "BENCH_ROOT": args.root,
        "BENCH_OUTPUT": str(output),
        "BENCH_CSV": str(csv_output),
    }
    merged_env = dict(**env)
    wait_for_health(args.health_timeout)
    started = time.time()
    completed = subprocess.run(
        command,
        check=False,
        env={**os.environ, **merged_env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ended = time.time()
    log_path = out_dir / f"{stem}.log"
    log_path.write_text(completed.stdout)
    if completed.returncode != 0:
        raise RuntimeError(f"{stem} failed with exit {completed.returncode}\n{completed.stdout}")
    return {
        "scenario": job.scenario,
        "cold_fraction": job.cold_fraction,
        "cold_access_fraction": job.cold_access_fraction,
        "variant": job.variant,
        "repeat": job.repeat,
        "seed": job.seed,
        "json": str(output),
        "csv": str(csv_output),
        "log": str(log_path),
        "wall_seconds": round(ended - started, 6),
    }


def summarize(out_dir: Path, records: list[dict[str, object]]) -> None:
    enriched = []
    for record in records:
        document = json.loads(Path(str(record["json"])).read_text())
        derived = document.get("derived_metrics", {})
        storage = document.get("storage_metrics", {})
        enriched.append(
            {
                **record,
                "measured_ops_per_sec": float(document["measured_ops_per_sec"]),
                "measured_seconds": float(document["measured_seconds"]),
                "elapsed_seconds": float(document["elapsed_seconds"]),
                "physical_namespace_entries_estimate": derived.get(
                    "physical_namespace_entries_estimate"
                ),
                "physical_files_created_estimate": derived.get(
                    "physical_files_created_estimate"
                ),
                "packed_create_fraction": derived.get("packed_create_fraction"),
                "index_flushes": derived.get("index_flushes"),
                "data_flushes": derived.get("data_flushes"),
                "layout_xattrs_attempted": storage.get("layout_xattrs_attempted"),
                "layout_xattrs_applied": storage.get("layout_xattrs_applied"),
                "layout_xattr_failures": len(storage.get("layout_xattr_failures", [])),
            }
        )

    summary_rows = []
    by_group: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in enriched:
        by_group.setdefault((str(row["scenario"]), str(row["variant"])), []).append(row)

    native_means = {
        scenario: mean([float(row["measured_ops_per_sec"]) for row in rows])
        for (scenario, variant), rows in by_group.items()
        if variant == "native"
    }
    for (scenario, variant), rows in sorted(by_group.items()):
        ops_values = [float(row["measured_ops_per_sec"]) for row in rows]
        elapsed_values = [float(row["elapsed_seconds"]) for row in rows]
        measured_values = [float(row["measured_seconds"]) for row in rows]
        namespace_values = [
            float(row["physical_namespace_entries_estimate"])
            for row in rows
            if row["physical_namespace_entries_estimate"] is not None
        ]
        base = native_means.get(scenario)
        summary_rows.append(
            {
                "scenario": scenario,
                "cold_fraction": rows[0]["cold_fraction"],
                "cold_access_fraction": rows[0]["cold_access_fraction"],
                "variant": variant,
                "runs": len(rows),
                "mean_ops_per_sec": round(mean(ops_values), 6),
                "stdev_ops_per_sec": round(stdev(ops_values), 6),
                "speedup_vs_native_mean": round(mean(ops_values) / base, 6)
                if base
                else "",
                "mean_measured_seconds": round(mean(measured_values), 6),
                "mean_elapsed_seconds": round(mean(elapsed_values), 6),
                "mean_namespace_entries": round(mean(namespace_values), 6)
                if namespace_values
                else "",
                "mean_layout_attempted": round(
                    mean([float(row["layout_xattrs_attempted"] or 0) for row in rows]),
                    6,
                ),
                "mean_layout_applied": round(
                    mean([float(row["layout_xattrs_applied"] or 0) for row in rows]),
                    6,
                ),
                "layout_failure_runs": sum(
                    1 for row in rows if int(row["layout_xattr_failures"] or 0) > 0
                ),
            }
        )

    write_csv(out_dir / "run_manifest.csv", enriched)
    write_csv(out_dir / "summary.csv", summary_rows)
    phase_rows = summarize_phases(records)
    write_csv(out_dir / "phase_summary.csv", phase_rows)
    write_markdown(out_dir / "summary.md", summary_rows, phase_rows)


def summarize_phases(records: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], list[dict[str, float]]] = {}
    for record in records:
        document = json.loads(Path(str(record["json"])).read_text())
        for phase in document["results"]:
            key = (
                str(record["scenario"]),
                str(record["variant"]),
                str(phase["operation"]),
            )
            groups.setdefault(key, []).append(
                {
                    "ops_per_sec": float(phase["ops_per_sec"]),
                    "latency_p95_ms": float(phase["latency_p95_ms"]),
                }
            )
    rows = []
    for (scenario, variant, operation), values in sorted(groups.items()):
        rows.append(
            {
                "scenario": scenario,
                "variant": variant,
                "operation": operation,
                "mean_ops_per_sec": round(mean([value["ops_per_sec"] for value in values]), 6),
                "mean_latency_p95_ms": round(
                    mean([value["latency_p95_ms"] for value in values]), 6
                ),
                "runs": len(values),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    summary_rows: list[dict[str, object]],
    phase_rows: list[dict[str, object]],
) -> None:
    lines = [
        "# CloudLab Hot/Cold Matrix",
        "",
        "Serial randomized benchmark matrix for `oracle_hotcold_mix`.",
        "",
        "## Fairness Controls",
        "",
        "- Same cluster, mount, benchmark root, file size, file count, directory count, worker count, and operation count for every variant.",
        "- Same seeds are reused across variants within each scenario/repeat.",
        "- Run order is randomized globally, then executed serially.",
        "- Each benchmark includes create, access, hot churn, and cleanup phases.",
        "- Ceph health is checked before each run.",
        "",
        "## Summary",
        "",
        "| Scenario | Variant | Runs | Mean ops/s | Speedup | Stdev ops/s | Mean namespace entries | Layout applied | Failure runs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            "| {scenario} | {variant} | {runs} | {mean_ops_per_sec:.2f} | {speedup} | {stdev_ops_per_sec:.2f} | {namespace} | {applied:.1f} | {failures} |".format(
                scenario=row["scenario"],
                variant=row["variant"],
                runs=row["runs"],
                mean_ops_per_sec=float(row["mean_ops_per_sec"]),
                speedup=row["speedup_vs_native_mean"] or "",
                stdev_ops_per_sec=float(row["stdev_ops_per_sec"]),
                namespace=row["mean_namespace_entries"],
                applied=float(row["mean_layout_applied"]),
                failures=row["layout_failure_runs"],
            )
        )
    lines.extend(
        [
            "",
            "## Phase Summary",
            "",
            "| Scenario | Variant | Operation | Mean ops/s | Mean p95 ms | Runs |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in phase_rows:
        lines.append(
            "| {scenario} | {variant} | {operation} | {ops:.2f} | {p95:.2f} | {runs} |".format(
                scenario=row["scenario"],
                variant=row["variant"],
                operation=row["operation"],
                ops=float(row["mean_ops_per_sec"]),
                p95=float(row["mean_latency_p95_ms"]),
                runs=row["runs"],
            )
        )
    path.write_text("\n".join(lines) + "\n")


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/mnt/cephfs/cs2640-bench")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variants", default="native,hybrid,hybrid_layout")
    parser.add_argument("--file-count", type=int, default=3000)
    parser.add_argument("--file-size", type=int, default=4096)
    parser.add_argument("--dirs", type=int, default=64)
    parser.add_argument("--ops", type=int, default=12000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--segment-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--seed-base", type=int, default=1000)
    parser.add_argument("--order-seed", type=int, default=2640)
    parser.add_argument("--health-timeout", type=int, default=180)
    parser.add_argument("--sleep-between-runs", type=float, default=5.0)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip completed jobs in the output directory and summarize all available runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]
    unknown = sorted(set(variants) - {"native", "hybrid", "hybrid_layout"})
    if unknown:
        raise SystemExit(f"unknown variants: {', '.join(unknown)}")
    out_dir = Path(
        args.out_dir
        or f"report/results/cloudlab-hotcold-matrix-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for repeat in range(args.repeats):
        seed = args.seed_base + repeat
        for cold_fraction, cold_access_fraction in SCENARIOS:
            scenario = scenario_name(cold_fraction, cold_access_fraction)
            for variant in variants:
                jobs.append(
                    Job(
                        scenario=scenario,
                        cold_fraction=cold_fraction,
                        cold_access_fraction=cold_access_fraction,
                        variant=variant,
                        repeat=repeat,
                        seed=seed,
                    )
                )
    random.Random(args.order_seed).shuffle(jobs)

    manifest_json = out_dir / "run_manifest.json"
    records: list[dict[str, object]] = []
    completed_stems: set[str] = set()
    if args.resume:
        if manifest_json.exists():
            loaded = json.loads(manifest_json.read_text())
            for record in loaded:
                stem = Path(str(record.get("json", ""))).stem
                json_path = Path(str(record.get("json", "")))
                if stem and json_path.exists() and stem not in completed_stems:
                    records.append(record)
                    completed_stems.add(stem)
        known_stems = {job_stem(job) for job in jobs}
        for job in jobs:
            stem = job_stem(job)
            if stem in completed_stems:
                continue
            output = out_dir / f"{stem}.json"
            csv_output = out_dir / f"{stem}.csv"
            log_path = out_dir / f"{stem}.log"
            if output.exists() and stem in known_stems:
                json.loads(output.read_text())
                records.append(
                    {
                        "scenario": job.scenario,
                        "cold_fraction": job.cold_fraction,
                        "cold_access_fraction": job.cold_access_fraction,
                        "variant": job.variant,
                        "repeat": job.repeat,
                        "seed": job.seed,
                        "json": str(output),
                        "csv": str(csv_output),
                        "log": str(log_path),
                        "wall_seconds": "",
                    }
                )
                completed_stems.add(stem)
        if records:
            manifest_json.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
        print(
            f"resume: {len(completed_stems)} completed, {len(jobs) - len(completed_stems)} remaining",
            flush=True,
        )

    remaining_jobs = [job for job in jobs if job_stem(job) not in completed_stems]
    for index, job in enumerate(remaining_jobs, start=len(completed_stems) + 1):
        print(
            f"[{index}/{len(jobs)}] {job.scenario} {job.variant} repeat={job.repeat} seed={job.seed}",
            flush=True,
        )
        records.append(run_job(job, args, out_dir))
        manifest_json.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
        time.sleep(args.sleep_between_runs)

    summarize(out_dir, records)
    print(f"wrote {out_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
