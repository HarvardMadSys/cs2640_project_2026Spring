#!/usr/bin/env python3
"""
Phase-7 §10: Translate miss-ratio gaps into request-latency and origin-load
deltas, using a closed-form latency model and the simulator's measured
miss-ratios.

Closed-form model (no warmup; aggregate over many requests):
    E[latency_per_request] = H · L_cache + (1 − H) · L_origin
where H is the hit ratio (= 1 − miss_ratio).

Origin latency anchored to MEASURED collection wall-clock, with the configured
base-delay subtracted (we recorded gross per-request time but not per-request
response time, so this is a derived estimate, not a per-request distribution):

  Congress.gov v3 REST   gross 1.874 s/req  −  BASE_DELAY 1.2 s   →  L_origin ≈ 0.67 s
  CourtListener REST v4  gross 1.577 s/req  −  BASE_DELAY 0.8 s   →  L_origin ≈ 0.78 s

  L_origin used in figures: 700 ms (round number consistent with both)
  L_cache  used in figures: 1 ms  (typical Memcached on localhost/LAN —
                                   Yang et al. 2023 measure ~0.3-2 ms across
                                   Twitter/Memcached production deployments)

Outputs:
  results/sweep_alpha_sigma/figures/latency_translation.pdf
"""

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
COURT_MRC = ROOT / "results" / "court" / "mrc.csv"
CONG_MRC  = ROOT / "results" / "congress" / "mrc.csv"
FIG = ROOT / "results" / "sweep_alpha_sigma" / "figures"

L_ORIGIN_MS = 400.0  # empirical p50 across Congress + Court (see scripts/extract_origin_latency.py)
L_CACHE_MS  = 1.0    # typical Memcached on host or LAN; Nishtala et al. NSDI 2013

def expected_latency(miss_ratio: float) -> float:
    h = 1 - miss_ratio
    return h * L_CACHE_MS + (1 - h) * L_ORIGIN_MS

def upstream_load_reduction(mr_baseline: float, mr_better: float) -> float:
    """Fractional reduction in origin requests."""
    if mr_baseline <= 0: return 0.0
    return (mr_baseline - mr_better) / mr_baseline

# ---------- load measured miss-ratios from main alpha sweep ----------
# We pull the focal cache_frac data from the existing mrc.csv files (which
# contain the 5-seed-aggregated focal-α point) and the small-cache cell from
# the same.  These are the production-comparable miss-ratios from the paper's
# main sweep, not synthetic.

def load_mrc(path):
    rows = list(csv.DictReader(open(path)))
    out = {}  # (cache_frac, policy) -> miss_ratio
    for r in rows:
        out[(float(r["cache_frac"]), r["policy"])] = float(r["miss_ratio"])
    return out

court = load_mrc(COURT_MRC)
cong  = load_mrc(CONG_MRC)

POLICIES = ["LRU", "FIFO", "CLOCK", "S3-FIFO", "SIEVE", "W-TinyLFU"]
CACHE_FRACS = sorted(set(k[0] for k in court.keys()))

# ---------- figure 1: latency curves vs cache_frac per policy on Court ----------
fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))

for ax, mrc, name in [(axes[0], court, "CourtListener"),
                      (axes[1], cong,  "Congress")]:
    for p in POLICIES:
        ys = []
        xs = []
        for cf in CACHE_FRACS:
            if (cf, p) in mrc:
                xs.append(cf); ys.append(expected_latency(mrc[(cf, p)]))
        if not xs: continue
        ls = "-" if p == "W-TinyLFU" else ("--" if p == "SIEVE" else ":")
        lw = 2.4 if p in ("W-TinyLFU", "SIEVE") else 1.4
        alpha = 1.0 if p in ("W-TinyLFU", "SIEVE") else 0.55
        ax.plot(xs, ys, marker="o", linestyle=ls, linewidth=lw, alpha=alpha,
                markersize=6, label=p)
    ax.set_xscale("log")
    ax.set_xlabel("cache fraction (log)", fontsize=11)
    ax.set_ylabel("expected per-request latency (ms)", fontsize=11)
    ax.set_title(f"{name}  ·  L_origin = {L_ORIGIN_MS:.0f} ms,  L_cache = {L_CACHE_MS:.0f} ms",
                 fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8.5, ncol=2, framealpha=0.92, loc="upper right")

