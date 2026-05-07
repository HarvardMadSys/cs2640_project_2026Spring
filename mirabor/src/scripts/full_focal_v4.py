#!/usr/bin/env python3
"""
v4 focal sweep: 7 policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU,
GDSF, GDSF-Cost) on Congress and Court at the focal alpha overlay,
5 seeds, 4 cache fractions. Records BOTH object-miss and byte-miss for
every cell.

Output: results/v4/{summary,raw}.csv with columns workload, alpha, seed,
cache_frac, policy, miss_ratio, byte_miss_ratio, accesses_per_sec.
"""
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
OUT_DIR = ROOT / "results" / "v4"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WORKLOADS = [
    ("congress", ROOT / "traces" / "congress_trace.csv"),
    ("court",    ROOT / "traces" / "court_trace.csv"),
]
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]
POLICIES = "lru,fifo,clock,s3fifo,sieve,wtinylfu,gdsf,gdsf-cost"
cache_frac_str = ",".join(str(c) for c in CACHE_FRACS)

raw_rows = []
def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(trace_path),
           "--replay-zipf", "--alpha", str(alpha), "--seed", str(seed),
           "--policies", POLICIES, "--cache-sizes", cache_frac_str,
           "--output-dir", str(cell_dir)]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL {alpha} {seed}\n{proc.stderr[-1500:]}\n")
        return [], 0
    rows = []
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            rows.append({
                "cache_frac":     float(row["cache_frac"]),
                "policy":         row["policy"],
                "miss_ratio":     float(row["miss_ratio"]),
                "byte_miss_ratio": float(row["byte_miss_ratio"]),
                "accesses_per_sec": float(row["accesses_per_sec"]),
            })
    shutil.rmtree(cell_dir, ignore_errors=True)
    return rows, elapsed

print(f"v4 focal sweep: {len(WORKLOADS)}×{len(ALPHAS)}×{len(SEEDS)} cells, "
      f"7+1 policies, {len(CACHE_FRACS)} cache fractions, both miss-types")
total_elapsed = 0
for wname, wpath in WORKLOADS:
    for a in ALPHAS:
        cell_t = 0
        for s in SEEDS:
            rows, et = run(wpath, a, s)
            cell_t += et
            for r in rows:
                raw_rows.append({"workload": wname, "alpha": a, "seed": s, **r})
        total_elapsed += cell_t
        print(f"  {wname} α={a}: {cell_t:.1f}s")
print(f"\nTotal: {total_elapsed:.1f}s wall-clock")

with open(OUT_DIR / "raw.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=raw_rows[0].keys())
    w.writeheader()
    for r in raw_rows: w.writerow(r)

# ---------- summary: mean/std per cell, both metrics ----------
def agg(workload, alpha, cf, policy, key):
    vals = [r[key] for r in raw_rows
            if r["workload"]==workload and r["alpha"]==alpha
            and r["cache_frac"]==cf and r["policy"]==policy]
    return (mean(vals), stdev(vals) if len(vals)>1 else 0.0) if vals else (None, None)

summary = []
for wname, _ in WORKLOADS:
    for a in ALPHAS:
        for cf in CACHE_FRACS:
            for p in ["LRU","FIFO","CLOCK","S3-FIFO","SIEVE","W-TinyLFU","GDSF","GDSF-Cost"]:
                m_obj, s_obj = agg(wname, a, cf, p, "miss_ratio")
                m_byte, s_byte = agg(wname, a, cf, p, "byte_miss_ratio")
                m_thr, _ = agg(wname, a, cf, p, "accesses_per_sec")
                if m_obj is None: continue
                summary.append({
                    "workload": wname, "alpha": a, "cache_frac": cf, "policy": p,
                    "obj_miss_mean": round(m_obj, 5), "obj_miss_std": round(s_obj, 5),
                    "byte_miss_mean": round(m_byte, 5), "byte_miss_std": round(s_byte, 5),
                    "throughput_acc_per_sec": int(m_thr),
                })

with open(OUT_DIR / "summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=summary[0].keys())
    w.writeheader()
    for r in summary: w.writerow(r)

# ---------- print: GDSF/GDSF-Cost/W-TinyLFU/SIEVE on object AND byte miss ----------
print(f"\n{'='*100}")
print(f"v4 focal table: object-miss vs byte-miss, GDSF / GDSF-Cost / W-TinyLFU / SIEVE")
print(f"{'='*100}")
print(f"{'WL':<8} {'α':>4} {'cf':>6} | {'OBJECT-MISS (mean)':<40} | {'BYTE-MISS (mean)':<40}")
print(f"{'':8} {'':4} {'':6} | {'GDSF':>9} {'GDSFc':>9} {'WTLFU':>9} {'SIEVE':>9} | {'GDSF':>9} {'GDSFc':>9} {'WTLFU':>9} {'SIEVE':>9}")
print('-'*100)
for wname, _ in WORKLOADS:
    for a in ALPHAS:
        for cf in CACHE_FRACS:
            def lookup(p, key):
                for r in summary:
                    if r["workload"]==wname and r["alpha"]==a and r["cache_frac"]==cf and r["policy"]==p:
                        return r[key]
                return None
            o_g = lookup("GDSF", "obj_miss_mean")
            o_gc = lookup("GDSF-Cost", "obj_miss_mean")
            o_w = lookup("W-TinyLFU", "obj_miss_mean")
            o_s = lookup("SIEVE", "obj_miss_mean")
            b_g = lookup("GDSF", "byte_miss_mean")
            b_gc = lookup("GDSF-Cost", "byte_miss_mean")
            b_w = lookup("W-TinyLFU", "byte_miss_mean")
            b_s = lookup("SIEVE", "byte_miss_mean")
            print(f"{wname:<8} {a:>4} {cf:>6} | {o_g:>9.4f} {o_gc:>9.4f} {o_w:>9.4f} {o_s:>9.4f} | "
                  f"{b_g:>9.4f} {b_gc:>9.4f} {b_w:>9.4f} {b_s:>9.4f}")

print(f"\nWrote {OUT_DIR/'raw.csv'} and {OUT_DIR/'summary.csv'}")
