#!/usr/bin/env python3
"""
Phase 7 Path-B 2D sweep: α × σ_size × seed × policy at multiple cache fractions.

For each (α, σ_size, seed) cell we invoke cache_sim once with --num-requests
fixed and --policies all six base policies, and read miss-ratios from
results/sweep_<id>/mrc.csv.

Output:  results/sweep_alpha_sigma/raw.csv  (one row per cell)
         results/sweep_alpha_sigma/agg.csv  (mean ± std across seeds)
"""

import argparse
import csv
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
OUT_DIR = ROOT / "results" / "sweep_alpha_sigma"
RAW_CSV = OUT_DIR / "raw.csv"
AGG_CSV = OUT_DIR / "agg.csv"

POLICIES = ["lru", "fifo", "clock", "s3fifo", "sieve", "wtinylfu", "gdsf"]
POLICY_LABELS = {
    "lru": "LRU", "fifo": "FIFO", "clock": "CLOCK",
    "s3fifo": "S3-FIFO", "sieve": "SIEVE", "wtinylfu": "W-TinyLFU",
}

# Sweep grid (Phase 7 D-23):
#   α   ∈ {0.4, 0.6, 0.8, 1.0, 1.2, 1.4}        — spans Congress (0.23) to high-skew
#   σ   ∈ {0.5, 1.0, 1.5, 2.0, 2.5, 3.0}        — log-normal sigma; max/median ratios
#                                                  ~3×, 12×, 65×, 470×, 5300×, 95000×
#   cache_frac ∈ {0.001, 0.005, 0.01, 0.05}     — small / mid / focal / large
#   seeds 5  (same as paper main sweep)
ALPHAS = [0.4, 0.6, 0.8, 1.0, 1.2, 1.4]
SIGMAS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]
SEEDS = [42, 43, 44, 45, 46]

NUM_REQUESTS = 20000   # match paper main-sweep scale (default; CLI override)
NUM_OBJECTS  = 15000   # match Court raw unique-object count (default; CLI override)

def run_cell(alpha: float, sigma: float, seed: int, tmp_root: str,
             num_requests: int, num_objects: int) -> list[dict]:
    """Run cache_sim once for (α, σ, seed); return per-policy per-cache_frac rows."""
    cell_dir = Path(tmp_root) / f"a{alpha}_s{sigma}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(SIM),
        "--num-requests", str(num_requests),
        "--num-objects",  str(num_objects),
        "--alpha",        str(alpha),
        "--size-sigma",   str(sigma),
        "--seed",         str(seed),
        "--policies",     ",".join(POLICIES),
        "--cache-sizes",  ",".join(str(f) for f in CACHE_FRACS),
        "--output-dir",   str(cell_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL α={alpha} σ={sigma} seed={seed}\n{proc.stderr[-2000:]}\n")
        return []
    rows = []
    mrc_path = cell_dir / "mrc.csv"
    with open(mrc_path) as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append({
                "alpha": alpha,
                "sigma": sigma,
                "seed": seed,
                "cache_frac": float(row["cache_frac"]),
                "policy": row["policy"],
                "miss_ratio": float(row["miss_ratio"]),
                "byte_miss_ratio": float(row["byte_miss_ratio"]),
            })
    shutil.rmtree(cell_dir, ignore_errors=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--num-requests", type=int, default=NUM_REQUESTS,
                        help="requests per cell (default: 20000)")
    parser.add_argument("--num-objects", type=int, default=NUM_OBJECTS,
                        help="unique objects per synthetic trace (default: 15000)")
    parser.add_argument("--out-suffix", type=str, default="",
                        help="suffix for output csvs (e.g. _1m); default overwrites")
    parser.add_argument("--smoke", action="store_true",
                        help="tiny grid for smoke-test")
    args = parser.parse_args()

    alphas, sigmas, seeds = ALPHAS, SIGMAS, SEEDS
    if args.smoke:
        alphas = [0.4, 1.0]
        sigmas = [0.5, 2.5]
        seeds  = [42, 43]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cells = list(itertools.product(alphas, sigmas, seeds))
    print(f"Cells: {len(cells)} × {len(POLICIES)} policies × {len(CACHE_FRACS)} cache_fracs"
          f"  =  {len(cells) * len(POLICIES) * len(CACHE_FRACS)} miss-ratio points",
          flush=True)

    print(f"  num_requests={args.num_requests}  num_objects={args.num_objects}  "
          f"workers={args.workers}", flush=True)

    t0 = time.time()
    all_rows = []
    with tempfile.TemporaryDirectory() as tmp_root:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(run_cell, a, s, d, tmp_root,
                              args.num_requests, args.num_objects)
                    for (a, s, d) in cells]
            for n, fut in enumerate(as_completed(futs), 1):
                all_rows.extend(fut.result())
                if n % max(1, len(cells) // 20) == 0 or n == len(cells):
                    print(f"  [{n:4d}/{len(cells)}]  elapsed {time.time()-t0:6.1f}s",
                          flush=True)

    # output paths (apply suffix if any)
    suffix = args.out_suffix
    raw_out = OUT_DIR / f"raw{suffix}.csv"
    agg_out = OUT_DIR / f"agg{suffix}.csv"

    # write raw
    fields = ["alpha", "sigma", "seed", "cache_frac", "policy", "miss_ratio", "byte_miss_ratio"]
    with open(raw_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Wrote {len(all_rows)} rows -> {raw_out}")

    # aggregate (mean ± std across seeds)
    from statistics import mean, stdev
    grouped = {}
    for r in all_rows:
        k = (r["alpha"], r["sigma"], r["cache_frac"], r["policy"])
        grouped.setdefault(k, []).append(r["miss_ratio"])
    agg_rows = []
    for (a, s, cf, p), vs in grouped.items():
        agg_rows.append({
            "alpha": a, "sigma": s, "cache_frac": cf, "policy": p,
            "mean_miss": mean(vs),
            "std_miss":  stdev(vs) if len(vs) > 1 else 0.0,
            "n_seeds":   len(vs),
        })
    agg_fields = ["alpha", "sigma", "cache_frac", "policy", "mean_miss", "std_miss", "n_seeds"]
    with open(agg_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=agg_fields)
        w.writeheader()
        w.writerows(agg_rows)
    print(f"Wrote {len(agg_rows)} agg rows -> {agg_out}")
    print(f"Total wall-clock: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
