#!/usr/bin/env python3
"""
Re-extract L_origin with proper uncertainty quantification.

The v2 estimate (extract_origin_latency.py) reports a single p50 = 350-400ms.
Real production deployments care about a *distribution*, and the extraction
procedure itself has biases:

  - Configured sleep is jitter-augmented (Court: 800-1200ms).
    Subtracting the *mean* sleep treats all gaps as if sleep were the mean,
    overestimating response time when sleep was longer-than-mean and
    underestimating when shorter. The asymmetry depends on which jitter
    realizations bias the gap distribution.
  - First request after a backoff burst has gap >> response_time; we
    filter at 30s. Some near-burst gaps slip through.
  - OS scheduling noise on the collector adds ~10-20ms of variance.

Output:
  - p25 / p50 / p75 / p90 of extracted response-time distribution
  - p50 ± half-IQR confidence band
  - Note that p25 is the most-defensible "no-network-jitter" estimate
    because gaps shorter than (sleep + p25_response) are likely the
    cleanest extractions.
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

OUT = ROOT / "results" / "sweep_alpha_sigma" / "origin_latency_band.json"

def load_timestamps(path):
    return [int(r["timestamp"]) for r in csv.DictReader(open(path))]

def per_request_response_ms(ts, sleep_min_ms, sleep_max_ms):
    """Subtract MEAN sleep (legacy) and MIN sleep (lower-bound estimate).
    The distribution of gaps - mean_sleep is the legacy estimate;
    gaps - max_sleep is a lower-bound (what response time would be if sleep
    were maximal). Real response time is between these for any single
    request."""
    sleep_mean = (sleep_min_ms + sleep_max_ms) / 2.0
    sleep_max = sleep_max_ms
    sleep_min = sleep_min_ms
    legacy = []   # gap - mean(sleep)  — what v2 used
    lower  = []   # gap - max(sleep)   — lower bound on response_time
    upper  = []   # gap - min(sleep)   — upper bound on response_time
    for i in range(len(ts) - 1):
        gap = ts[i+1] - ts[i]
        rt_legacy = gap - sleep_mean
        rt_lower  = gap - sleep_max
        rt_upper  = gap - sleep_min
        if 0 < rt_legacy < 30000:  # filter retry-backoff bursts
            legacy.append(rt_legacy)
        if 0 < rt_lower < 30000:
            lower.append(rt_lower)
        if 0 < rt_upper < 30000:
            upper.append(rt_upper)
    return legacy, lower, upper

summary = {}
for name, path, smin, smax in TRACES:
    ts = load_timestamps(path)
    legacy, lower, upper = per_request_response_ms(ts, smin, smax)

    summary[name] = {
        "n_samples": len(legacy),
        "configured_sleep_min_ms": smin,
        "configured_sleep_max_ms": smax,
        "legacy_estimate_p50": float(np.percentile(legacy, 50)),
        "legacy_estimate_p25": float(np.percentile(legacy, 25)),
        "legacy_estimate_p75": float(np.percentile(legacy, 75)),
        "legacy_estimate_p90": float(np.percentile(legacy, 90)),
        "lower_bound_p50":  float(np.percentile(lower, 50)) if lower else None,
        "upper_bound_p50":  float(np.percentile(upper, 50)) if upper else None,
    }

# Combined summary across both APIs (the headline for the paper)
all_legacy = []
all_lower = []
all_upper = []
for name, path, smin, smax in TRACES:
    ts = load_timestamps(path)
    l, lo, up = per_request_response_ms(ts, smin, smax)
    all_legacy.extend(l); all_lower.extend(lo); all_upper.extend(up)

p25 = float(np.percentile(all_legacy, 25))
p50 = float(np.percentile(all_legacy, 50))
p75 = float(np.percentile(all_legacy, 75))
p90 = float(np.percentile(all_legacy, 90))
lo_p50 = float(np.percentile(all_lower, 50))
up_p50 = float(np.percentile(all_upper, 50))

summary["combined"] = {
    "n_samples": len(all_legacy),
    "p25_ms": round(p25, 1),
    "p50_ms": round(p50, 1),
    "p75_ms": round(p75, 1),
    "p90_ms": round(p90, 1),
    "lower_bound_p50_ms": round(lo_p50, 1),
    "upper_bound_p50_ms": round(up_p50, 1),
    "note": "lower_bound assumes sleep was always maximum; upper_bound assumes sleep was always minimum. Real per-request response time is between these.",
}

with open(OUT, "w") as f:
    json.dump(summary, f, indent=2)
print(f"Wrote {OUT}")

print(f"\n=== Combined L_origin band ===")
print(f"  p25 = {p25:.0f} ms")
print(f"  p50 = {p50:.0f} ms (legacy point estimate, used in v2)")
print(f"  p75 = {p75:.0f} ms")
print(f"  p90 = {p90:.0f} ms (heavy-tail anchor)")
print(f"  Sleep-jitter envelope on p50: [{lo_p50:.0f}, {up_p50:.0f}] ms")
print(f"\nFor latency translation, use:")
print(f"  - L_origin ≈ {p50:.0f} ms (median) for typical-request expected latency")
print(f"  - L_origin ≈ {p90:.0f} ms (p90) for tail-latency claims")
print(f"  - Uncertainty band: ±{(up_p50-lo_p50)/2:.0f} ms from sleep-jitter alone")
