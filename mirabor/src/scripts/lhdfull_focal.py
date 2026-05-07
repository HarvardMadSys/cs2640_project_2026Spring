#!/usr/bin/env python3
"""LHD-Full focal sweep on Court + Congress."""
import csv, shutil, subprocess, sys, time
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
OUT_DIR = ROOT / "results" / "v5_lhdfull"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WORKLOADS = [("congress", ROOT/"traces"/"congress_trace.csv"),
             ("court",    ROOT/"traces"/"court_trace.csv")]
SEEDS = [42, 43, 44, 45, 46]
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]
POLICIES = "sieve,wtinylfu,gdsf,lhd-full"
cf_str = ",".join(str(c) for c in CACHE_FRACS)

raw = []
def run(tp, a, s):
    cd = OUT_DIR / f"_run_{tp.stem}_a{a}_d{s}"
    cd.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM), "--trace", str(tp), "--replay-zipf",
           "--alpha", str(a), "--seed", str(s),
           "--policies", POLICIES, "--cache-sizes", cf_str,
           "--output-dir", str(cd)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(f"FAIL a={a} s={s}\n{p.stderr[-1500:]}\n")
        return []
    rows = []
    with open(cd/"mrc.csv") as f:
        for r in csv.DictReader(f):
            rows.append({"cache_frac": float(r["cache_frac"]),
                         "policy": r["policy"],
                         "miss_ratio": float(r["miss_ratio"]),
                         "byte_miss_ratio": float(r["byte_miss_ratio"])})
    shutil.rmtree(cd, ignore_errors=True)
    return rows

t0 = time.time()
for wn, wp in WORKLOADS:
    for a in ALPHAS:
        for s in SEEDS:
            for r in run(wp, a, s):
                raw.append({"workload": wn, "alpha": a, "seed": s, **r})
        print(f"  {wn} α={a}: done")
print(f"Total: {time.time()-t0:.1f}s")

with open(OUT_DIR/"raw.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=raw[0].keys())
    w.writeheader()
    [w.writerow(r) for r in raw]

def agg(wn, a, cf, p, k):
    v = [r[k] for r in raw if r["workload"]==wn and r["alpha"]==a
         and r["cache_frac"]==cf and r["policy"]==p]
    return (mean(v), stdev(v) if len(v)>1 else 0.0) if v else (None, None)

print(f"\n{'='*100}")
print(f"Court focal: object-miss / byte-miss")
print(f"{'='*100}")
print(f"{'α':>4} {'cf':>6} | {'LHD-Full':>20}  {'GDSF':>20}  {'W-TinyLFU':>20}  {'SIEVE':>20}")
print('-'*100)
for a in ALPHAS:
    for cf in CACHE_FRACS:
        l_o, l_os = agg("court", a, cf, "LHD-Full", "miss_ratio")
        l_b, _    = agg("court", a, cf, "LHD-Full", "byte_miss_ratio")
        g_o, _    = agg("court", a, cf, "GDSF", "miss_ratio")
        g_b, _    = agg("court", a, cf, "GDSF", "byte_miss_ratio")
        w_o, _    = agg("court", a, cf, "W-TinyLFU", "miss_ratio")
        w_b, _    = agg("court", a, cf, "W-TinyLFU", "byte_miss_ratio")
        s_o, _    = agg("court", a, cf, "SIEVE", "miss_ratio")
        s_b, _    = agg("court", a, cf, "SIEVE", "byte_miss_ratio")
        print(f"{a:>4} {cf:>6} | {l_o:.4f}/{l_b:.4f}    {g_o:.4f}/{g_b:.4f}    "
              f"{w_o:.4f}/{w_b:.4f}    {s_o:.4f}/{s_b:.4f}")

# build summary csv
summ = []
for wn,_ in WORKLOADS:
    for a in ALPHAS:
        for cf in CACHE_FRACS:
            for p in ["LHD-Full","GDSF","W-TinyLFU","SIEVE"]:
                m_o, s_o = agg(wn, a, cf, p, "miss_ratio")
                m_b, s_b = agg(wn, a, cf, p, "byte_miss_ratio")
                if m_o is None: continue
                summ.append({"workload":wn, "alpha":a, "cache_frac":cf, "policy":p,
                            "obj_miss_mean": round(m_o,5), "obj_miss_std": round(s_o,5),
                            "byte_miss_mean": round(m_b,5), "byte_miss_std": round(s_b,5)})
with open(OUT_DIR/"summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=summ[0].keys())
    w.writeheader()
    [w.writerow(r) for r in summ]
print(f"\nWrote {OUT_DIR/'summary.csv'}")
