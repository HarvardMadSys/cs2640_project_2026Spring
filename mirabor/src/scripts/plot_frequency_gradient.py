#!/usr/bin/env python3
"""
Direct evidence for the §7 mechanism claim: the CMS gradient that
W-TinyLFU's admission filter relies on exists on Court but not on
Congress. We don't need to instrument the simulator; we can plot the
ground-truth access counts that the CMS approximates.

Outputs:
  results/sweep_alpha_sigma/figures/frequency_gradient.pdf
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

CONG = ROOT / "traces" / "congress_trace.csv"
COURT = ROOT / "traces" / "court_trace.csv"
OUT = ROOT / "results" / "sweep_alpha_sigma" / "figures" / "frequency_gradient.pdf"

def counts(path):
    return Counter(r["key"] for r in csv.DictReader(open(path)))

cong = counts(CONG)
court = counts(COURT)

# What W-TinyLFU's admission filter compares: the count of an incoming key
# vs. the counts of keys currently in cache. For the comparison to *do
# anything*, there must be a stratified distribution of counts across the
# keys. We plot the count-of-counts histogram for each workload, capped at
# the top of the distribution.

fig, axes = plt.subplots(1, 2, figsize=(13, 4.7))

for ax, ctr, name, color in [(axes[0], cong, "Congress", "#1f77b4"),
                              (axes[1], court, "Court",   "#e67e22")]:
    vals = list(ctr.values())
    n_total_keys = len(vals)
    max_v = max(vals)
    # truncate at min(50, max_v) for readability
    upper = min(50, max_v)
    bins = list(range(1, upper + 2))

    ax.hist(vals, bins=bins, color=color, alpha=0.85, edgecolor="black",
            linewidth=0.4, log=True)
    # annotate
    n_one = sum(1 for v in vals if v == 1)
    n_two = sum(1 for v in vals if v == 2)
    n_ge_5 = sum(1 for v in vals if v >= 5)
    n_ge_10 = sum(1 for v in vals if v >= 10)
    n_ge_30 = sum(1 for v in vals if v >= 30)

    txt = (f"max access count: {max_v}\n"
           f"keys accessed once: {n_one:,} ({100*n_one/n_total_keys:.1f}%)\n"
           f"keys accessed ≥5×:  {n_ge_5:,} ({100*n_ge_5/n_total_keys:.2f}%)\n"
           f"keys accessed ≥10×: {n_ge_10:,} ({100*n_ge_10/n_total_keys:.2f}%)\n"
           f"keys accessed ≥30×: {n_ge_30:,} ({100*n_ge_30/n_total_keys:.2f}%)")
    ax.text(0.97, 0.97, txt, transform=ax.transAxes,
            fontsize=10, va="top", ha="right", family="monospace",
            bbox=dict(facecolor="white", alpha=0.92, edgecolor="#888"))

    ax.set_title(f"{name}  ·  {n_total_keys:,} unique keys", fontsize=12)
    ax.set_xlabel("times this key was requested", fontsize=11)
    ax.set_ylabel("number of keys (log)", fontsize=11)
    ax.set_xlim(0.5, upper + 1)
    ax.grid(axis="y", alpha=0.3, which="both")

fig.suptitle("Ground-truth per-key access counts — the gradient W-TinyLFU's CMS approximates.\n"
             "Congress: every key seen 1–3 times; no gradient to capture. "
             "Court: long tail to 170+ accesses; clear stratification.",
             fontsize=12, y=1.04)
fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"Wrote {OUT}")

# Numeric summary for the paper
print("\n=== Frequency-gradient ground truth ===")
for name, ctr in [("Congress", cong), ("Court", court)]:
    vals = list(ctr.values())
    print(f"  {name}: max={max(vals)}  keys≥10={sum(1 for v in vals if v>=10)}"
          f"  keys≥30={sum(1 for v in vals if v>=30)}"
          f"  keys≥100={sum(1 for v in vals if v>=100)}")
