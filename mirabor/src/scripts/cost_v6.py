#!/usr/bin/env python3
"""
v6 cost analysis with correct latency and dollar economics.

Cost models per request:

  mean_latency = obj_miss × TTFB + (1 − obj_miss) × L_cache
                 (TTFB-dominated; size/throughput negligible at our sizes)

  tail_latency = obj_miss × p99_origin_lat
                 (p99 origin response is what tail-latency observers see)

  dollar_cost  = obj_miss × $req_per_miss + byte_miss × E[size] × $byte
                 AWS S3-like: $req = $0.0004/1000, $byte = $0.09/GB

  egress_only  = byte_miss × E[size] × $byte
                 The pure-bandwidth cost.

For each cost model we report per-cell winners, plus *significance*:
a winner is "robust" only if its mean cost is more than the standard
error of the difference below the runner-up's mean. We use the
multi-seed std from summary.csv.
"""
import csv
import math
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "results" / "v5_lhdfull" / "summary.csv"

TTFB_MS = 358.0
P99_MS  = 1020.0
L_CACHE_MS = 1.0
REQ_DOLLAR  = 0.0004 / 1000      # $/request
BYTE_DOLLAR = 0.09 / 1e9         # $/byte
# Per-workload mean object size (from workload table)
SIZE_AVG = {"court": 12000.0, "congress": 250.0}

rows = list(csv.DictReader(open(SUMMARY)))
for r in rows:
    r["alpha"] = float(r["alpha"])
    r["cache_frac"] = float(r["cache_frac"])
    r["obj_miss_mean"] = float(r["obj_miss_mean"])
    r["obj_miss_std"]  = float(r["obj_miss_std"])
    r["byte_miss_mean"] = float(r["byte_miss_mean"])
    r["byte_miss_std"]  = float(r["byte_miss_std"])

def cost_model(name, r, wname):
    o, ostd = r["obj_miss_mean"], r["obj_miss_std"]
    b, bstd = r["byte_miss_mean"], r["byte_miss_std"]
    s = SIZE_AVG[wname]
    if name == "obj_only":         return o, ostd
    if name == "byte_only":        return b, bstd
    if name == "mean_latency_ms":  return o * TTFB_MS + (1 - o) * L_CACHE_MS, ostd * TTFB_MS
    if name == "tail_latency_ms":  return o * P99_MS, ostd * P99_MS
    if name == "egress_dollar":    return b * s * BYTE_DOLLAR, bstd * s * BYTE_DOLLAR
    if name == "total_dollar":
        # cost = obj_miss × $req + byte_miss × E[size] × $byte
        c = o * REQ_DOLLAR + b * s * BYTE_DOLLAR
        # err: assuming independent (conservative)
        e = math.sqrt((ostd*REQ_DOLLAR)**2 + (bstd*s*BYTE_DOLLAR)**2)
        return c, e
    raise ValueError(name)

MODELS = ["obj_only", "byte_only", "mean_latency_ms", "tail_latency_ms",
          "egress_dollar", "total_dollar"]

cells = sorted(set((r["workload"], r["alpha"], r["cache_frac"]) for r in rows))

# Per-cell winners with robustness check
robust_winners = {m: Counter() for m in MODELS}
all_winners    = {m: Counter() for m in MODELS}
court_robust   = {m: Counter() for m in MODELS}
court_all      = {m: Counter() for m in MODELS}

print(f"{'cell':<28} | " + " | ".join(f"{m:>22}" for m in MODELS))
print('-' * (30 + 25 * len(MODELS)))

for wn, alpha, cf in cells:
    cell_rows = [r for r in rows if r["workload"]==wn and r["alpha"]==alpha and r["cache_frac"]==cf]
    line = f"{wn} α={alpha} cf={cf:<7}|"
    for m in MODELS:
        scored = [(*cost_model(m, r, wn), r["policy"]) for r in cell_rows]
        scored.sort()
        winner_cost, winner_std, winner_pol = scored[0]
        runner_cost, runner_std, runner_pol = scored[1] if len(scored) > 1 else scored[0]
        # robustness: gap > pooled std
        pooled_std = math.sqrt(winner_std**2 + runner_std**2)
        robust = (runner_cost - winner_cost) > pooled_std
        all_winners[m][winner_pol] += 1
        if robust: robust_winners[m][winner_pol] += 1
        if wn == "court":
            court_all[m][winner_pol] += 1
            if robust: court_robust[m][winner_pol] += 1
        mark = "" if robust else "*"
        line += f" {winner_pol:>10}{mark:<2}|"
    print(line)

print("\n* = winner not robust to seed-std (gap < pooled std). Win counted but flagged.\n")

print("="*80)
print("Aggregate winners (robust / all of 32 cells)")
print("="*80)
for m in MODELS:
    rob = ", ".join(f"{p}: {n}" for p, n in robust_winners[m].most_common())
    all_ = ", ".join(f"{p}: {n}" for p, n in all_winners[m].most_common())
    print(f"  {m:<20} robust: {rob}")
    print(f"  {' '*20}    all: {all_}")

print(f"\n=== Court only (16 cells, robust) ===")
for m in MODELS:
    rob = ", ".join(f"{p}: {n}" for p, n in court_robust[m].most_common())
    all_ = ", ".join(f"{p}: {n}" for p, n in court_all[m].most_common())
    print(f"  {m:<20} robust: {rob}")
    print(f"  {' '*20}    all: {all_}")

# Per-cell numerical demonstration: dollar cost on Court at α=1.0 cf=0.01
print(f"\n=== Court α=1.0 cf=0.01 cost breakdown ===")
target = [r for r in rows if r["workload"]=="court" and r["alpha"]==1.0 and r["cache_frac"]==0.01]
for r in target:
    o, b = r["obj_miss_mean"], r["byte_miss_mean"]
    lat = o * TTFB_MS + (1-o) * L_CACHE_MS
    tail = o * P99_MS
    egress = b * SIZE_AVG["court"] * BYTE_DOLLAR
    total = o * REQ_DOLLAR + egress
    print(f"  {r['policy']:>10}: obj={o:.3f}  byte={b:.3f}  "
          f"mean_lat={lat:6.1f}ms  tail={tail:6.0f}ms  "
          f"egress=${egress*1e6:.2f}/Mreq  total=${total*1e6:.2f}/Mreq")
