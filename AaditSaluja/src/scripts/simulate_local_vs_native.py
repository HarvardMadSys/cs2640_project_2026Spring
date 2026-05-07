#!/usr/bin/env python3
"""Run a local native-vs-oracle sanity matrix with a Ceph-like cost model."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


VARIANTS = [
    {
        "name": "native",
        "storage": "native",
        "storage_opts": [],
    },
    {
        "name": "oracle_cephfs_segments",
        "storage": "oracle_cold_segments",
        "storage_opts": [
            "hot_prefixes=hot",
            "virtual_cold_dirs=true",
            "index_batch_bytes=1048576",
            "index_batch_records=512",
            "cold_data_batch_bytes=1048576",
            "cold_data_backend=cephfs",
        ],
    },
    {
        "name": "oracle_rados_objects",
        "storage": "oracle_cold_segments",
        "storage_opts": [
            "hot_prefixes=hot",
            "virtual_cold_dirs=true",
            "index_batch_bytes=1048576",
            "index_batch_records=512",
            "cold_data_batch_bytes=1048576",
            "cold_data_backend=rados",
        ],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--file-count", type=int, default=1024)
    parser.add_argument("--file-size", type=int, default=4096)
    parser.add_argument("--dirs", type=int, default=64)
    parser.add_argument("--ops", type=int, default=4096)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--mds-ranks", type=int, default=4)
    parser.add_argument("--osd-ranks", type=int, default=3)
    parser.add_argument("--root", default="/tmp/cs2640-local-sim-bench")
    return parser.parse_args()


def run_variant(args: argparse.Namespace, repo: Path, out_dir: Path, variant: dict[str, object]) -> dict[str, object]:
    output = out_dir / f"{variant['name']}.json"
    csv_output = out_dir / f"{variant['name']}.csv"
    command = [
        sys.executable,
        "-m",
        "cephfs_metadata.benchmark_runner",
        "--backend",
        "simulated-ceph",
        "--sim-mds-ranks",
        str(args.mds_ranks),
        "--sim-osd-ranks",
        str(args.osd_ranks),
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
        str(args.seed),
        "--storage",
        str(variant["storage"]),
        "--root",
        args.root,
        "--output",
        str(output),
        "--csv",
        str(csv_output),
        "--no-ceph-stats",
    ]
    for option in variant["storage_opts"]:
        command.extend(["--storage-opt", str(option)])

    env = os.environ.copy()
    src = str(repo / "src")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{variant['name']} failed with exit {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )
    return json.loads(output.read_text())


def row_for(name: str, document: dict[str, object], native_ops: float) -> list[str]:
    derived = document.get("derived_metrics", {})
    storage = document.get("storage_metrics", {})
    ops = float(document.get("measured_ops_per_sec", 0.0))
    rel = ops / native_ops if native_ops > 0 else 0.0
    return [
        name,
        f"{ops:.1f}",
        f"{rel:.2f}x",
        str(derived.get("physical_namespace_entries_estimate", "")),
        str(derived.get("logical_creates_per_physical_file", "")),
        str(storage.get("data_flushes", "")),
        str(storage.get("rados_objects_created", "")),
    ]


def print_table(results: list[tuple[str, dict[str, object]]]) -> None:
    native_ops = float(results[0][1].get("measured_ops_per_sec", 0.0)) if results else 0.0
    headers = [
        "variant",
        "ops/sec",
        "vs native",
        "namespace entries",
        "creates/physical file",
        "data flushes",
        "rados objects",
    ]
    rows = [row_for(name, document, native_ops) for name, document in results]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        print("| " + " | ".join(row) + " |")


def main() -> int:
    args = parse_args()
    script_path = Path(__file__).resolve()
    repo = script_path.parents[2] if script_path.parent.parent.name == "src" else script_path.parents[1]
    out_dir = Path(args.out_dir) if args.out_dir else repo / "report" / "results" / f"local-sim-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, dict[str, object]]] = []
    for variant in VARIANTS:
        name = str(variant["name"])
        print(f"running {name}...", flush=True)
        results.append((name, run_variant(args, repo, out_dir, variant)))

    print(f"\nresults: {out_dir}")
    print_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