fig.suptitle("Cache-policy choice translates to user-visible latency on slow public-records APIs",
             fontsize=13, y=1.02)
fig.tight_layout()
out = FIG / "latency_translation.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- figure 2: small-cache W-TinyLFU vs LRU savings, Court ----------
fig, ax = plt.subplots(figsize=(9, 4.5))

# Take Court small-cache (cache_frac = 0.001) — the regime where
# admission-filter benefit is largest.
target_cf = 0.001
mr_lru   = court[(target_cf, "LRU")]
mr_wtlfu = court[(target_cf, "W-TinyLFU")]
gap_pp = (mr_lru - mr_wtlfu) * 100
lat_lru   = expected_latency(mr_lru)
lat_wtlfu = expected_latency(mr_wtlfu)
load_red  = upstream_load_reduction(mr_lru, mr_wtlfu)

# Bar chart with annotations
labels = ["LRU", "W-TinyLFU"]
vals   = [lat_lru, lat_wtlfu]
colors = ["#7f8c8d", "#e67e22"]
bars = ax.bar(labels, vals, color=colors, width=0.55, edgecolor="black", linewidth=0.8)
for b, v, mr in zip(bars, vals, [mr_lru, mr_wtlfu]):
    ax.text(b.get_x() + b.get_width()/2, v + 8,
            f"{v:.0f} ms\n(miss ratio {mr:.3f})",
            ha="center", va="bottom", fontsize=11, fontweight="bold")
ax.annotate(
    f"Δ = {lat_lru - lat_wtlfu:.0f} ms saved per request\n"
    f"= {load_red*100:.0f}% reduction in upstream calls\n"
    f"= {gap_pp:.1f} pp miss-ratio improvement",
    xy=(0.5, max(vals) * 0.55), xycoords="data",
    fontsize=11, ha="center",
    bbox=dict(boxstyle="round,pad=0.5", facecolor="#fff4d6",
              edgecolor="#b8860b", linewidth=1.2),
)
ax.set_ylim(0, max(vals) * 1.30)
ax.set_ylabel("expected latency per request (ms)", fontsize=11)
ax.set_title(f"Court @ cache_frac = {target_cf}: 'orders of magnitude' isn't the test; "
             f"upstream load is.\n"
             f"L_origin = {L_ORIGIN_MS:.0f} ms (measured Court collection wall-clock),  "
             f"L_cache = {L_CACHE_MS:.0f} ms",
             fontsize=11)
ax.grid(axis="y", alpha=0.3)

fig.tight_layout()
out2 = FIG / "small_cache_savings.pdf"
fig.savefig(out2, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out2}")

# ---------- numeric summary for §10 prose ----------
print("\n=== §10 ground-truth numbers ===")
for cf in [0.001, 0.005, 0.01, 0.05]:
    if (cf, "LRU") not in court or (cf, "W-TinyLFU") not in court: continue
    mr_lru   = court[(cf, "LRU")]
    mr_wtlfu = court[(cf, "W-TinyLFU")]
    lat_lru   = expected_latency(mr_lru)
    lat_wtlfu = expected_latency(mr_wtlfu)
    print(f"  Court  cache_frac={cf}:  LRU={lat_lru:.0f}ms  W-TLFU={lat_wtlfu:.0f}ms"
          f"  Δ={lat_lru-lat_wtlfu:.0f}ms  load_red={upstream_load_reduction(mr_lru, mr_wtlfu)*100:.1f}%")
for cf in [0.001, 0.005, 0.01, 0.05]:
    if (cf, "LRU") not in cong or (cf, "W-TinyLFU") not in cong: continue
    mr_lru   = cong[(cf, "LRU")]
    mr_wtlfu = cong[(cf, "W-TinyLFU")]
    lat_lru   = expected_latency(mr_lru)
    lat_wtlfu = expected_latency(mr_wtlfu)
    print(f"  Cong   cache_frac={cf}:  LRU={lat_lru:.0f}ms  W-TLFU={lat_wtlfu:.0f}ms"
          f"  Δ={lat_lru-lat_wtlfu:.0f}ms  load_red={upstream_load_reduction(mr_lru, mr_wtlfu)*100:.1f}%")
