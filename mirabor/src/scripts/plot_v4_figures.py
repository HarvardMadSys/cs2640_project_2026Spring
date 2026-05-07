#!/usr/bin/env python3
"""
v4 figures:
  1. obj-vs-byte miss tradeoff scatter (Court focal): each policy is one
     point in (object-miss, byte-miss) space. Pareto frontier shows that
     no single policy dominates both metrics.
  2. (alpha, sigma) surface for GDSF vs W-TinyLFU object-miss (extends v3
     surface with the size-aware baseline).
  3. policy-by-metric ranking bar chart at Court cf=0.01.
"""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
V4_SUMMARY = ROOT / "results" / "v4" / "summary.csv"
AGG_V4 = ROOT / "results" / "sweep_alpha_sigma" / "agg_v4.csv"
OUT_DIR = ROOT / "results" / "v4" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)

rows = list(csv.DictReader(open(V4_SUMMARY)))
for r in rows:
    r["alpha"] = float(r["alpha"])
    r["cache_frac"] = float(r["cache_frac"])
    for k in ["obj_miss_mean", "obj_miss_std", "byte_miss_mean", "byte_miss_std"]:
        r[k] = float(r[k])

# ---------- Figure 1: obj-vs-byte miss tradeoff scatter on Court focal ----------
fig, axes = plt.subplots(1, 2, figsize=(13, 5.4))

POLICY_COLORS = {
    "LRU": "#7f7f7f", "FIFO": "#bcbd22", "CLOCK": "#9467bd",
    "S3-FIFO": "#2ca02c", "SIEVE": "#1f77b4",
    "W-TinyLFU": "#d62728", "GDSF": "#e67e22", "GDSF-Cost": "#ff7f0e",
}
POLICY_MARKERS = {
    "LRU": "o", "FIFO": "v", "CLOCK": "^", "S3-FIFO": "<",
    "SIEVE": "s", "W-TinyLFU": "D", "GDSF": "P", "GDSF-Cost": "X",
}

