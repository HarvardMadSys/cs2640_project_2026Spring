#!/usr/bin/env python3
"""
Multi-seed sweep orchestrator for Phase 5 (D-05, D-06, D-09).

Runs ./cache_sim five times (seeds: 42, 7, 13, 23, 31) per workload (Congress
and Court) with --alpha-sweep, producing per-seed MRC + alpha-sensitivity CSVs
under results/compare/multiseed/{congress,court}/ for aggregation by
scripts/compare_workloads.py (Plan 05-04) and ±1σ CI-band plotting (Plan 05-05).

Invocation model: SINGLE-INVOCATION (verified empirically and via src/main.cpp).
One `./cache_sim --alpha-sweep ...` call emits both `mrc.csv` and
`alpha_sensitivity.csv` into --output-dir. The MRC block (src/main.cpp line ~238)
runs unconditionally; the alpha-sweep block (line ~302) is gated by
`if (alpha_sweep)` and writes alpha_sensitivity.csv alongside the MRC. So we
need exactly 10 invocations (5 seeds × 2 workloads) to produce 20 CSVs.

Usage:
    python3 scripts/run_multiseed_sweep.py [--seeds 42,7,13,23,31]
        [--congress-trace traces/congress_trace.csv]
        [--court-trace traces/court_trace.csv]
        [--output-base results/compare/multiseed]
        [--cache-sim ./cache_sim]
        [--dry-run]

Depends on Plan 05-01's --seed flag being wired into cache_sim.
Expected runtime: 10-20 min on a dev laptop (10 invocations × ~1-2 min each).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

# D-05: five seeds for CI bands. 42 is the existing default; others are small primes.
SEEDS = [42, 7, 13, 23, 31]

# Workload -> trace path map; arg defaults echo these (overridable via CLI).
WORKLOADS = {
    "congress": "traces/congress_trace.csv",
    "court":    "traces/court_trace.csv",
}

# CSV stems produced by one cache_sim invocation with --alpha-sweep.
# Both files are emitted into --output-dir under the SINGLE-invocation model
# (verified via src/main.cpp: MRC block unconditional, alpha-sweep block
# gated by --alpha-sweep; both write into --output-dir).
STEMS = ["mrc", "alpha_sensitivity"]


def run_one_cell(cache_sim, workload, trace, seed, output_base, dry_run):
    """Run one (workload, seed) cell: invoke cache_sim once with --alpha-sweep,
    then rename the two emitted CSVs (mrc.csv + alpha_sensitivity.csv) to
    per-seed names (mrc_seed{N}.csv + alpha_sensitivity_seed{N}.csv).

    Exits the whole orchestrator with sys.exit(1) on any cache_sim failure
    (T-05-03-02: no silent partial-failure).
    """
    final_dir = os.path.join(output_base, workload)
    os.makedirs(final_dir, exist_ok=True)

    # Per-cell scratch output-dir so concurrent cells could not collide
    # (T-05-03-04: rename-race mitigation; cache_sim writes mrc.csv + alpha_sensitivity.csv
    # by fixed stem names, so we isolate each cell's output).
    scratch = os.path.join(final_dir, f"_scratch_seed{seed}")
    os.makedirs(scratch, exist_ok=True)

    # T-05-03-01: list-form subprocess args (never shell-mode); no shell metachars
    # parsed. User-supplied `trace` path flows in as a literal argv element.
    cmd = [
        cache_sim,
        "--trace", trace,
        "--replay-zipf",
        "--alpha-sweep",
        "--seed", str(seed),
        "--output-dir", scratch,
    ]

    if dry_run:
        print("  DRY-RUN:", " ".join(cmd))
        return

    print(f"  Running: {' '.join(cmd)}")
    t_start = time.time()
    result = subprocess.run(cmd, check=False)
    elapsed = time.time() - t_start
    if result.returncode != 0:
        print(f"  ERROR: cache_sim exited {result.returncode} for "
              f"workload={workload} seed={seed}", file=sys.stderr)
        sys.exit(1)
    print(f"  Cell workload={workload} seed={seed} done in {elapsed:.1f}s")

    # Rename the two emitted CSVs to per-seed names, move up from scratch.
    for stem in STEMS:
        src = os.path.join(scratch, f"{stem}.csv")
        dst = os.path.join(final_dir, f"{stem}_seed{seed}.csv")
        if not os.path.exists(src):
            print(f"  ERROR: expected {src} from cache_sim but it's missing",
                  file=sys.stderr)
            sys.exit(1)
        # os.rename is atomic on POSIX when src/dst are on the same filesystem
        # (T-05-03-04). All paths are under the same filesystem by construction.
        os.rename(src, dst)
        print(f"  Renamed {src} -> {dst}")

    # Clean up the empty scratch dir (leaves behind only the renamed per-seed CSVs).
    # Note: cache_sim may also emit one_hit_wonder.csv (uncommitted side output);
    # shutil.rmtree takes it with the scratch dir — intentional.
    try:
        shutil.rmtree(scratch)
    except OSError as e:
        # Non-fatal: scratch removal is hygiene, not correctness.
        print(f"  Warning: could not remove {scratch}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--seeds", default=",".join(str(s) for s in SEEDS),
                    help=f"Comma-separated seed list (default: {','.join(str(s) for s in SEEDS)})")
    ap.add_argument("--congress-trace", default=WORKLOADS["congress"],
                    help=f"Congress trace CSV (default: {WORKLOADS['congress']})")
    ap.add_argument("--court-trace", default=WORKLOADS["court"],
                    help=f"Court trace CSV (default: {WORKLOADS['court']})")
    ap.add_argument("--output-base", default="results/compare/multiseed",
                    help="Base directory for per-seed CSVs (default: results/compare/multiseed)")
    ap.add_argument("--cache-sim", default="./cache_sim",
                    help="Path to cache_sim binary (default: ./cache_sim)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print invocations without running")
    args = ap.parse_args()

    # Parse seed list.
    try:
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    except ValueError as e:
        print(f"Error: invalid --seeds '{args.seeds}': {e}", file=sys.stderr)
        sys.exit(1)
    if not seeds:
        print("Error: --seeds produced an empty list", file=sys.stderr)
        sys.exit(1)

    # Pre-flight checks (T-05-03-03).
    if not os.path.exists(args.cache_sim):
        print(f"Error: {args.cache_sim} not found - run `make` first",
              file=sys.stderr)
        sys.exit(1)
    trace_map = {"congress": args.congress_trace, "court": args.court_trace}
    for workload, trace in trace_map.items():
        if not os.path.exists(trace):
            print(f"Error: {workload} trace {trace} not found", file=sys.stderr)
            sys.exit(1)

    # Main sweep loop.
    total_cells = len(seeds) * len(trace_map)
    print(f"=== Multi-seed sweep: {len(seeds)} seeds x {len(trace_map)} workloads "
          f"= {total_cells} cells (each emits mrc + alpha_sensitivity) ===")
    t_all_start = time.time()
    cell_idx = 0
    for workload, trace in trace_map.items():
        for seed in seeds:
            cell_idx += 1
            print(f"\n[{cell_idx}/{total_cells}] workload={workload} seed={seed}")
            run_one_cell(args.cache_sim, workload, trace, seed,
                         args.output_base, args.dry_run)

    if not args.dry_run:
        total_elapsed = time.time() - t_all_start
        print(f"\n=== Multi-seed sweep complete in {total_elapsed:.1f}s "
              f"({total_elapsed/60:.1f} min) ===")
        # Summary: list produced CSVs
        produced = []
        for workload in trace_map:
            wl_dir = os.path.join(args.output_base, workload)
            if os.path.isdir(wl_dir):
                for fname in sorted(os.listdir(wl_dir)):
                    if fname.endswith(".csv"):
                        produced.append(os.path.join(wl_dir, fname))
        print(f"Produced {len(produced)} CSVs:")
        for p in produced:
            print(f"  {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
