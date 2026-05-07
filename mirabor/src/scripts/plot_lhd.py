#!/usr/bin/env python3
"""LHD-extended hero: 9 policies in (object-miss, byte-miss) space, Court focal."""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
LHD = ROOT / "results" / "v4_lhd" / "summary.csv"
V4 = ROOT / "results" / "v4" / "summary.csv"
OUT_DIR = ROOT / "results" / "v4_lhd" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)

rows = list(csv.DictReader(open(LHD)))
# also pull S3-FIFO/CLOCK/FIFO from v4 (LHD sweep didn't include them)
v4_rows = list(csv.DictReader(open(V4)))
extra_rows = [r for r in v4_rows if r["policy"] in ("FIFO","CLOCK","S3-FIFO")]
rows = rows + extra_rows
for r in rows:
    r["alpha"] = float(r["alpha"]); r["cache_frac"] = float(r["cache_frac"])
    r["obj_miss_mean"] = float(r["obj_miss_mean"]); r["byte_miss_mean"] = float(r["byte_miss_mean"])

POLICY_COLORS = {
    "LRU": "#7f7f7f", "FIFO": "#bcbd22", "CLOCK": "#9467bd",
    "S3-FIFO": "#2ca02c", "SIEVE": "#1f77b4",
    "W-TinyLFU": "#d62728", "GDSF": "#e67e22", "GDSF-Cost": "#ff7f0e",
    "LHD": "#17becf",
}
POLICY_MARKERS = {
    "LRU": "o", "FIFO": "v", "CLOCK": "^", "S3-FIFO": "<",
    "SIEVE": "s", "W-TinyLFU": "D", "GDSF": "P", "GDSF-Cost": "X",
    "LHD": "*",
}

fig, axes = plt.subplots(1, 2, figsize=(13, 5.4))
for ax, wname, title in [(axes[0], "court", "Court (heavy-tail, α≈1.03)"),
                          (axes[1], "congress", "Congress (light-tail, α≈0.23)")]:
    sub = [r for r in rows if r["workload"] == wname and r["cache_frac"] == 0.01]
    for policy in sorted(set(r["policy"] for r in sub)):
        prows = sorted([r for r in sub if r["policy"] == policy], key=lambda r: r["alpha"])
        if not prows: continue
        xs = [r["obj_miss_mean"] for r in prows]
        ys = [r["byte_miss_mean"] for r in prows]
        ax.plot(xs, ys, "-", color=POLICY_COLORS.get(policy, "black"), alpha=0.4, linewidth=1.2)
        ms = 200 if policy == "LHD" else 80
        ax.scatter(xs, ys, marker=POLICY_MARKERS.get(policy, "o"),
                   color=POLICY_COLORS.get(policy, "black"),
                   s=ms, edgecolor="black", linewidth=0.6,
                   label=policy, zorder=3)
        for i, r in enumerate(prows):
            ax.annotate(f"α={r['alpha']}", (xs[i], ys[i]),
                       textcoords="offset points", xytext=(4, 5),
                       fontsize=7, alpha=0.6)
    ax.set_xlabel("object-miss ratio (lower better)", fontsize=11)
    ax.set_ylabel("byte-miss ratio (lower better)", fontsize=11)
    ax.set_title(title + ", cache_frac=0.01", fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best", ncol=2, framealpha=0.92)

fig.suptitle("Nine policies in (object-miss, byte-miss) space: GDSF wins object-miss; "
             "W-TinyLFU wins byte-miss; LHD is a Pareto middle-ground.",
             fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(OUT_DIR / "obj_vs_byte_with_lhd.pdf", bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT_DIR / 'obj_vs_byte_with_lhd.pdf'}")
