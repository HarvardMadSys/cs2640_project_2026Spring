#!/usr/bin/env python3
"""
Plot the (α × σ_size) crossover heatmaps from sweep_alpha_sigma/agg.csv.

Outputs:
  results/sweep_alpha_sigma/figures/crossover_heatmap.pdf  — 2x2 grid (4 cache fractions)
  results/sweep_alpha_sigma/figures/crossover_focal.pdf    — single-panel focal (cache_frac=0.01)
  results/sweep_alpha_sigma/figures/gap_vs_cache_frac.pdf  — line plot at 3 representative (α,σ) cells
  results/sweep_alpha_sigma/figures/wtinylfu_only_heatmap.pdf — W-TinyLFU miss-ratio surface for §3 expository
"""

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ROOT = Path(__file__).resolve().parent.parent
AGG  = ROOT / "results" / "sweep_alpha_sigma" / "agg.csv"
FIG  = ROOT / "results" / "sweep_alpha_sigma" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

ALPHAS = [0.4, 0.6, 0.8, 1.0, 1.2, 1.4]
SIGMAS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
CACHE_FRACS = [0.001, 0.005, 0.01, 0.05]

# ---------- load ----------
rows = list(csv.DictReader(open(AGG)))

def grid(policy: str, cache_frac: float, key: str = "mean_miss"):
    g = np.full((len(ALPHAS), len(SIGMAS)), np.nan)
    ai = {a: i for i, a in enumerate(ALPHAS)}
    si = {s: j for j, s in enumerate(SIGMAS)}
    for r in rows:
        cf = float(r["cache_frac"])
        if abs(cf - cache_frac) > 1e-9: continue
        if r["policy"] != policy: continue
        a = float(r["alpha"]); s = float(r["sigma"])
        if a in ai and s in si:
            g[ai[a], si[s]] = float(r[key])
    return g

def gap_grid(cache_frac: float):
    """SIEVE − W-TinyLFU. Positive = W-TinyLFU wins."""
    return grid("SIEVE", cache_frac) - grid("W-TinyLFU", cache_frac)

# observed real-trace coordinates (from §3 of the report)
CONGRESS_POINT = (0.231, 0.55)   # α MLE = 0.23; raw size σ ≈ 0.55 (median 231B, max 6700B)
COURT_POINT    = (1.028, 1.70)   # α MLE = 1.03; raw size σ ≈ 1.70 (median 1381B, max 462490B)

# ---------- 2x2 heatmap ----------
fig, axes = plt.subplots(2, 2, figsize=(11, 9))
fig.suptitle(r"SIEVE $-$ W-TinyLFU miss-ratio gap: continuous decision surface",
             fontsize=15, y=0.995)

for ax, cf in zip(axes.flat, CACHE_FRACS):
    g = gap_grid(cf)
    vmax = max(0.20, np.nanmax(np.abs(g)))
    norm = TwoSlopeNorm(vcenter=0, vmin=-vmax, vmax=vmax)
    im = ax.imshow(g, origin="lower", aspect="auto", cmap="RdBu_r", norm=norm,
                   extent=[SIGMAS[0]-0.25, SIGMAS[-1]+0.25,
                           ALPHAS[0]-0.1, ALPHAS[-1]+0.1])
    cs = ax.contour(SIGMAS, ALPHAS, g, levels=[0.0], colors="k",
                    linewidths=2.0, linestyles="--")
    if len(cs.allsegs[0]) > 0:
        ax.clabel(cs, fmt="tied", inline=True, fontsize=9)
    # gap-value labels
    for i, a in enumerate(ALPHAS):
        for j, s in enumerate(SIGMAS):
            v = g[i, j]
            color = "white" if abs(v) > vmax * 0.55 else "black"
            ax.text(s, a, f"{v:+.2f}", ha="center", va="center",
                    fontsize=8.2, color=color)

    ax.set_title(f"cache_frac = {cf:g}", fontsize=11)
    ax.set_xlabel(r"size-distribution $\sigma$  (heavier tail $\rightarrow$)", fontsize=10)
    ax.set_ylabel(r"raw Zipf $\alpha$", fontsize=10)
    ax.set_xticks(SIGMAS); ax.set_yticks(ALPHAS)

    # mark observed real-trace operating points
    cs_a, cs_s = CONGRESS_POINT
    co_a, co_s = COURT_POINT
    ax.plot([cs_s], [cs_a], marker="o", markersize=10, markerfacecolor="white",
            markeredgecolor="black", markeredgewidth=1.4, zorder=5)
    ax.annotate("Congress\n(measured)", xy=(cs_s, cs_a), xytext=(cs_s+0.05, cs_a-0.05),
                fontsize=8.5, color="black", fontweight="bold")
    ax.plot([co_s], [co_a], marker="s", markersize=10, markerfacecolor="white",
            markeredgecolor="black", markeredgewidth=1.4, zorder=5)
    ax.annotate("Court\n(measured)", xy=(co_s, co_a), xytext=(co_s-0.55, co_a+0.05),
                fontsize=8.5, color="black", fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, fraction=0.038, pad=0.04)
    cbar.set_label("gap (pp; +ve = W-TinyLFU wins)", fontsize=9)

