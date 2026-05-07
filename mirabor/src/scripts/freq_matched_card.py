#!/usr/bin/env python3
"""
Matched-cardinality frequency control ablation. The previous freq-only
ablations stripped either 75 keys (mostly 1-access pads) or 20 keys
(5000 access rows = 25% of trace). Neither is apples-to-apples with
the 75-key, 75-row size ablation.

This run: strip 75 randomly-chosen 1-access keys (so we remove 75 keys
and 75 rows, exactly matching the size ablation's cardinality and
access-row footprint). Repeat with 5 random selections of which 75
1-access keys to strip; report mean shrinkage over those 5 selections.

If the size ablation's 62-78% shrinkage is workload-shape-driven
(removing any 75 keys gives the same shrinkage), this control reproduces
it. If it's specifically about size, this control leaves the gap intact.
"""
import csv
from collections import Counter
import shutil
import subprocess
import random
import sys
import json
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
COURT_TRACE = ROOT / "traces" / "court_trace.csv"
OUT_DIR = ROOT / "results" / "freq_matched_ablation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

K = 75
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01
N_SELECTIONS = 5  # 5 random subsets of which 75 1-access keys to strip

rows = list(csv.DictReader(open(COURT_TRACE)))
counts = Counter(r["key"] for r in rows)
one_access_keys = [k for k, c in counts.items() if c == 1]
print(f"Court trace: {len(rows)} rows, {len(counts)} unique keys")
print(f"  one-access keys (count=1): {len(one_access_keys)}")
print(f"  {K} matched-cardinality strip = 75 of {len(one_access_keys)} one-access keys")

def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM), "--trace", str(trace_path), "--replay-zipf",
           "--alpha", str(alpha), "--seed", str(seed),
           "--policies", "sieve,wtinylfu", "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0: return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row["policy"]] = float(row["miss_ratio"])
    shutil.rmtree(cell_dir, ignore_errors=True)
    return out

# Original (with all keys) gap, computed once
print("\nOriginal Court sweep (with all keys, 5 seeds × 4 alphas)...")
orig_gaps_per_alpha = {}
for a in ALPHAS:
    gaps = []
    for s in SEEDS:
        d = run(COURT_TRACE, a, s)
        if d: gaps.append((d["SIEVE"] - d["W-TinyLFU"]) * 100)
    orig_gaps_per_alpha[a] = (mean(gaps), stdev(gaps))
print("  done")

# 5 random selections of 75 1-access keys to strip
results = []
for sel_id in range(N_SELECTIONS):
    rnd = random.Random(1000 + sel_id)
    drop_set = set(rnd.sample(one_access_keys, K))

    trim_path = OUT_DIR / f"court_no75_random1ax_{sel_id}.csv"
    with open(trim_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "key", "size"])
        for r in rows:
            if r["key"] not in drop_set:
                w.writerow([r["timestamp"], r["key"], r["size"]])
    n_kept = len(rows) - len(drop_set & {r['key'] for r in rows})
    n_kept = sum(1 for r in rows if r['key'] not in drop_set)
    print(f"\n  Selection {sel_id} (seed={1000+sel_id}): trimmed to {n_kept} rows")

    sel_gaps = {}
    for a in ALPHAS:
        gaps = []
        for s in SEEDS:
            d = run(trim_path, a, s)
            if d: gaps.append((d["SIEVE"] - d["W-TinyLFU"]) * 100)
        sel_gaps[a] = (mean(gaps), stdev(gaps))
    results.append({"selection": sel_id, "drop_seed": 1000+sel_id, "gaps_pp": sel_gaps})
    for a in ALPHAS:
        m, s = sel_gaps[a]
        om, _ = orig_gaps_per_alpha[a]
        shrink = 100*(om - m)/om if abs(om) > 0.01 else 0
        print(f"    α={a}: gap = {m:+.2f} ± {s:.2f} pp  (orig {om:+.2f}; shrinkage {shrink:+.1f}%)")

# Aggregate across the 5 random selections
print(f"\n{'='*92}")
print(f"Mean across 5 random 75-key selections of one-access strips, 5 seeds each:")
print(f"{'='*92}")
print(f"{'α':>4}  | {'orig gap':>14}  | {'mean trimmed gap':>20}  | {'mean shrinkage':>14}")
print('-'*92)
agg = {}
for a in ALPHAS:
    selection_means = [r["gaps_pp"][a][0] for r in results]
    om, _ = orig_gaps_per_alpha[a]
    grand_mean = mean(selection_means)
    grand_std = stdev(selection_means) if len(selection_means)>1 else 0
    shrink = 100*(om - grand_mean)/om if abs(om) > 0.01 else 0
    print(f"  {a}   | {om:+6.2f} pp        |  {grand_mean:+6.2f} ± {grand_std:.2f} pp     |  {shrink:+6.1f}%")
    agg[a] = {
        "orig_gap_pp": round(om, 2),
        "trim_grand_mean_pp": round(grand_mean, 2),
        "trim_std_across_selections_pp": round(grand_std, 2),
        "shrinkage_pct": round(shrink, 1),
    }

with open(OUT_DIR / "summary.json", "w") as f:
    json.dump({"agg": agg, "K": K, "N_SELECTIONS": N_SELECTIONS,
               "n_one_access_keys": len(one_access_keys)}, f, indent=2)
print(f"\nWrote {OUT_DIR / 'summary.json'}")
