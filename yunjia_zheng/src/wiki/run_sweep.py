#!/usr/bin/env python3
"""Sweep bench_tenants.py across a list of collections.

For each collection in COLLECTIONS, run three benches in this order:

  1. python bench_tenants.py --collection X --reset-cache --warm 20 --duration 120
       (A+B concurrent, request-per-tenant=1, drops page cache first.)
  2. python bench_tenants.py --collection X --warm 20 --duration 120 \\
                             --tenant A --request-per-tenant 16
  3. python bench_tenants.py --collection X --warm 20 --duration 120 \\
                             --tenant B --request-per-tenant 16

Only the first call passes --reset-cache, runs 2 and 3 inherit the page
cache that was warmed by run 1 plus their own --warm phase. This keeps
the cold-start penalty paid once per collection rather than three times.

Usage:
  python run_sweep.py
  python run_sweep.py --collections wiki_QC02_SS08 wiki_QC08_SS08
  python run_sweep.py --duration 300 --warm 30 --rpt 8
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench_tenants.py"
BENCH_RESULT = HERE / "bench_result"
EXPECTED_FILES_PER_SEQUENCE = 9   # 3 runs * (run.log + throughput.csv + per_query_recall.csv)

DEFAULT_COLLECTIONS = [
    "wiki_QC02_SS08",
    "wiki_QC02_SS08_nocompact",
    "wiki_QC08_SS08",
    "wiki_QC08_SS08_nocompact",
]


def run(cmd: list[str]) -> int:
    pretty = " ".join(cmd)
    print(f"\n>>> {pretty}", flush=True)
    t0 = time.time()
    rc = subprocess.run(cmd).returncode
    dt = time.time() - t0
    status = "OK" if rc == 0 else f"FAILED rc={rc}"
    print(f"<<< {status}  elapsed={dt:.1f}s", flush=True)
    return rc


def quarantine_bench_result() -> None:
    """If bench_result/ exists and contains anything from a prior partial run,
    move it aside under a timestamped name so the new sweep starts clean."""
    if not BENCH_RESULT.exists():
        BENCH_RESULT.mkdir(parents=True, exist_ok=True)
        return
    contents = list(BENCH_RESULT.iterdir())
    if not contents:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = HERE / f"bench_result_unfiled_{stamp}"
    print(f"[sweep] bench_result/ has {len(contents)} stale files, "
          f"moving aside to {backup.name}/")
    BENCH_RESULT.rename(backup)
    BENCH_RESULT.mkdir(parents=True, exist_ok=True)


def archive_bench_result(collection: str) -> tuple[Path, int, bool]:
    """Rename bench_result/ to <collection>_bench_result/.

    Returns (target_path, file_count, ok). ok = file_count == EXPECTED.
    If the target already exists, append a timestamp so we never overwrite
    a previous archive of the same collection.
    """
    target = HERE / f"{collection}_bench_result"
    if target.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = HERE / f"{collection}_bench_result_{stamp}"

    files = list(BENCH_RESULT.iterdir()) if BENCH_RESULT.exists() else []
    n = len(files)
    ok = n == EXPECTED_FILES_PER_SEQUENCE
    if not ok:
        print(f"[sweep] WARNING: bench_result/ has {n} files, "
              f"expected {EXPECTED_FILES_PER_SEQUENCE}. Listing:")
        for f in sorted(files):
            print(f"   {f.name}")

    BENCH_RESULT.rename(target)
    BENCH_RESULT.mkdir(parents=True, exist_ok=True)
    print(f"[sweep] archived {n} files to {target.name}/  (expected {EXPECTED_FILES_PER_SEQUENCE})")
    return target, n, ok


def runs_for(collection: str, duration: int, warm: int, rpt: int,
             ab_rpt: int, python: str) -> list[list[str]]:
    bench = str(BENCH)
    base = [python, bench, "--collection", collection,
            "--warm", str(warm), "--duration", str(duration)]
    return [
        # 1. Cold cache, A+B concurrent at ab_rpt per tenant.
        #    Default ab_rpt = rpt // 2 so total in-flight = rpt, matching the
        #    single-tenant runs below for an apples-to-apples comparison.
        base + ["--reset-cache",
                "--request-per-tenant", str(ab_rpt)],
        # 2. Warm cache, A only at full per-tenant concurrency
        base + ["--tenant", "A", "--request-per-tenant", str(rpt)],
        # 3. Warm cache, B only at full per-tenant concurrency
        base + ["--tenant", "B", "--request-per-tenant", str(rpt)],
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collections", nargs="+", default=DEFAULT_COLLECTIONS,
                    help=f"Collections to sweep (default: {DEFAULT_COLLECTIONS})")
    ap.add_argument("--duration", type=int, default=120,
                    help="Bench duration seconds (passed to bench_tenants.py)")
    ap.add_argument("--warm", type=int, default=20,
                    help="Warmup seconds (passed to bench_tenants.py)")
    ap.add_argument("--rpt", type=int, default=16,
                    help="--request-per-tenant for the single-tenant runs (2 and 3).")
    ap.add_argument("--ab-rpt", type=int, default=None,
                    help="--request-per-tenant for the A+B concurrent run (1). "
                         "Default is --rpt / 2 so total in-flight matches the "
                         "single-tenant runs (e.g. solo=16 -> A+B=8+8=16).")
    ap.add_argument("--python", default=sys.executable,
                    help="Python interpreter to launch bench_tenants with")
    ap.add_argument("--keep-going", action="store_true",
                    help="Continue to the next collection even if a bench in "
                         "the current sequence fails. Default is to skip the "
                         "remaining runs of the failing collection but still "
                         "move on to the next collection.")
    args = ap.parse_args()

    if not BENCH.exists():
        print(f"ERROR: {BENCH} not found", file=sys.stderr)
        return 2

    ab_rpt = args.ab_rpt if args.ab_rpt is not None else max(1, args.rpt // 2)

    print(f"Sweep over {len(args.collections)} collection(s):")
    for c in args.collections:
        print(f"  - {c}")
    print(f"  duration={args.duration}s  warm={args.warm}s  "
          f"rpt={args.rpt} (solo)  ab_rpt={ab_rpt} (A+B per tenant)")

    sweep_t0 = time.time()
    summary: list[tuple[str, int, int, int, str]] = []
    # (collection, ok_runs, total_runs, file_count, archive_dir_name)

    for col in args.collections:
        # Make sure bench_result/ is empty so the file-count check is meaningful.
        quarantine_bench_result()

        seq = runs_for(col, args.duration, args.warm, args.rpt, ab_rpt, args.python)
        ok = 0
        for cmd in seq:
            rc = run(cmd)
            if rc != 0:
                if args.keep_going:
                    continue
                break
            ok += 1

        # Always archive whatever landed, even on partial failure, so the
        # next collection's runs do not get mixed with this one's outputs.
        archive_path, n_files, exact = archive_bench_result(col)
        summary.append((col, ok, len(seq), n_files, archive_path.name))

    print("\n=== sweep summary ===")
    for col, ok, total, n_files, archive_name in summary:
        run_marker = "OK " if ok == total else "PART"
        file_marker = "OK " if n_files == EXPECTED_FILES_PER_SEQUENCE else "MISS"
        print(f"  [{run_marker}/{file_marker}] {col}: "
              f"{ok}/{total} runs, {n_files}/{EXPECTED_FILES_PER_SEQUENCE} files "
              f"-> {archive_name}/")
    print(f"total elapsed: {(time.time() - sweep_t0)/60:.1f} min")
    all_ok = all(ok == total and n == EXPECTED_FILES_PER_SEQUENCE
                 for _, ok, total, n, _ in summary)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
