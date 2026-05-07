#!/usr/bin/env python3
"""
v3 headline figure: GDSF (size-aware) vs W-TinyLFU (frequency-aware) vs
SIEVE across the operating-point grid (alpha, cache_fraction) on both
real workloads. This figure replaces the v2 (alpha, sigma) decision map
as the headline because it shows the actual policy ranking that should
inform deployment, including the size-aware baseline that was missing.

Reads results/gdsf/summary.csv (created by gdsf_focal.py).
"""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "results" / "gdsf" / "summary.csv"
OUT = ROOT / "results" / "gdsf" / "figures" / "gdsf_headline.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

rows = list(csv.DictReader(open(SUMMARY)))
for r in rows:
    r["alpha"] = float(r["alpha"])
    r["cache_frac"] = float(r["cache_frac"])
    for k in ["GDSF_mean", "GDSF_std", "SIEVE_mean", "SIEVE_std",
             "WTLFU_mean", "WTLFU_std", "GDSF_minus_WTLFU_pp"]:
        r[k] = float(r[k])

ALPHAS = sorted(set(r["alpha"] for r in rows))
CFS    = sorted(set(r["cache_frac"] for r in rows))

fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))

for ax, wname, title in [(axes[0], "congress", "Congress (low α, light tail)"),
                          (axes[1], "court", "Court (high α, heavy tail)")]:
    sub = [r for r in rows if r["workload"] == wname]
    for policy_label, color, marker in [("SIEVE", "#7f7f7f", "o"),
                                        ("WTLFU", "#1f77b4", "s"),
                                        ("GDSF",  "#e67e22", "D")]:
        # one line per cache_frac, x = alpha, y = miss-ratio for this policy
        for cf in CFS:
            ys = [next(r[f"{policy_label}_mean"] for r in sub
                       if r["alpha"]==a and r["cache_frac"]==cf) for a in ALPHAS]
            ax.plot(ALPHAS, ys, marker=marker, linestyle="-", color=color,
                    alpha=0.45 + 0.55 * CFS.index(cf)/len(CFS),
                    linewidth=1.6, markersize=6,
                    label=f"{policy_label.replace('WTLFU','W-TinyLFU')} (cf={cf})" if cf == CFS[2] else None)
    # cleanup: keep only one legend entry per policy
    handles, labels = ax.get_legend_handles_labels()
    by_label = {}
    for h, l in zip(handles, labels):
        if l and l not in by_label:
            by_label[l] = h
    ax.legend(by_label.values(), by_label.keys(), fontsize=9, ncol=1, loc="lower left")
    ax.set_xlabel("Zipf α (replay overlay)", fontsize=11)
    ax.set_ylabel("miss ratio (5-seed mean)", fontsize=11)
    ax.set_title(title, fontsize=11)
    ax.grid(alpha=0.3)

fig.suptitle("Three policies, two workloads: GDSF (size-aware) dominates broadly; "
             "W-TinyLFU narrowly wins only at small cache + heavy tail + high α",
             fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT}")

# ------- second figure: per-cache-frac bar chart on Court ----------
fig, axes = plt.subplots(1, len(CFS), figsize=(15, 4.2), sharey=False)

for ax, cf in zip(axes, CFS):
    court = [r for r in rows if r["workload"] == "court" and r["cache_frac"] == cf]
    width = 0.25
    x = np.arange(len(ALPHAS))
    sieve_y = [r["SIEVE_mean"] for r in sorted(court, key=lambda r: r["alpha"])]
    wtlfu_y = [r["WTLFU_mean"] for r in sorted(court, key=lambda r: r["alpha"])]
    gdsf_y  = [r["GDSF_mean"]  for r in sorted(court, key=lambda r: r["alpha"])]
    ax.bar(x - width, sieve_y, width, label="SIEVE", color="#7f7f7f", edgecolor="black", linewidth=0.4)
    ax.bar(x,         wtlfu_y, width, label="W-TinyLFU", color="#1f77b4", edgecolor="black", linewidth=0.4)
    ax.bar(x + width, gdsf_y,  width, label="GDSF", color="#e67e22", edgecolor="black", linewidth=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels([f"α={a}" for a in ALPHAS], fontsize=9)
    ax.set_ylabel("miss ratio", fontsize=10)
    ax.set_title(f"Court, cache_frac={cf}", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    if cf == CFS[0]: ax.legend(fontsize=9, loc="upper right")

fig.suptitle("Court at four cache fractions. GDSF wins at cf≥0.01 universally; "
             "W-TinyLFU wins at cf≤0.005 (small cache).", fontsize=11, y=1.02)
fig.tight_layout()
out2 = OUT.parent / "gdsf_court_bars.pdf"
fig.savefig(out2, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out2}")

# ------- third figure: same but Congress ----------
fig, axes = plt.subplots(1, len(CFS), figsize=(15, 4.2), sharey=False)

for ax, cf in zip(axes, CFS):
    cong = [r for r in rows if r["workload"] == "congress" and r["cache_frac"] == cf]
    width = 0.25
    x = np.arange(len(ALPHAS))
    sieve_y = [r["SIEVE_mean"] for r in sorted(cong, key=lambda r: r["alpha"])]
    wtlfu_y = [r["WTLFU_mean"] for r in sorted(cong, key=lambda r: r["alpha"])]
    gdsf_y  = [r["GDSF_mean"]  for r in sorted(cong, key=lambda r: r["alpha"])]
    ax.bar(x - width, sieve_y, width, label="SIEVE", color="#7f7f7f", edgecolor="black", linewidth=0.4)
    ax.bar(x,         wtlfu_y, width, label="W-TinyLFU", color="#1f77b4", edgecolor="black", linewidth=0.4)
    ax.bar(x + width, gdsf_y,  width, label="GDSF", color="#e67e22", edgecolor="black", linewidth=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels([f"α={a}" for a in ALPHAS], fontsize=9)
    ax.set_ylabel("miss ratio", fontsize=10)
    ax.set_title(f"Congress, cache_frac={cf}", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    if cf == CFS[0]: ax.legend(fontsize=9, loc="upper right")

fig.suptitle("Congress at four cache fractions. GDSF wins at cf≥0.005 universally; "
             "no clear winner at the smallest cache.", fontsize=11, y=1.02)
fig.tight_layout()
out3 = OUT.parent / "gdsf_congress_bars.pdf"
fig.savefig(out3, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out3}")
