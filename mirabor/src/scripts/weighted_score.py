#!/usr/bin/env python3
"""
Per-cell weighted-score winner under multiple cost weightings.

For each (workload, alpha, cache_frac) cell and each weighting:
  - obj-only: cost = obj_miss * 1
  - byte-only: cost = byte_miss * 1
  - equal-unit: cost = obj_miss + byte_miss (treats both equally)
  - latency-equal: cost = obj_miss * L_obj + byte_miss * L_byte
    where L_obj = 358 ms (typical object miss adds origin p50)
          L_byte = 1020 ms (heavy bytes hit p90 origin tail)

Pick the policy with min cost per cell, tally wins per weighting.
"""
import csv
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "results" / "v5_lhdfull" / "summary.csv"

L_OBJ = 358   # ms — origin p50 (per-request typical)
L_BYTE = 1020 # ms — origin p90 tail (heavy fetches)

rows = list(csv.DictReader(open(SUMMARY)))
for r in rows:
    r["alpha"] = float(r["alpha"])
    r["cache_frac"] = float(r["cache_frac"])
    r["obj_miss_mean"] = float(r["obj_miss_mean"])
    r["byte_miss_mean"] = float(r["byte_miss_mean"])

WEIGHTINGS = {
    "obj_only":     lambda o, b: o,
    "byte_only":    lambda o, b: b,
    "equal_unit":   lambda o, b: o + b,
    "latency_p50":  lambda o, b: o * L_OBJ + b * L_BYTE,  # weighted by latency cost
}

cells = sorted(set((r["workload"], r["alpha"], r["cache_frac"]) for r in rows))
print(f"Per-cell weighted-score winners ({len(cells)} cells × {len(WEIGHTINGS)} weightings)")
print(f"L_obj = {L_OBJ}ms (typical)  L_byte = {L_BYTE}ms (tail-event multiplier)\n")

per_weighting_winners = {w: Counter() for w in WEIGHTINGS}
per_weighting_winners_court = {w: Counter() for w in WEIGHTINGS}

# detail per cell
print(f"{'workload':<10} {'α':>4} {'cf':>6} | {'obj_only':>11} | {'byte_only':>11} | {'equal_unit':>11} | {'latency':>11}")
print('-'*82)
for wn, alpha, cf in cells:
    cell_rows = [r for r in rows if r["workload"]==wn and r["alpha"]==alpha and r["cache_frac"]==cf]
    line = f"{wn:<10} {alpha:>4} {cf:>6} |"
    for w_name, w_fn in WEIGHTINGS.items():
        scored = [(w_fn(r["obj_miss_mean"], r["byte_miss_mean"]), r["policy"]) for r in cell_rows]
        scored.sort()
        winner = scored[0][1]
        per_weighting_winners[w_name][winner] += 1
        if wn == "court":
            per_weighting_winners_court[w_name][winner] += 1
        line += f" {winner:>10}  |"
    print(line)

print(f"\n{'='*82}")
print(f"Aggregate winner counts ({len(cells)} cells total)")
print(f"{'='*82}")
print(f"{'weighting':<14} | {'winners':<60}")
for w_name in WEIGHTINGS:
    counts = per_weighting_winners[w_name].most_common()
    s = ", ".join(f"{p}: {n}" for p, n in counts)
    print(f"  {w_name:<12} | {s}")

print(f"\n=== Court only (16 cells) ===")
for w_name in WEIGHTINGS:
    counts = per_weighting_winners_court[w_name].most_common()
    s = ", ".join(f"{p}: {n}" for p, n in counts)
    print(f"  {w_name:<12} | {s}")

# By alpha, equal-weighted: see which policy wins
print(f"\n=== Court, equal-weighted (latency_p50): winner by alpha+cf ===")
for cf in sorted(set(c[2] for c in cells if c[0] == "court")):
    line = f"  cf={cf}: "
    for alpha in sorted(set(c[1] for c in cells if c[0] == "court" and c[2] == cf)):
        cell_rows = [r for r in rows if r["workload"]=="court" and r["alpha"]==alpha and r["cache_frac"]==cf]
        scored = [(WEIGHTINGS["latency_p50"](r["obj_miss_mean"], r["byte_miss_mean"]), r["policy"], r["obj_miss_mean"], r["byte_miss_mean"]) for r in cell_rows]
        scored.sort()
        line += f"α={alpha}→{scored[0][1]}({scored[0][2]:.3f}/{scored[0][3]:.3f}) "
    print(line)
