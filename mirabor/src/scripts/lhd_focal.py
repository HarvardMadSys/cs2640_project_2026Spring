#!/usr/bin/env python3
"""
LHD focal sweep: 9 policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU,
GDSF, GDSF-Cost, LHD) on Congress and Court at the focal alpha overlay,
5 seeds, 4 cache fractions. Records BOTH object-miss and byte-miss.
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
OUT_DIR = ROOT / "results" / "v4_lhd"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WORKLOADS = [
    ("congress", ROOT / "traces" / "congress_trace.csv"),
    ("court",    ROOT / "traces" / "court_trace.csv"),
]
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]
POLICIES = "lru,sieve,wtinylfu,gdsf,gdsf-cost,lhd"
cache_frac_str = ",".join(str(c) for c in CACHE_FRACS)

raw_rows = []
def run(trace_path, alpha, seed):
    cell_dir = OUT_DIR / f"_run_{trace_path.stem}_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM), "--trace", str(trace_path),
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
                "cache_frac": float(row["cache_frac"]),
                "policy":     row["policy"],
                "miss_ratio": float(row["miss_ratio"]),
                "byte_miss_ratio": float(row["byte_miss_ratio"]),
                "accesses_per_sec": float(row["accesses_per_sec"]),
            })
    shutil.rmtree(cell_dir, ignore_errors=True)
    return rows, elapsed

print(f"LHD focal sweep: {len(WORKLOADS)}×{len(ALPHAS)}×{len(SEEDS)} cells, {len(POLICIES.split(','))} policies")
total = 0
for wname, wpath in WORKLOADS:
    for a in ALPHAS:
        cell_t = 0
        for s in SEEDS:
            rows, et = run(wpath, a, s)
            cell_t += et
            for r in rows:
                raw_rows.append({"workload": wname, "alpha": a, "seed": s, **r})
        total += cell_t
        print(f"  {wname} α={a}: {cell_t:.1f}s")
print(f"\nTotal: {total:.1f}s wall-clock")

with open(OUT_DIR / "raw.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=raw_rows[0].keys())
    w.writeheader()
    for r in raw_rows: w.writerow(r)

# ---------- summary ----------
def agg(workload, alpha, cf, policy, key):
    vals = [r[key] for r in raw_rows
            if r["workload"]==workload and r["alpha"]==alpha
            and r["cache_frac"]==cf and r["policy"]==policy]
    return (mean(vals), stdev(vals) if len(vals)>1 else 0.0) if vals else (None, None)

summary = []
for wname, _ in WORKLOADS:
    for a in ALPHAS:
        for cf in CACHE_FRACS:
            for p in ["LRU","SIEVE","W-TinyLFU","GDSF","GDSF-Cost","LHD"]:
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

# ---------- print: Court focal LHD vs GDSF vs W-TinyLFU vs SIEVE ----------
print(f"\n{'='*108}")
print(f"Court focal: object-miss / byte-miss for LHD vs GDSF vs W-TinyLFU vs SIEVE")
print(f"{'='*108}")
print(f"{'α':>4} {'cf':>6} | {'LHD obj/byte':>20}  {'GDSF obj/byte':>20}  {'WTLFU obj/byte':>20}  {'SIEVE obj/byte':>20}")
print('-'*108)
for a in ALPHAS:
    for cf in CACHE_FRACS:
        def lookup(p, key):
            for r in summary:
                if r["workload"]=="court" and r["alpha"]==a and r["cache_frac"]==cf and r["policy"]==p:
                    return r[key]
            return None
        lhd_o, lhd_b   = lookup("LHD","obj_miss_mean"),       lookup("LHD","byte_miss_mean")
        gdsf_o, gdsf_b = lookup("GDSF","obj_miss_mean"),      lookup("GDSF","byte_miss_mean")
        wt_o, wt_b     = lookup("W-TinyLFU","obj_miss_mean"), lookup("W-TinyLFU","byte_miss_mean")
        sv_o, sv_b     = lookup("SIEVE","obj_miss_mean"),     lookup("SIEVE","byte_miss_mean")
        print(f"{a:>4} {cf:>6} | {lhd_o:.4f}/{lhd_b:.4f}    "
              f"{gdsf_o:.4f}/{gdsf_b:.4f}    "
              f"{wt_o:.4f}/{wt_b:.4f}    "
              f"{sv_o:.4f}/{sv_b:.4f}")

print(f"\nWrote {OUT_DIR/'raw.csv'} and {OUT_DIR/'summary.csv'}")

# ---------- LHD throughput summary ----------
print(f"\n=== LHD throughput vs others ===")
for cf in CACHE_FRACS:
    print(f"  Court cf={cf}:")
    for p in ["LHD", "GDSF", "W-TinyLFU", "SIEVE", "LRU"]:
        thrs = [r["throughput_acc_per_sec"] for r in summary
                if r["workload"]=="court" and r["cache_frac"]==cf and r["policy"]==p]
        if thrs:
            print(f"    {p:>10}: {sum(thrs)/len(thrs):>10.0f} acc/sec")