fig.tight_layout(rect=[0, 0, 1, 0.97])
out = FIG / "crossover_heatmap.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- single-panel focal (cache_frac=0.01) ----------
fig, ax = plt.subplots(figsize=(7.5, 5.8))
g = gap_grid(0.01)
vmax = max(0.20, np.nanmax(np.abs(g)))
norm = TwoSlopeNorm(vcenter=0, vmin=-vmax, vmax=vmax)
im = ax.imshow(g, origin="lower", aspect="auto", cmap="RdBu_r", norm=norm,
               extent=[SIGMAS[0]-0.25, SIGMAS[-1]+0.25,
                       ALPHAS[0]-0.1, ALPHAS[-1]+0.1])
cs = ax.contour(SIGMAS, ALPHAS, g, levels=[0.0], colors="k",
                linewidths=2.5, linestyles="--")
ax.clabel(cs, fmt="SIEVE = W-TinyLFU", inline=True, fontsize=9)
for i, a in enumerate(ALPHAS):
    for j, s in enumerate(SIGMAS):
        v = g[i, j]
        color = "white" if abs(v) > vmax * 0.55 else "black"
        ax.text(s, a, f"{v:+.3f}", ha="center", va="center", fontsize=9, color=color)
ax.set_title(r"Continuous decision surface @ cache$\_$frac = 0.01" +
             "\n(positive = W-TinyLFU wins; negative = SIEVE wins; ~0 = tied)",
             fontsize=12)
ax.set_xlabel(r"size-distribution $\sigma$  (log-normal; heavier tail $\rightarrow$)", fontsize=11)
ax.set_ylabel(r"raw Zipf $\alpha$  (higher skew $\uparrow$)", fontsize=11)
ax.set_xticks(SIGMAS); ax.set_yticks(ALPHAS)

cs_a, cs_s = CONGRESS_POINT
co_a, co_s = COURT_POINT
ax.plot([cs_s], [cs_a], marker="o", markersize=14, markerfacecolor="white",
        markeredgecolor="black", markeredgewidth=1.6, zorder=5,
        label=f"Congress (α={cs_a:.2f}, σ≈{cs_s})")
ax.plot([co_s], [co_a], marker="s", markersize=14, markerfacecolor="white",
        markeredgecolor="black", markeredgewidth=1.6, zorder=5,
        label=f"Court (α={co_a:.2f}, σ≈{co_s})")
ax.legend(loc="lower right", fontsize=9, framealpha=0.92)

cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cbar.set_label("SIEVE − W-TinyLFU miss-ratio gap (percentage points)", fontsize=10)

fig.tight_layout()
out = FIG / "crossover_focal.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- gap vs cache_frac at three representative cells ----------
fig, ax = plt.subplots(figsize=(7.5, 4.5))
cells = [
    (0.4, 0.5, "Congress-like (α=0.4, σ=0.5)", "C0"),
    (1.0, 1.5, "Court-like (α=1.0, σ=1.5)",    "C1"),
    (1.2, 2.5, "Heavy-tail synthetic (α=1.2, σ=2.5)", "C3"),
]
for a, s, label, color in cells:
    ys = []
    for cf in CACHE_FRACS:
        g = gap_grid(cf)
        ai = ALPHAS.index(a); si = SIGMAS.index(s)
        ys.append(g[ai, si])
    ax.plot(CACHE_FRACS, ys, marker="o", linewidth=2.0, markersize=8,
            color=color, label=label)
ax.axhline(0, color="k", linewidth=0.8, linestyle="--", alpha=0.6)
ax.set_xscale("log")
ax.set_xlabel("cache fraction (log)", fontsize=11)
ax.set_ylabel("SIEVE − W-TinyLFU gap (pp)", fontsize=11)
ax.set_title("Admission-filter benefit shrinks at large cache, regardless of workload",
             fontsize=12)
ax.legend(fontsize=9.5, framealpha=0.9)
ax.grid(alpha=0.3)
fig.tight_layout()
out = FIG / "gap_vs_cache_frac.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")

# ---------- W-TinyLFU absolute miss-ratio surface (expository for §3) ----------
fig, ax = plt.subplots(figsize=(7.5, 5.8))
g = grid("W-TinyLFU", 0.01)
im = ax.imshow(g, origin="lower", aspect="auto", cmap="viridis_r",
               extent=[SIGMAS[0]-0.25, SIGMAS[-1]+0.25,
                       ALPHAS[0]-0.1, ALPHAS[-1]+0.1], vmin=0, vmax=1)
for i, a in enumerate(ALPHAS):
    for j, s in enumerate(SIGMAS):
        v = g[i, j]
        ax.text(s, a, f"{v:.2f}", ha="center", va="center", fontsize=9,
                color="white" if v > 0.5 else "black")
ax.set_title(r"W-TinyLFU absolute miss-ratio @ cache$\_$frac = 0.01"
             "\n(lower is better)", fontsize=12)
ax.set_xlabel(r"size-distribution $\sigma$", fontsize=11)
ax.set_ylabel(r"raw Zipf $\alpha$", fontsize=11)
ax.set_xticks(SIGMAS); ax.set_yticks(ALPHAS)
cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cbar.set_label("miss ratio", fontsize=10)
fig.tight_layout()
out = FIG / "wtinylfu_only_heatmap.pdf"
fig.savefig(out, bbox_inches="tight"); plt.close(fig)
print(f"Wrote {out}")
