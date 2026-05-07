#!/usr/bin/env python3
"""
Phase-7 §3 expository figures: what α actually means, what the
Congress / Court traces look like at human readable scale.

Outputs:
  results/sweep_alpha_sigma/figures/zipf_explainer.pdf
  results/sweep_alpha_sigma/figures/top10_keys.pdf
  results/sweep_alpha_sigma/figures/trace_excerpt.pdf
"""

import csv
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "traces"
FIG = ROOT / "results" / "sweep_alpha_sigma" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

# ---------- Figure A: Zipf explainer ----------
fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

ranks = np.arange(1, 101)
for ax in axes:
    for alpha, color, label in [(0.23, "#1f77b4", "α = 0.23  (Congress, near-uniform)"),
                                 (0.6,  "#9467bd", "α = 0.6"),
                                 (1.0,  "#2ca02c", "α = 1.0"),
                                 (1.03, "#d62728", "α = 1.03  (Court, real skew)"),
                                 (1.4,  "#8c564b", "α = 1.4  (very skewed)")]:
        weights = 1.0 / (ranks ** alpha)
        weights /= weights.sum()
        lw = 2.4 if alpha in (0.23, 1.03) else 1.4
        ax.plot(ranks, weights, color=color, linewidth=lw, label=label)

axes[0].set_yscale("linear")
axes[0].set_title("linear scale", fontsize=11)
axes[0].set_xlabel("object rank (1 = most popular)", fontsize=10)
axes[0].set_ylabel("access probability", fontsize=10)
axes[0].set_xlim(0, 30)
axes[0].grid(alpha=0.3)
axes[0].legend(fontsize=9, framealpha=0.9)

axes[1].set_yscale("log")
axes[1].set_xscale("log")
axes[1].set_title("log–log (a Zipf line is a straight line of slope −α)", fontsize=11)
axes[1].set_xlabel("object rank (log)", fontsize=10)
axes[1].set_ylabel("access probability (log)", fontsize=10)
axes[1].grid(alpha=0.3, which="both")
axes[1].legend(fontsize=9, framealpha=0.9)

fig.suptitle(r"Zipf $\alpha$: how concentrated access is on the popular keys",
             fontsize=13, y=1.02)
fig.tight_layout()
out = FIG / "zipf_explainer.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- Figure B: top-10 key frequencies side by side ----------
def load_keys(p):
    return [r["key"] for r in csv.DictReader(open(p))]

cong_keys  = load_keys(TRACES / "congress_trace.csv")
court_keys = load_keys(TRACES / "court_trace.csv")
print(f"Congress: {len(cong_keys)} requests, {len(set(cong_keys))} unique")
print(f"Court:    {len(court_keys)} requests, {len(set(court_keys))} unique")

cong_top  = Counter(cong_keys ).most_common(10)
court_top = Counter(court_keys).most_common(10)

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
for ax, top, title, color in [(axes[0], cong_top,  "Congress.gov  ·  α_raw = 0.23", "#1f77b4"),
                              (axes[1], court_top, "CourtListener  ·  α_raw = 1.03", "#d62728")]:
    keys, counts = zip(*top)
    short = []
    for k in keys:
        s = k.replace("amendment/", "amdt/").replace("dockets/", "dkt/").replace("opinions/", "op/")
        s = s.replace("clusters/", "cl/").replace("courts/", "ct/").replace("roll-call-vote/", "rcv/")
        if len(s) > 24: s = s[:21] + "..."
        short.append(s)
    y = np.arange(len(short))
    ax.barh(y, counts, color=color, alpha=0.85, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(short, fontsize=9, family="monospace")
    ax.invert_yaxis()
    for yi, c in enumerate(counts):
        ax.text(c + max(counts)*0.01, yi, f"  {c}× ", va="center", fontsize=9)
    ax.set_xlabel("times this key was requested in the trace", fontsize=10)
    ax.set_title(title, fontsize=12)
    ax.grid(axis="x", alpha=0.3)
    ax.set_xlim(0, max(counts) * 1.18)

fig.suptitle("Top-10 most-requested keys: side-by-side, real captured traces",
             fontsize=13, y=1.01)
fig.tight_layout()
out = FIG / "top10_keys.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- Figure C: 200-line trace excerpt - access timeline ----------
fig, axes = plt.subplots(2, 1, figsize=(13, 5.6))

# Build object-rank index per trace (most-popular = rank 1, etc)
def rank_map(keys):
    ctr = Counter(keys)
    ranked = [k for k, _ in ctr.most_common()]
    return {k: i + 1 for i, k in enumerate(ranked)}

cong_rank  = rank_map(cong_keys)
court_rank = rank_map(court_keys)

WINDOW = 500  # show first 500 requests

cong_y  = [cong_rank[k]  for k in cong_keys[:WINDOW]]
court_y = [court_rank[k] for k in court_keys[:WINDOW]]

axes[0].scatter(range(len(cong_y)),  cong_y,  s=14, c="#1f77b4", alpha=0.7,
                edgecolors="none")
axes[0].set_yscale("log")
axes[0].set_ylim(0.7, max(cong_rank.values()) * 1.5)
axes[0].invert_yaxis()
axes[0].set_title("Congress.gov · first 500 requests (y-axis: object popularity rank, log)",
                  fontsize=11)
axes[0].set_ylabel("rank (1 = hottest)", fontsize=10)
axes[0].grid(alpha=0.3, axis="y", which="both")
axes[0].text(0.985, 0.95, f"{len(set(cong_keys[:WINDOW]))} of 500 keys are unique",
             transform=axes[0].transAxes, ha="right", va="top",
             fontsize=10, bbox=dict(facecolor="white", alpha=0.85, edgecolor="#888"))

axes[1].scatter(range(len(court_y)), court_y, s=14, c="#d62728", alpha=0.7,
                edgecolors="none")
axes[1].set_yscale("log")
axes[1].set_ylim(0.7, max(court_rank.values()) * 1.5)
axes[1].invert_yaxis()
axes[1].set_title("CourtListener · first 500 requests (lower = more popular; clusters at top = recurring hot keys)",
                  fontsize=11)
axes[1].set_ylabel("rank (1 = hottest)", fontsize=10)
axes[1].set_xlabel("request index (chronological)", fontsize=10)
axes[1].grid(alpha=0.3, axis="y", which="both")
axes[1].text(0.985, 0.95, f"{len(set(court_keys[:WINDOW]))} of 500 keys are unique",
             transform=axes[1].transAxes, ha="right", va="top",
             fontsize=10, bbox=dict(facecolor="white", alpha=0.85, edgecolor="#888"))

fig.tight_layout()
out = FIG / "trace_excerpt.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")
