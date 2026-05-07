#!/usr/bin/env python3
"""
Extract per-request origin response time from existing trace timestamps.

Both collectors record `timestamp` at the start of each request. The gap
between consecutive timestamps is:

    gap[i] = timestamp[i+1] - timestamp[i]
           = response_time[i] + sleep_after[i] + minor_overhead

Subtracting the configured pacing recovers a per-request response-time
estimate. This is noisy (the sleep call is not exact, retry/backoff bursts
appear, the first request after a backoff has gap >> response_time) but
the median is a defensible point estimate of L_origin without re-collecting.

Outputs JSON summary + a histogram figure.
"""

import csv
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

# (path, configured_sleep_min_ms, configured_sleep_max_ms)
TRACES = [
    ("Congress", ROOT / "traces" / "congress_trace.csv", 1200, 1200),
    ("Court",    ROOT / "traces" / "court_trace.csv",    800,  1200),  # 800 base + 0..400 jitter
]

OUT = ROOT / "results" / "sweep_alpha_sigma" / "figures" / "origin_latency.pdf"
SUMMARY = ROOT / "results" / "sweep_alpha_sigma" / "origin_latency_summary.json"

def load_timestamps(path):
    ts = []
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            ts.append(int(row["timestamp"]))
    return ts

def per_request_response_ms(ts, sleep_min_ms, sleep_max_ms):
    """For each consecutive pair, compute gap minus mean configured sleep.
    Returns list of estimated response times in ms, with outliers > 30s
    (retry-backoff bursts) filtered out."""
    sleep_mean = (sleep_min_ms + sleep_max_ms) / 2.0
    out = []
    for i in range(len(ts) - 1):
        gap = ts[i+1] - ts[i]
        rt  = gap - sleep_mean
        if 0 < rt < 30000:  # filter retry-backoff bursts
            out.append(rt)
    return out

summary = {}
fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))

for ax, (name, path, smin, smax) in zip(axes, TRACES):
    ts = load_timestamps(path)
    rt = per_request_response_ms(ts, smin, smax)
    if not rt:
        print(f"{name}: no usable timestamps"); continue

    rt_arr = np.array(rt)
    p50 = float(np.percentile(rt_arr, 50))
    p90 = float(np.percentile(rt_arr, 90))
    p99 = float(np.percentile(rt_arr, 99))
    mean = float(np.mean(rt_arr))
    iqr = float(np.percentile(rt_arr, 75) - np.percentile(rt_arr, 25))

    summary[name] = {
        "n_samples":     len(rt),
        "p50_ms":        round(p50, 1),
        "p90_ms":        round(p90, 1),
        "p99_ms":        round(p99, 1),
        "mean_ms":       round(mean, 1),
        "iqr_ms":        round(iqr, 1),
        "configured_sleep_mean_ms": (smin + smax) / 2,
    }

    ax.hist(rt_arr, bins=80, range=(0, min(3000, p99 * 1.2)),
            color="#4dabf7" if name == "Congress" else "#e67e22",
            alpha=0.85, edgecolor="black", linewidth=0.4)
    ax.axvline(p50, color="black", linestyle="--", linewidth=1.2)
    ax.text(p50 + 30, ax.get_ylim()[1] * 0.92,
            f"p50 = {p50:.0f} ms", fontsize=10, fontweight="bold")
    ax.set_title(f"{name}  ·  {len(rt)} samples", fontsize=12)
    ax.set_xlabel("estimated origin response time (ms)", fontsize=10)
    ax.set_ylabel("requests", fontsize=10)
    ax.grid(alpha=0.3)

fig.suptitle("Per-request origin response time, extracted from trace timestamps "
             "(gap between consecutive requests minus configured pacing)",
             fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"Wrote {OUT}")

with open(SUMMARY, "w") as f:
    json.dump(summary, f, indent=2)
print(f"Wrote {SUMMARY}")

print()
print("=== Empirical L_origin per workload ===")
for k, v in summary.items():
    print(f"  {k:10s}  p50 = {v['p50_ms']:6.0f} ms   "
          f"p90 = {v['p90_ms']:6.0f} ms   "
          f"mean = {v['mean_ms']:6.0f} ms   "
          f"n = {v['n_samples']}")