for ax, wname, title in [(axes[0], "court", "Court (heavy-tail, α≈1.03)"),
                          (axes[1], "congress", "Congress (light-tail, α≈0.23)")]:
    sub = [r for r in rows if r["workload"] == wname and r["cache_frac"] == 0.01]
    for policy in sorted(set(r["policy"] for r in sub)):
        prows = [r for r in sub if r["policy"] == policy]
        xs = [r["obj_miss_mean"] for r in prows]
        ys = [r["byte_miss_mean"] for r in prows]
        # connect points across α to show trajectory
        idx = sorted(range(len(prows)), key=lambda i: prows[i]["alpha"])
        xs = [xs[i] for i in idx]; ys = [ys[i] for i in idx]
        ax.plot(xs, ys, "-", color=POLICY_COLORS.get(policy, "black"), alpha=0.4, linewidth=1.2)
        ax.scatter(xs, ys, marker=POLICY_MARKERS.get(policy, "o"),
                   color=POLICY_COLORS.get(policy, "black"),
                   s=80, edgecolor="black", linewidth=0.6,
                   label=policy, zorder=3)
        # annotate each point with α
        for i, r in enumerate([prows[j] for j in idx]):
            ax.annotate(f"α={r['alpha']}", (xs[i], ys[i]),
                       textcoords="offset points", xytext=(4, 5),
                       fontsize=7, alpha=0.6)
    ax.set_xlabel("object-miss ratio (lower better)", fontsize=11)
    ax.set_ylabel("byte-miss ratio (lower better)", fontsize=11)
    ax.set_title(title + ", cache_frac=0.01", fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best", ncol=2, framealpha=0.92)

fig.suptitle("Object-miss vs byte-miss is a tradeoff: GDSF wins object-miss, "
             "W-TinyLFU wins byte-miss. No single policy dominates.", fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(OUT_DIR / "obj_vs_byte_tradeoff.pdf", bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT_DIR / 'obj_vs_byte_tradeoff.pdf'}")

# ---------- Figure 2: bar chart of all 8 policies on Court focal cf=0.01 ----------
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

for ax, metric, ylabel in [(axes[0], "obj_miss_mean", "object-miss ratio"),
                            (axes[1], "byte_miss_mean", "byte-miss ratio")]:
    court = [r for r in rows if r["workload"] == "court" and r["cache_frac"] == 0.01]
    POLS = ["LRU", "FIFO", "CLOCK", "S3-FIFO", "SIEVE", "W-TinyLFU", "GDSF", "GDSF-Cost"]
    ALPHAS = sorted(set(r["alpha"] for r in court))

    width = 0.10
    x = np.arange(len(ALPHAS))
    for i, p in enumerate(POLS):
        ys = []
        for a in ALPHAS:
            ys.append(next((r[metric] for r in court if r["policy"]==p and r["alpha"]==a), 0))
        ax.bar(x + (i - len(POLS)/2) * width, ys, width,
               label=p, color=POLICY_COLORS.get(p, "black"),
               edgecolor="black", linewidth=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels([f"α={a}" for a in ALPHAS], fontsize=10)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(f"Court @ cache_frac=0.01 — {ylabel}", fontsize=11)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=7.5, ncol=2, loc="upper right" if metric == "obj_miss_mean" else "lower right")

fig.suptitle("Eight policies on Court at cache_frac=0.01: "
             "object-miss ranking ≠ byte-miss ranking", fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(OUT_DIR / "court_obj_vs_byte_8policies.pdf", bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT_DIR / 'court_obj_vs_byte_8policies.pdf'}")

# ---------- Figure 3: (α, σ) GDSF vs W-TinyLFU surface ----------
agg = list(csv.DictReader(open(AGG_V4)))
for r in agg:
    r["alpha"] = float(r["alpha"]); r["sigma"] = float(r["sigma"])
    r["cache_frac"] = float(r["cache_frac"]); r["mean_miss"] = float(r["mean_miss"])

ALPHAS_S = sorted(set(r["alpha"] for r in agg))
SIGMAS_S = sorted(set(r["sigma"] for r in agg))

fig, axes = plt.subplots(2, 2, figsize=(14, 10))
for idx, cf in enumerate([0.001, 0.005, 0.01, 0.05]):
    ax = axes[idx // 2, idx % 2]
    grid = np.full((len(SIGMAS_S), len(ALPHAS_S)), np.nan)
    for r in agg:
        if r["cache_frac"] != cf: continue
        if r["policy"] not in ("GDSF", "W-TinyLFU"): continue
    for i, sig in enumerate(SIGMAS_S):
        for j, alf in enumerate(ALPHAS_S):
            wtlfu_v = next((r["mean_miss"] for r in agg
                          if r["alpha"]==alf and r["sigma"]==sig
                          and r["cache_frac"]==cf and r["policy"]=="W-TinyLFU"), None)
            gdsf_v = next((r["mean_miss"] for r in agg
                         if r["alpha"]==alf and r["sigma"]==sig
                         and r["cache_frac"]==cf and r["policy"]=="GDSF"), None)
            if wtlfu_v is not None and gdsf_v is not None:
                grid[i, j] = (wtlfu_v - gdsf_v) * 100  # positive = GDSF wins
    cmap = plt.get_cmap("RdBu_r")
    vmax = np.nanmax(np.abs(grid))
    im = ax.imshow(grid, origin="lower", aspect="auto", cmap=cmap,
                   vmin=-vmax, vmax=vmax,
                   extent=[ALPHAS_S[0]-0.05, ALPHAS_S[-1]+0.05,
                           SIGMAS_S[0]-0.25, SIGMAS_S[-1]+0.25])
    # annotate cells
    for i, sig in enumerate(SIGMAS_S):
        for j, alf in enumerate(ALPHAS_S):
            if not np.isnan(grid[i,j]):
                ax.text(alf, sig, f"{grid[i,j]:+.1f}",
                        ha="center", va="center", fontsize=7,
                        color="white" if abs(grid[i,j]) > vmax*0.5 else "black")
    ax.set_xlabel("Zipf α", fontsize=10); ax.set_ylabel("size σ (log-normal)", fontsize=10)
    ax.set_title(f"cache_frac = {cf}: W-TinyLFU obj-miss − GDSF obj-miss (pp)\n"
                 f"red = GDSF wins; blue = W-TinyLFU wins", fontsize=10)
    plt.colorbar(im, ax=ax, label="pp (positive = GDSF lower miss)")

fig.suptitle("Synthetic 1M-cell sweep: GDSF vs W-TinyLFU object-miss across (α, σ).\n"
             "GDSF dominates everywhere except very small caches at high σ.", fontsize=12, y=1.005)
fig.tight_layout()
fig.savefig(OUT_DIR / "gdsf_vs_wtlfu_surface.pdf", bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT_DIR / 'gdsf_vs_wtlfu_surface.pdf'}")
