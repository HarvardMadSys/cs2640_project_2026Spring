#!/usr/bin/env python3
"""
Outlier ablation: how much of the Court SIEVE-vs-W-TinyLFU gap survives
if we strip the 462KB document from the trace?

This is a fragility check on the v2 paper's headline mechanism story.
The CourtListener trace has one document at 462,490 bytes (335x the
median); much of the W-TinyLFU advantage on Court is attributed to
admission-filter rejection of this single object. If that's right, the
gap should shrink substantially when the outlier is removed.

Procedure:
  1. Load court_trace.csv.
  2. Identify all (key, size) pairs where size > THRESHOLD (e.g., 100KB).
  3. Drop those keys + all their access records.
  4. Save the trimmed trace to a temp file.
  5. Run cache_sim --replay-zipf --alpha-sweep on both original and
     trimmed traces, 5 seeds each.
  6. Report SIEVE-vs-W-TinyLFU gap per (alpha, seed) for both.
"""

import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
COURT_TRACE = ROOT / "traces" / "court_trace.csv"
OUT_DIR = ROOT / "results" / "outlier_ablation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUTLIER_THRESHOLD_BYTES = 100_000  # >100 KB counts as catastrophic outlier
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01

# ---------- inspect outliers ----------
rows = list(csv.DictReader(open(COURT_TRACE)))
print(f"Original Court trace: {len(rows)} rows, "
      f"{len(set(r['key'] for r in rows))} unique keys")
sizes = sorted([(int(r['size']), r['key']) for r in rows], reverse=True)
print(f"\nTop-5 sizes:")
for s, k in sizes[:5]:
    n_accesses = sum(1 for r in rows if r['key'] == k)
    print(f"  {s:8d} bytes   key={k}   accessed {n_accesses}x")

outlier_keys = {r['key'] for r in rows if int(r['size']) > OUTLIER_THRESHOLD_BYTES}
print(f"\nOutlier keys (>{OUTLIER_THRESHOLD_BYTES} bytes): {len(outlier_keys)}")
print(f"Outlier accesses removed: "
      f"{sum(1 for r in rows if r['key'] in outlier_keys)} of {len(rows)}")

# ---------- write trimmed trace ----------
trimmed_path = OUT_DIR / "court_trace_no_outliers.csv"
with open(trimmed_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "key", "size"])
    n = 0
    for r in rows:
        if r['key'] not in outlier_keys:
            w.writerow([r['timestamp'], r['key'], r['size']])
            n += 1
print(f"\nTrimmed trace: {n} rows -> {trimmed_path}\n")

# ---------- run cache_sim on both traces, 5 seeds, 4 alphas, focal cache_frac ----------
def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(trace_path),
           "--replay-zipf",
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--policies", "sieve,wtinylfu,lru",
           "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL {alpha} {seed}\n{proc.stderr[-1500:]}\n")
        return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row['policy']] = float(row['miss_ratio'])
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

print("Running without-outlier (trimmed) sweep...")
without_data = {}
for a in ALPHAS:
    without_data[a] = []
    for s in SEEDS:
        d = run(trimmed_path, a, s)
        if d: without_data[a].append(d)
    print(f"  α={a}: done")

# ---------- summarize ----------
print(f"\n{'='*72}")
print(f"SIEVE - W-TinyLFU gap @ cache_frac={CACHE_FRAC} (mean ± std across 5 seeds)")
print(f"{'='*72}")
print(f"{'α':>5}  |  {'with outlier':>20}  |  {'without outlier':>20}  |  shrinkage")
print(f"{'-'*68}")

import json
results = {"alpha": [], "with_outlier_gap_pp": [], "with_std_pp": [],
           "without_outlier_gap_pp": [], "without_std_pp": [], "shrinkage_pct": []}

for a in ALPHAS:
    gw = [d['SIEVE'] - d['W-TinyLFU'] for d in with_data[a]]
    gn = [d['SIEVE'] - d['W-TinyLFU'] for d in without_data[a]]
    mw, sw = mean(gw)*100, (stdev(gw) if len(gw)>1 else 0)*100
    mn, sn = mean(gn)*100, (stdev(gn) if len(gn)>1 else 0)*100
    shrink_pct = 100 * (mw - mn) / mw if abs(mw) > 0.01 else 0
    print(f"  {a:.1f}  |  {mw:+6.2f} ± {sw:5.2f} pp     |"
          f"  {mn:+6.2f} ± {sn:5.2f} pp     |  {shrink_pct:5.1f}%")
    results["alpha"].append(a)
    results["with_outlier_gap_pp"].append(round(mw, 3))
    results["with_std_pp"].append(round(sw, 3))
    results["without_outlier_gap_pp"].append(round(mn, 3))
    results["without_std_pp"].append(round(sn, 3))
    results["shrinkage_pct"].append(round(shrink_pct, 1))

with open(OUT_DIR / "summary.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nWrote {OUT_DIR / 'summary.json'}")
