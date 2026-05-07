#!/usr/bin/env python3
"""
Empirical cost-vs-size fit. The v3 paper's GDSF implementation uses cost=1
uniform; the v3 critique flagged this as a hidden simplification. To know
whether it matters, we fit per-request response time (extracted from gap
between consecutive timestamps minus configured pacing) against the size
of the object fetched at that index, and check whether the relationship is
flat (cost=1 is fine) or sloped (need cost-aware GDSF).

Output:
  - per-bucket median response time
  - linear fit slope (ms per KB)
  - JSON with fitted (intercept_ms, slope_ms_per_byte) for use by GDSF-Cost
"""
import csv
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

TRACES = [
    ("Congress", ROOT / "traces" / "congress_trace.csv", 1200, 1200),
    ("Court",    ROOT / "traces" / "court_trace.csv",    800,  1200),
]

OUT = ROOT / "results" / "sweep_alpha_sigma" / "cost_vs_size_fit.json"

def load_trace(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append((int(r["timestamp"]), r["key"], int(r["size"])))
    return rows

summary = {}
for name, path, smin, smax in TRACES:
    rows = load_trace(path)
    sleep_mean = (smin + smax) / 2.0
    # cost[i] = response time for fetch i = gap[i] - sleep_after[i]
    # sleep_after[i] is jittered between smin and smax; expected value sleep_mean
    cost_size_pairs = []
    for i in range(len(rows) - 1):
        gap = rows[i+1][0] - rows[i][0]
        rt = gap - sleep_mean
        if 0 < rt < 30000:
            cost_size_pairs.append((rows[i][2], rt))  # (size_bytes, response_ms)
    if not cost_size_pairs: continue
    sizes = np.array([p[0] for p in cost_size_pairs])
    costs = np.array([p[1] for p in cost_size_pairs])

    # Linear fit
    A = np.vstack([sizes, np.ones_like(sizes)]).T
    slope_per_byte, intercept = np.linalg.lstsq(A, costs, rcond=None)[0]

    # Per-bucket medians
    buckets = [
        (0, 1000, "<1KB"),
        (1000, 5000, "1-5KB"),
        (5000, 20000, "5-20KB"),
        (20000, 100000, "20-100KB"),
        (100000, 1000000, ">100KB"),
    ]
    bucket_stats = []
    for lo, hi, label in buckets:
        mask = (sizes >= lo) & (sizes < hi)
        if mask.sum() < 5: continue
        bucket_stats.append({
            "size_range": label,
            "n": int(mask.sum()),
            "median_size_bytes": float(np.median(sizes[mask])),
            "median_response_ms": float(np.median(costs[mask])),
            "p25_response_ms": float(np.percentile(costs[mask], 25)),
            "p75_response_ms": float(np.percentile(costs[mask], 75)),
        })
    # Pearson correlation as sanity check
    if costs.std() > 0 and sizes.std() > 0:
        r = float(np.corrcoef(sizes, costs)[0, 1])
    else:
        r = 0.0
    summary[name] = {
        "n_samples":          len(cost_size_pairs),
        "size_range":         [int(sizes.min()), int(sizes.max())],
        "linear_fit": {
            "intercept_ms":          float(intercept),
            "slope_ms_per_byte":     float(slope_per_byte),
            "slope_ms_per_kb":       float(slope_per_byte * 1000),
            "pearson_r":             r,
        },
        "bucket_medians": bucket_stats,
    }
    print(f"\n=== {name} cost-vs-size ===")
    print(f"  Linear fit:  cost_ms = {intercept:.0f} + {slope_per_byte*1000:.3f} ms/KB * size")
    print(f"  Pearson r = {r:+.3f}  (n = {len(cost_size_pairs)})")
    print(f"  Per-bucket medians:")
    for b in bucket_stats:
        print(f"    {b['size_range']:>10}  n={b['n']:>5}  "
              f"median size={b['median_size_bytes']/1024:>6.1f} KB  "
              f"median rt={b['median_response_ms']:>6.0f} ms")

# ---------- combined fit (used by gdsf-cost) ----------
all_sizes = []
all_costs = []
for name, path, smin, smax in TRACES:
    rows = load_trace(path)
    sm = (smin+smax)/2
    for i in range(len(rows)-1):
        gap = rows[i+1][0] - rows[i][0]
        rt = gap - sm
        if 0 < rt < 30000:
            all_sizes.append(rows[i][2])
            all_costs.append(rt)
sizes = np.array(all_sizes); costs = np.array(all_costs)
A = np.vstack([sizes, np.ones_like(sizes)]).T
slope, intercept = np.linalg.lstsq(A, costs, rcond=None)[0]
r = float(np.corrcoef(sizes, costs)[0, 1])

summary["combined"] = {
    "n_samples": len(all_sizes),
    "size_range": [int(sizes.min()), int(sizes.max())],
    "linear_fit": {
        "intercept_ms": float(intercept),
        "slope_ms_per_byte": float(slope),
        "slope_ms_per_kb": float(slope * 1000),
        "pearson_r": r,
    },
}
print(f"\n=== Combined fit (used by GDSF-Cost) ===")
print(f"  cost_ms = {intercept:.0f} + {slope*1000:.4f} ms/KB * size_bytes")
print(f"  Pearson r = {r:+.4f}")
print(f"\nFor a 1KB object:   cost ≈ {intercept + slope*1000:.0f} ms")
print(f"For a 100KB object: cost ≈ {intercept + slope*100000:.0f} ms")
print(f"For a 462KB object: cost ≈ {intercept + slope*462000:.0f} ms")

ratio = (intercept + slope*462000) / (intercept + slope*1000)
print(f"\nCost ratio 462KB / 1KB = {ratio:.2f}")
if ratio < 1.5:
    print(f"  -> Cost is roughly uniform across sizes; cost=1 baseline is defensible")
elif ratio > 3:
    print(f"  -> Cost varies substantially with size; cost-aware GDSF needed")
else:
    print(f"  -> Cost varies moderately with size; cost-aware GDSF may matter")

with open(OUT, "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nWrote {OUT}")
