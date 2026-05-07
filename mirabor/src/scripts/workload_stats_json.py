#!/usr/bin/env python3
"""
Emit workload_stats.json from a trace CSV, mirroring src/workload_stats.cpp::characterize()
plus a p95 size stat (D-13 literal spec: mean/median/p95/max). Implemented in Python so
Phase 3 can produce workload_stats.json for the court trace without modifying main.cpp
(CONTEXT.md rules main.cpp out of scope for Phase 3).

Usage:
    python3 scripts/workload_stats_json.py --trace traces/court_trace.csv \\
        --output results/court/workload_stats.json

Output JSON keys (all load-bearing — Phase 5 compare_workloads.py depends on them):
    trace_path, total_requests, unique_objects, alpha_mle, ohw_ratio,
    mean_size, median_size, p95_size, max_size, working_set_bytes
"""
import argparse
import csv
import json
import math
import os
from collections import Counter


def estimate_zipf_alpha_mle(freq_counts, max_rank=2000):
    """Clauset et al. (2009) MLE for discrete power-law.
    Port of src/workload_stats.cpp::estimate_zipf_alpha (Newton's method on dL/dalpha).
    """
    freqs = sorted(freq_counts.values(), reverse=True)[:max_rank]
    n = len(freqs)
    if n < 2:
        return 0.0
    N = sum(freqs)
    # sum_i (f_i * ln(i+1)) for i=1..n (skip i=0 because ln(1)=0)
    sum_fi_lni = sum(f * math.log(i + 1) for i, f in enumerate(freqs) if i > 0)

    def H(alpha):
        return sum(k ** -alpha for k in range(1, n + 1))

    def Hp(alpha):
        # d/dalpha H(n, alpha) = -sum_{k=1}^{n} k^{-alpha} * ln(k)
        return -sum((k ** -alpha) * math.log(k) for k in range(2, n + 1))

    def Hpp(alpha):
        # d^2/dalpha^2 H(n, alpha) = sum_{k=1}^{n} k^{-alpha} * (ln k)^2
        return sum((k ** -alpha) * (math.log(k) ** 2) for k in range(2, n + 1))

    alpha = 1.0
    for _ in range(50):
        H_val = H(alpha)
        Hp_val = Hp(alpha)
        Hpp_val = Hpp(alpha)
        # dL/dalpha = -sum_fi_lni - N * Hp / H
        grad = -sum_fi_lni - N * Hp_val / H_val
        # d2L/dalpha2 = -N * (Hpp * H - Hp^2) / H^2
        hess = -N * (Hpp_val * H_val - Hp_val * Hp_val) / (H_val * H_val)
        if abs(hess) < 1e-15:
            break
        step = grad / hess
        alpha = max(0.01, min(5.0, alpha - step))
        if abs(step) < 1e-8:
            break
    return alpha


def one_hit_wonder_ratio(keys, window_frac=0.1):
    """Fraction of unique keys appearing exactly once in the trailing window.
    Mirrors src/workload_stats.cpp::one_hit_wonder_ratio default window_frac=0.1."""
    window = max(1, int(len(keys) * window_frac))
    start = max(0, len(keys) - window)
    freq = Counter(keys[start:])
    one_hits = sum(1 for v in freq.values() if v == 1)
    return (one_hits / len(freq)) if freq else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--trace", required=True, help="Input trace CSV (timestamp,key,size)")
    ap.add_argument("--output", required=True, help="Output JSON path")
    args = ap.parse_args()

    keys = []
    sizes = []
    first_seen_size = {}  # for working_set_bytes (first observation per key)

    with open(args.trace) as f:
        r = csv.DictReader(f)
        required = {"timestamp", "key", "size"}
        if not required.issubset(r.fieldnames or []):
            raise SystemExit(
                f"FATAL: expected {required} columns, got {r.fieldnames}"
            )
        for row in r:
            k = row["key"]
            s = int(row["size"])
            keys.append(k)
            sizes.append(s)
            if k not in first_seen_size:
                first_seen_size[k] = s

    if not sizes:
        raise SystemExit("FATAL: empty trace")

    freq = Counter(keys)
    sizes_sorted = sorted(sizes)
    n = len(sizes_sorted)

    stats = {
        "trace_path": args.trace,
        "total_requests": len(keys),
        "unique_objects": len(freq),
        "alpha_mle": estimate_zipf_alpha_mle(freq),
        "ohw_ratio": one_hit_wonder_ratio(keys, window_frac=0.1),
        "mean_size": sum(sizes) / n,
        "median_size": sizes_sorted[n // 2],
        "p95_size": sizes_sorted[int(n * 0.95)],
        "max_size": sizes_sorted[-1],
        "working_set_bytes": sum(first_seen_size.values()),
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Wrote {args.output}: {stats}")


if __name__ == "__main__":
    main()
