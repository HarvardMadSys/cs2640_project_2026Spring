#!/usr/bin/env python3
"""Profile non-oracle predictive cold-packing strategies on CloudLab."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SCENARIOS = [
    (0.70, 0.20),
    (0.90, 0.10),
    (0.90, 0.20),
]

BASE_OPTS = [
    "virtual_cold_dirs=true",
    "index_batch_bytes=1048576",
    "index_batch_records=512",
    "cold_data_batch_bytes=1048576",
    "cold_data_backend=cephfs",
    "packed_stat_mode=index",
]


@dataclass(frozen=True)
class Job:
    scenario: str
    cold_fraction: float
    cold_access_fraction: float
    variant: str
    repeat: int
    seed: int


def scenario_name(cold_fraction: float, cold_access_fraction: float) -> str:
    return f"cold{int(cold_fraction * 100)}_access{int(cold_access_fraction * 100)}"


def job_stem(job: Job) -> str:
    return f"{job.scenario}__{job.variant}__r{job.repeat}"


def storage_args(variant: str) -> list[str]:
    if variant == "native":
        return ["--storage", "native"]
    if variant == "oracle":
        return [
            "--storage",
            "oracle_cold_segments",
            "--storage-opt",
            "hot_prefixes=hot",
            *storage_opts(BASE_OPTS),
        ]
    if variant == "pred_read1":
        return predictor_args(["promotion_threshold=1", "promotion_triggers=read"])
    if variant == "pred_statread2":
        return predictor_args(["promotion_threshold=2", "promotion_triggers=stat,read"])
    if variant == "pred_statread4":
        return predictor_args(["promotion_threshold=4", "promotion_triggers=stat,read"])
    if variant == "pred_dirhot_lazy_read":
        return predictor_args(
            [
                "predictor_strategy=directory_hotset",
                "predictor_directory_promotion=true",
                "predictor_promote_existing=false",
                "predictor_dir_event_threshold=8",
                "predictor_dir_distinct_threshold=3",
                "promotion_threshold=1",
                "promotion_triggers=read",
            ]
        )
    raise ValueError(f"unknown variant: {variant}")


def predictor_args(extra_opts: list[str]) -> list[str]:
    return ["--storage", "predictive_cold_segments", *storage_opts(BASE_OPTS + extra_opts)]


def storage_opts(options: list[str]) -> list[str]:
    args: list[str] = []
    for option in options:
        args.extend(["--storage-opt", option])
    return args


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
        *storage_args(job.variant),
    ]
    wait_for_health(args.health_timeout)
    started = time.time()
    completed = subprocess.run(
        command,
        check=False,
        env={
            **os.environ,
            "BENCH_ROOT": args.root,
            "BENCH_OUTPUT": str(output),
            "BENCH_CSV": str(csv_output),
        },
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
    rows = []
    for record in records:
        document = json.loads(Path(str(record["json"])).read_text())
        storage = document.get("storage_metrics", {})
        derived = document.get("derived_metrics", {})
        rows.append(
            {
                **record,
                "measured_ops_per_sec": float(document["measured_ops_per_sec"]),
                "measured_seconds": float(document["measured_seconds"]),
                "namespace_entries": derived.get("physical_namespace_entries_estimate"),
                "total_promotions": storage.get("total_promotions", 0),
                "promoted_hot": storage.get("total_promoted_eval_hot_paths", 0),
                "promoted_cold": storage.get("total_promoted_eval_cold_paths", 0),
                "promotion_failures": storage.get("promotion_failures", 0),
                "layout_failures": len(storage.get("layout_xattr_failures", []) or []),
            }
        )
    write_csv(out_dir / "run_manifest.csv", rows)

    summary = []
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault((str(row["scenario"]), str(row["variant"])), []).append(row)
    oracle_means = {
        scenario: mean([float(row["measured_ops_per_sec"]) for row in values])
        for (scenario, variant), values in groups.items()
        if variant == "oracle"
    }
    for (scenario, variant), values in sorted(groups.items()):
        ops = [float(row["measured_ops_per_sec"]) for row in values]
        oracle = oracle_means.get(scenario)
        summary.append(
            {
                "scenario": scenario,
                "variant": variant,
                "runs": len(values),
                "mean_ops_per_sec": round(mean(ops), 6),
                "stdev_ops_per_sec": round(stdev(ops), 6),
                "relative_to_oracle": round(mean(ops) / oracle, 6) if oracle else "",
                "mean_namespace_entries": round(
                    mean([float(row["namespace_entries"]) for row in values]), 6
                ),
                "mean_promotions": round(
                    mean([float(row["total_promotions"]) for row in values]), 6
                ),
                "mean_promoted_hot": round(
                    mean([float(row["promoted_hot"]) for row in values]), 6
                ),
                "mean_promoted_cold": round(
                    mean([float(row["promoted_cold"]) for row in values]), 6
                ),
                "promotion_failure_runs": sum(
                    1 for row in values if int(row["promotion_failures"] or 0) > 0
                ),
                "layout_failure_runs": sum(
                    1 for row in values if int(row["layout_failures"] or 0) > 0
                ),
            }
        )
    write_csv(out_dir / "summary.csv", summary)
    write_markdown(out_dir / "summary.md", summary)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [
        "# CloudLab Predictor Profile",
        "",
        "Non-oracle predictive cold-packing strategies compared with oracle upper bound.",
        "",
        "| Scenario | Variant | Runs | Mean ops/s | Rel. oracle | Stdev | Namespace | Promotions | Hot promos | Cold promos |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {scenario} | {variant} | {runs} | {ops:.2f} | {rel} | {stdev:.2f} | {ns:.1f} | {promo:.1f} | {hot:.1f} | {cold:.1f} |".format(
                scenario=row["scenario"],
                variant=row["variant"],
                runs=row["runs"],
                ops=float(row["mean_ops_per_sec"]),
                rel=row["relative_to_oracle"],
                stdev=float(row["stdev_ops_per_sec"]),
                ns=float(row["mean_namespace_entries"]),
                promo=float(row["mean_promotions"]),
                hot=float(row["mean_promoted_hot"]),
                cold=float(row["mean_promoted_cold"]),
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
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--variants",
        default="oracle,pred_read1,pred_statread2,pred_statread4,pred_dirhot_lazy_read",
    )
    parser.add_argument("--file-count", type=int, default=3000)
    parser.add_argument("--file-size", type=int, default=4096)
    parser.add_argument("--dirs", type=int, default=64)
    parser.add_argument("--ops", type=int, default=12000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--segment-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--seed-base", type=int, default=2000)
    parser.add_argument("--order-seed", type=int, default=92640)
    parser.add_argument("--health-timeout", type=int, default=180)
    parser.add_argument("--sleep-between-runs", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]
    unknown = sorted(
        set(variants)
        - {
            "native",
            "oracle",
            "pred_read1",
            "pred_statread2",
            "pred_statread4",
            "pred_dirhot_lazy_read",
        }
    )
    if unknown:
        raise SystemExit(f"unknown variants: {', '.join(unknown)}")
    out_dir = Path(
        args.out_dir
        or f"report/results/cloudlab-predictor-profile-{time.strftime('%Y%m%d-%H%M%S')}"
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

    records = []
    manifest_json = out_dir / "run_manifest.json"
    for index, job in enumerate(jobs, start=1):
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
