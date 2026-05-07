#!/usr/bin/env python3
"""
20K-vs-1M scale comparison at multiple (alpha, sigma) cells, replacing the
single-cell extrapolation in v2 §11. The v2 claim was "3-5x cold-start ceiling"
based on one (alpha=1.0, sigma=1.5) point. Here we test 6 cells across the
plane and report the actual transient/steady-state ratio at each.

Cells tested:
   (alpha, sigma) ∈ {(0.6, 1.5), (1.0, 1.5), (1.4, 1.5),
                     (0.6, 0.5), (1.0, 0.5), (1.4, 0.5)}

Output: results/scale_multicell/summary.csv with one row per (alpha, sigma)
showing the transient/steady-state ratio.
"""
import csv
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
OUT_DIR = ROOT / "results" / "scale_multicell"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CELLS = [(0.6, 1.5), (1.0, 1.5), (1.4, 1.5),
         (0.6, 0.5), (1.0, 0.5), (1.4, 0.5)]
SEEDS = [42, 43, 44]
SCALES = [(20_000,   2_000), ("20K"),
          (1_000_000, 100_000)]
CACHE_FRAC = 0.01

# Define scale tuples cleanly: (label, num_requests, num_objects)
SCALE_DEFS = [
    ("20K",  20_000,    2_000),
    ("1M",   1_000_000, 100_000),
]

def run(alpha, sigma, seed, num_req, num_obj):
    cell_dir = OUT_DIR / f"_run_a{alpha}_s{sigma}_d{seed}_n{num_req}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--num-requests", str(num_req),
           "--num-objects",  str(num_obj),
           "--size-mu", "8.3",
           "--size-sigma", str(sigma),
           "--policies", "sieve,wtinylfu",
           "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL ({alpha}, {sigma}, {seed}, {num_req}):\n{proc.stderr[-1500:]}\n")
        return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row["policy"]] = float(row["miss_ratio"])
    shutil.rmtree(cell_dir, ignore_errors=True)
    return out

print(f"Running {len(CELLS)} cells × {len(SCALES)//2} scales × {len(SEEDS)} seeds = "
      f"{len(CELLS)*2*len(SEEDS)} cache_sim invocations...")

rows_summary = []
for alpha, sigma in CELLS:
    cell_data = {}
    for label, num_req, num_obj in SCALE_DEFS:
        gaps = []
        for s in SEEDS:
            d = run(alpha, sigma, s, num_req, num_obj)
            if d: gaps.append((d["SIEVE"] - d["W-TinyLFU"]) * 100)
        cell_data[label] = (mean(gaps), stdev(gaps) if len(gaps) > 1 else 0)
    transient = cell_data["20K"][0]
    steady = cell_data["1M"][0]
    ratio = transient / steady if abs(steady) > 0.01 else None

    print(f"  α={alpha} σ={sigma}: 20K gap={transient:+.2f}±{cell_data['20K'][1]:.2f}pp  "
          f"1M gap={steady:+.2f}±{cell_data['1M'][1]:.2f}pp  "
          f"ratio={ratio:.2f}" if ratio is not None else f"  α={alpha} σ={sigma}: ratio undef (1M ≈ 0)")
    rows_summary.append({
        "alpha": alpha, "sigma": sigma,
        "gap_20K_pp": round(transient, 3), "std_20K_pp": round(cell_data['20K'][1], 3),
        "gap_1M_pp":  round(steady, 3),    "std_1M_pp":  round(cell_data['1M'][1], 3),
        "transient_to_steady_ratio": round(ratio, 2) if ratio is not None else None,
    })

with open(OUT_DIR / "summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows_summary[0].keys())
    w.writeheader()
    for r in rows_summary: w.writerow(r)

print(f"\n=== Multi-cell scale comparison verdict ===")
ratios = [r["transient_to_steady_ratio"] for r in rows_summary if r["transient_to_steady_ratio"] is not None]
print(f"  ratios: min={min(ratios):.2f}, median={sorted(ratios)[len(ratios)//2]:.2f}, "
      f"max={max(ratios):.2f}")
print(f"\nWrote {OUT_DIR / 'summary.csv'}")
