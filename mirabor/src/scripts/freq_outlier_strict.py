#!/usr/bin/env python3
"""
Stricter version of frequency-only ablation. The first attempt
(freq_outlier_ablation.py) padded the top-75 with one-access keys because
Court has so many tied-at-one keys. This run strips only keys whose
access count is meaningfully high (>=10), which is the actual frequency
distribution Counter targets.

If the size-ablation's 62-78% shrinkage were driven by frequency, this
ablation should reproduce most of it. If size is the actual driver,
this ablation should leave the gap intact.
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

THRESHOLD = 10
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01

rows = list(csv.DictReader(open(COURT_TRACE)))
counts = Counter(r["key"] for r in rows)
high_freq_set = {k for k, c in counts.items() if c >= THRESHOLD}
size_outlier_set = {r["key"] for r in rows if int(r["size"]) > 100_000}
overlap = high_freq_set & size_outlier_set

print(f"=== Strict frequency-outlier characterization ===")
print(f"Keys with >={THRESHOLD} accesses: {len(high_freq_set)}")
print(f"Their access counts (range): "
      f"{min(counts[k] for k in high_freq_set)}–{max(counts[k] for k in high_freq_set)}")
print(f"Their access count sum: {sum(counts[k] for k in high_freq_set)} of 20000 rows")
print(f"Overlap with size-outliers (>100KB): {len(overlap)} of {len(high_freq_set)}")
print(f"  -> {len(high_freq_set)-len(overlap)} are high-freq but small-size")

trimmed_path = OUT_DIR / f"court_trace_no_high_freq_ge{THRESHOLD}.csv"
n = 0
with open(trimmed_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "key", "size"])
    for r in rows:
        if r["key"] not in high_freq_set:
            w.writerow([r["timestamp"], r["key"], r["size"]])
            n += 1
print(f"\nTrimmed trace: {n} rows -> {trimmed_path}\n")

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

print("Original sweep...")
with_data = {a: [run(COURT_TRACE, a, s) for s in SEEDS] for a in ALPHAS}
print("Trimmed sweep...")
without_data = {a: [run(trimmed_path, a, s) for s in SEEDS] for a in ALPHAS}

print(f"\n{'='*78}")
print(f"SIEVE−WTLFU gap, STRICT freq-only ablation (keys w/ >={THRESHOLD} accesses removed)")
print(f"{'='*78}")
print(f"{'α':>5}  |  {'with':>20}  |  {'without freq>={}':>23}  |  shrinkage".format(THRESHOLD))
print(f"{'-'*78}")
results = {"threshold": THRESHOLD, "alpha": [], "with_gap_pp": [], "without_gap_pp": [],
           "shrinkage_pct": [], "high_freq_count": len(high_freq_set),
           "high_freq_size_overlap": len(overlap)}
for a in ALPHAS:
    gw = [d['SIEVE'] - d['W-TinyLFU'] for d in with_data[a] if d]
    gn = [d['SIEVE'] - d['W-TinyLFU'] for d in without_data[a] if d]
    mw, sw = mean(gw)*100, (stdev(gw) if len(gw)>1 else 0)*100
    mn, sn = mean(gn)*100, (stdev(gn) if len(gn)>1 else 0)*100
    shrink = 100*(mw-mn)/mw if abs(mw)>0.01 else 0
    print(f"  {a:.1f}  |  {mw:+6.2f} ± {sw:5.2f} pp     |"
          f"  {mn:+6.2f} ± {sn:5.2f} pp        |  {shrink:5.1f}%")
    results["alpha"].append(a)
    results["with_gap_pp"].append(round(mw, 3))
    results["without_gap_pp"].append(round(mn, 3))
    results["shrinkage_pct"].append(round(shrink, 1))

with open(OUT_DIR / "strict_summary.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nWrote {OUT_DIR / 'strict_summary.json'}")
