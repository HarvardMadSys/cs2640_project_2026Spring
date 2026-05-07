#!/usr/bin/env python3
"""
Run GDSF + 6 baseline policies on Congress and Court at the focal alpha
overlay (5 seeds, 4 alphas, cache_frac in {0.001, 0.005, 0.01, 0.05}).

Output: results/gdsf/{congress,court}_focal.csv with one row per
(workload, alpha, seed, cache_frac, policy, miss_ratio, byte_miss_ratio).
"""

import csv
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
OUT_DIR = ROOT / "results" / "gdsf"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WORKLOADS = [
    ("congress", ROOT / "traces" / "congress_trace.csv"),
    ("court",    ROOT / "traces" / "court_trace.csv"),
]
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]
POLICIES = "lru,fifo,clock,s3fifo,sieve,wtinylfu,gdsf"

cache_frac_str = ",".join(str(c) for c in CACHE_FRACS)

def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(trace_path),
           "--replay-zipf",
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--policies", POLICIES,
           "--cache-sizes", cache_frac_str,
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL {alpha} {seed}\n{proc.stderr[-1500:]}\n")
        return []
    rows = []
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            rows.append({
                "cache_frac": float(row["cache_frac"]),
                "policy":     row["policy"],
                "miss_ratio": float(row["miss_ratio"]),
                "byte_miss_ratio": float(row["byte_miss_ratio"]),
            })
    shutil.rmtree(cell_dir, ignore_errors=True)
    return rows

raw_rows = []
for wname, wpath in WORKLOADS:
    for a in ALPHAS:
        for s in SEEDS:
            for r in run(wpath, a, s):
                raw_rows.append({
                    "workload":   wname,
                    "alpha":      a,
                    "seed":       s,
                    "cache_frac": r["cache_frac"],
                    "policy":     r["policy"],
                    "miss_ratio": r["miss_ratio"],
                    "byte_miss_ratio": r["byte_miss_ratio"],
                })
        print(f"  {wname} α={a}: {sum(1 for x in raw_rows if x['workload']==wname and x['alpha']==a)} rows")

with open(OUT_DIR / "raw.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=raw_rows[0].keys())
    w.writeheader()
    for r in raw_rows: w.writerow(r)

# ---------- summarize: GDSF vs W-TinyLFU vs SIEVE @ each cache_frac ----------
def agg(workload, alpha, cache_frac, policy):
    vals = [r["miss_ratio"] for r in raw_rows
            if r["workload"]==workload and r["alpha"]==alpha
            and r["cache_frac"]==cache_frac and r["policy"]==policy]
    return mean(vals), (stdev(vals) if len(vals)>1 else 0.0)

print(f"\n{'='*84}")
print(f"GDSF vs W-TinyLFU vs SIEVE — miss-ratio @ focal cells (5-seed mean ± std)")
print(f"{'='*84}")
print(f"{'workload':<10} {'α':>5} {'cf':>7} | {'GDSF':>14} | {'SIEVE':>14} | {'W-TLFU':>14}  GDSF−WTLFU")
print("-"*84)
summary_rows = []
for wname, _ in WORKLOADS:
    for a in ALPHAS:
        for cf in CACHE_FRACS:
            mg, sg = agg(wname, a, cf, "GDSF")
            ms, ss = agg(wname, a, cf, "SIEVE")
            mw, sw = agg(wname, a, cf, "W-TinyLFU")
            gap_pp = (mg - mw) * 100
            print(f"{wname:<10} {a:>5} {cf:>7} | {mg:.4f}±{sg:.4f} | {ms:.4f}±{ss:.4f} | "
                  f"{mw:.4f}±{sw:.4f}  {gap_pp:+5.2f}pp")
            summary_rows.append({"workload": wname, "alpha": a, "cache_frac": cf,
                "GDSF_mean": round(mg, 5), "GDSF_std": round(sg, 5),
                "SIEVE_mean": round(ms, 5), "SIEVE_std": round(ss, 5),
                "WTLFU_mean": round(mw, 5), "WTLFU_std": round(sw, 5),
                "GDSF_minus_WTLFU_pp": round(gap_pp, 3)})

with open(OUT_DIR / "summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
    w.writeheader()
    for r in summary_rows: w.writerow(r)

print(f"\nWrote {OUT_DIR / 'summary.csv'} and {OUT_DIR / 'raw.csv'}")
