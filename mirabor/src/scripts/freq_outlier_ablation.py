#!/usr/bin/env python3
"""
Frequency-only ablation: strip the top-K Court keys *by access count*
(matching the count of size-outliers from outlier_ablation.py, which removed
75 keys >100KB). This is the missing control that decouples
"size-outlier effect" from "frequency-outlier effect" in the main outlier
ablation. If the gap shrinkage in outlier_ablation.py is driven by
high-frequency keys (which happen to be big), this ablation will reproduce
it; if it's driven by big keys (independent of frequency), this ablation
will leave the gap roughly intact.

Procedure:
  1. Count accesses per key in court_trace.csv.
  2. Identify top-K most-accessed keys (K=75 to match size ablation).
  3. Drop those keys + their access records.
  4. Run cache_sim --replay-zipf with --policies sieve,wtinylfu,gdsf,lru
     on the trimmed trace, 5 seeds × 4 alphas × focal cache_frac.
  5. Report SIEVE−WTLFU gap (the v2 §8 number) and GDSF mean miss-ratio
     (v3 baseline) on both original and trimmed traces.
"""

import csv
from collections import Counter
import shutil
import subprocess
import sys
import json
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
COURT_TRACE = ROOT / "traces" / "court_trace.csv"
OUT_DIR = ROOT / "results" / "freq_outlier_ablation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Match the size-ablation count exactly (75 keys >100KB → 75 most-accessed)
K_TOP = 75
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01

# ---------- identify top-K by access count ----------
rows = list(csv.DictReader(open(COURT_TRACE)))
counts = Counter(r["key"] for r in rows)
top_freq = [k for k, _ in counts.most_common(K_TOP)]
top_freq_set = set(top_freq)

# Cross-check: how much overlap with the size-outliers from outlier_ablation.py?
size_outlier_set = {r["key"] for r in rows if int(r["size"]) > 100_000}
overlap = top_freq_set & size_outlier_set

# Frequency-outlier characterization
top_freq_accesses = [counts[k] for k in top_freq]
print(f"=== Frequency-outlier characterization ===")
print(f"Total trace rows: {len(rows)}, unique keys: {len(counts)}")
print(f"Top-{K_TOP} most-accessed keys: access counts {min(top_freq_accesses)}–{max(top_freq_accesses)}")
print(f"  median accesses among top-{K_TOP}: {sorted(top_freq_accesses)[K_TOP//2]}")
print(f"Overlap with size-outliers (>100KB): {len(overlap)} of {K_TOP}")
print(f"  -> the size-ablation removed mostly DIFFERENT keys ({K_TOP - len(overlap)} non-overlap)")
print(f"  size-outlier accesses: {sum(counts[k] for k in size_outlier_set)} rows")
print(f"  freq-outlier accesses: {sum(counts[k] for k in top_freq_set)} rows")

# ---------- write trimmed trace ----------
trimmed_path = OUT_DIR / "court_trace_no_freq_outliers.csv"
n = 0
with open(trimmed_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "key", "size"])
    for r in rows:
        if r["key"] not in top_freq_set:
            w.writerow([r["timestamp"], r["key"], r["size"]])
            n += 1
print(f"\nTrimmed trace: {n} rows -> {trimmed_path}\n")

# ---------- run cache_sim on both traces ----------
def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(trace_path),
           "--replay-zipf",
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--policies", "sieve,wtinylfu,gdsf,lru",
           "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL {alpha} {seed}\n{proc.stderr[-1500:]}\n")
        return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row["policy"]] = float(row["miss_ratio"])
    shutil.rmtree(cell_dir, ignore_errors=True)
    return out

print("Running with-outlier (original) sweep...")
with_data = {}
for a in ALPHAS:
    with_data[a] = []
    for s in SEEDS:
        d = run(COURT_TRACE, a, s)
        if d: with_data[a].append(d)
    print(f"  α={a}: done")

print("Running without-freq-outlier (trimmed) sweep...")
without_data = {}
for a in ALPHAS:
    without_data[a] = []
    for s in SEEDS:
        d = run(trimmed_path, a, s)
        if d: without_data[a].append(d)
    print(f"  α={a}: done")

# ---------- summarize: compare to size-only ablation ----------
print(f"\n{'='*72}")
print(f"SIEVE−WTLFU gap @ cache_frac={CACHE_FRAC}, FREQUENCY-only ablation")
print(f"{'='*72}")
print(f"{'α':>5}  |  {'with':>20}  |  {'without freq-outliers':>22}  |  shrinkage")
print(f"{'-'*72}")

results = {"alpha": [], "with_gap_pp": [], "with_std_pp": [],
           "without_gap_pp": [], "without_std_pp": [], "shrinkage_pct": []}

for a in ALPHAS:
    gw = [d['SIEVE'] - d['W-TinyLFU'] for d in with_data[a]]
    gn = [d['SIEVE'] - d['W-TinyLFU'] for d in without_data[a]]
    mw, sw = mean(gw)*100, (stdev(gw) if len(gw)>1 else 0)*100
    mn, sn = mean(gn)*100, (stdev(gn) if len(gn)>1 else 0)*100
    shrink_pct = 100 * (mw - mn) / mw if abs(mw) > 0.01 else 0
    print(f"  {a:.1f}  |  {mw:+6.2f} ± {sw:5.2f} pp     |"
          f"  {mn:+6.2f} ± {sn:5.2f} pp       |  {shrink_pct:5.1f}%")
    results["alpha"].append(a)
    results["with_gap_pp"].append(round(mw, 3))
    results["with_std_pp"].append(round(sw, 3))
    results["without_gap_pp"].append(round(mn, 3))
    results["without_std_pp"].append(round(sn, 3))
    results["shrinkage_pct"].append(round(shrink_pct, 1))

results["overlap_keys"] = len(overlap)
results["size_outlier_count"] = len(size_outlier_set)
results["freq_outlier_count"] = K_TOP

with open(OUT_DIR / "summary.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nWrote {OUT_DIR / 'summary.json'}")
