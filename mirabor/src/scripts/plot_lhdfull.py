#!/usr/bin/env python3
"""Updated hero figure with LHD-Full vs LHD-Lite comparison."""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
LHDFULL = ROOT / "results" / "v5_lhdfull" / "summary.csv"
LHDLITE = ROOT / "results" / "v4_lhd" / "summary.csv"
V4 = ROOT / "results" / "v4" / "summary.csv"
OUT = ROOT / "results" / "v5_lhdfull" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

rows = list(csv.DictReader(open(LHDFULL)))
# also add SIEVE/W-TinyLFU/GDSF from lhdfull (already there)
# pull lhd-lite from older sweep, label as LHD-Lite
lite = list(csv.DictReader(open(LHDLITE)))
for r in lite:
    if r["policy"] == "LHD":
        r["policy"] = "LHD-Lite"
        rows.append(r)
# pull LRU
extra = [r for r in csv.DictReader(open(V4)) if r["policy"] == "LRU"]
rows.extend(extra)

for r in rows:
    r["alpha"] = float(r["alpha"])
    r["cache_frac"] = float(r["cache_frac"])
    r["obj_miss_mean"] = float(r["obj_miss_mean"])
    r["byte_miss_mean"] = float(r["byte_miss_mean"])

POLICY_COLORS = {
    "LRU": "#7f7f7f", "SIEVE": "#1f77b4",
    "W-TinyLFU": "#d62728", "GDSF": "#e67e22",
    "LHD-Lite": "#a0a0a0", "LHD-Full": "#17becf",
}
POLICY_MARKERS = {
    "LRU": "o", "SIEVE": "s", "W-TinyLFU": "D", "GDSF": "P",
    "LHD-Lite": "v", "LHD-Full": "*",
}

fig, axes = plt.subplots(1, 2, figsize=(13, 5.4))
for ax, wname, title in [(axes[0], "court", "Court (heavy-tail, α≈1.03)"),
                          (axes[1], "congress", "Congress (light-tail, α≈0.23)")]:
    sub = [r for r in rows if r["workload"] == wname and r["cache_frac"] == 0.01]
    for policy in ["LRU", "SIEVE", "W-TinyLFU", "GDSF", "LHD-Lite", "LHD-Full"]:
        prows = sorted([r for r in sub if r["policy"] == policy], key=lambda r: r["alpha"])
        if not prows: continue
        xs = [r["obj_miss_mean"] for r in prows]
        ys = [r["byte_miss_mean"] for r in prows]
        ax.plot(xs, ys, "-", color=POLICY_COLORS.get(policy, "black"), alpha=0.4, linewidth=1.2)
        ms = 220 if policy.startswith("LHD") else 80
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

fig.suptitle("Six policies in (object-miss, byte-miss) space. LHD-Full (★, with 2-D class) "
             "is a genuine Pareto middle-ground; LHD-Lite (▼, age-only class) is dominated.",
             fontsize=11, y=1.02)
fig.tight_layout()
fig.savefig(OUT / "obj_vs_byte_lhdfull.pdf", bbox_inches="tight"); plt.close(fig)
print(f"Wrote {OUT / 'obj_vs_byte_lhdfull.pdf'}")
