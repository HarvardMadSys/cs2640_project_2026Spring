"""Generate Paper 2 / Memento track figures.

Outputs three PNGs to studies/lifetime_cost/reports/figures/:
  fig_memento_v0_per_step.png   — mechanism demo: per-step prompt size
  fig_memento_multi_seed.png    — N=3 seeds × 3 variants on pytest-7490
  fig_memento_oracle_gap.png    — loose oracle vs real-test FAIL_TO_PASS

Style matches make_report_figures.py (Wong 2011 palette, no top/right spines).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "reports" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Multi-seed data lives in the paper2 worktree
PAPER2_DIR = Path("/home/vlad/adaptivecache-paper2/studies/lifetime_cost/paper2/out_v0_swebench")

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 180,
    "font.size": 12,
    "axes.titlesize": 14,
    "axes.labelsize": 12,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "legend.frameon": False,
    "font.family": "DejaVu Sans",
})

# Wong 2011 palette + the talk deck's accent colors
COLORS = {
    "off":       "#7F7F7F",  # neutral gray (control)
    "lru":       "#0072B2",  # blue
    "embedding": "#009E73",  # green
    "baseline":  "#7F7F7F",
    "memento":   "#E69F00",  # orange (matches talk deck accent)
    "loose":     "#56B4E9",  # sky blue
    "real":      "#D55E00",  # vermilion
}


# ---------------------------------------------------------------------------
# Fig 1 — v0 mechanism demo: per-step prompt size on requests-3362
# Source: studies/lifetime_cost/paper2/reports/FINDINGS_swebench.md (verbatim)
# ---------------------------------------------------------------------------

def fig_v0_per_step(out: Path):
    """Clean baseline vs memento on pytest-7490 — both ran without crashing."""
    # pytest-7490, Qwen3-30B-A3B, no recall on memento, T=0.0
    # Source: out_v0_swebench/{baseline,memento}_pytest-dev__pytest-7490.json
    baseline = [5042, 5196, 5974, 6040, 6094, 10670, 14384, 18618, 22354, 23088]
    memento  = [5042, 5196, 5974, 6040, 6094, 10670, 14384, 18618, 14177, 12657,
                12993, 11164, 7905]

    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    ax.plot(range(len(baseline)), baseline, "-o", color=COLORS["baseline"],
            label="baseline (no compaction)", linewidth=2.0, markersize=5)
    ax.plot(range(len(memento)), memento, "-o", color=COLORS["memento"],
            label="memento (lazy)", linewidth=2.2, markersize=5)
    # Mark the first compaction event (step 8: 18618 → 14177)
    ax.annotate("first compaction\nfires here",
                xy=(8, memento[8]), xytext=(9.5, 22000),
                fontsize=10, color=COLORS["memento"],
                arrowprops=dict(arrowstyle="->", color=COLORS["memento"], lw=1.2))
    ax.set_xlabel("agent step")
    ax.set_ylabel("prompt tokens")
    ax.set_title("v0 mechanism — memento bounds the prompt; baseline grows unbounded\n"
                 "pytest-dev__pytest-7490, Qwen3-30B-A3B, both runs ran clean",
                 fontsize=12)
    ax.legend(loc="upper left")
    ax.set_xlim(-0.5, 13.5)
    ax.set_ylim(0, 26000)
    ax.set_yticks([0, 5000, 10000, 15000, 20000, 25000])
    ax.set_yticklabels(["0", "5K", "10K", "15K", "20K", "25K"])
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


# ---------------------------------------------------------------------------
# Fig 2 — Multi-seed N=3 on pytest-7490 (off / lru / embedding)
# Source: paper2/out_v0_swebench/validate_recall_summary.json (latest run)
# ---------------------------------------------------------------------------

def fig_multi_seed(out: Path):
    p = PAPER2_DIR / "validate_recall_summary.json"
    rows = json.loads(p.read_text())
    # Group by variant
    groups = {"off": [], "lru": [], "embedding": []}
    for r in rows:
        if r["variant"] in groups:
            groups[r["variant"]].append(r)

    variants = ["off", "lru", "embedding"]
    n = len(variants)

    # Resolve rate (loose oracle), mean reads, mean wall_s, mean recalls
    resolves = [sum(1 for r in groups[v] if r["resolved"]) / len(groups[v]) for v in variants]
    reads = [np.mean([sum(r["reads"].values()) for r in groups[v]]) for v in variants]
    walls = [np.mean([r["total_wall_ms"] / 1000 for r in groups[v]]) for v in variants]
    recalls = [np.mean([r["num_recalls"] for r in groups[v]]) for v in variants]

    fig, axes = plt.subplots(1, 4, figsize=(13.0, 4.0))
    x = np.arange(n)

    bar_colors = [COLORS[v] for v in variants]
    labels = ["off\n(control)", "LRU\nrecall", "embedding\nrecall"]

    # 1. Loose-oracle resolve rate
    axes[0].bar(x, resolves, color=bar_colors, edgecolor="white")
    axes[0].set_xticks(x); axes[0].set_xticklabels(labels)
    axes[0].set_ylim(0, 1.05)
    axes[0].set_yticks([0, 0.5, 1.0])
    axes[0].set_yticklabels(["0", "1/2", "1"])
    axes[0].set_title("Loose-oracle resolve rate", fontsize=11)
    for i, v in enumerate(resolves):
        axes[0].text(i, v + 0.04, f"{int(v*3)}/3", ha="center", fontsize=10)

    # 2. Mean reads
    axes[1].bar(x, reads, color=bar_colors, edgecolor="white")
    axes[1].set_xticks(x); axes[1].set_xticklabels(labels)
    axes[1].set_title("Mean read_file calls", fontsize=11)
    for i, v in enumerate(reads):
        axes[1].text(i, v + 0.2, f"{v:.1f}", ha="center", fontsize=10)
    axes[1].set_ylim(0, max(reads) * 1.25)

    # 3. Mean wall (s)
    axes[2].bar(x, walls, color=bar_colors, edgecolor="white")
    axes[2].set_xticks(x); axes[2].set_xticklabels(labels)
    axes[2].set_title("Mean wall time (s)", fontsize=11)
    for i, v in enumerate(walls):
        axes[2].text(i, v + 1.0, f"{v:.1f}", ha="center", fontsize=10)
    axes[2].set_ylim(0, max(walls) * 1.25)

    # 4. Mean recall fires/run
    axes[3].bar(x, recalls, color=bar_colors, edgecolor="white")
    axes[3].set_xticks(x); axes[3].set_xticklabels(labels)
    axes[3].set_title("Mean recalls fired / run", fontsize=11)
    for i, v in enumerate(recalls):
        axes[3].text(i, v + 0.1, f"{v:.1f}", ha="center", fontsize=10)
    axes[3].set_ylim(0, max(max(recalls), 1.0) * 1.25)

    fig.suptitle("Multi-seed (N=3, T=0.6) on pytest-7490 — recall variants vs no-recall control",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


# ---------------------------------------------------------------------------
# Fig 3 — Oracle gap: loose oracle vs real-test FAIL_TO_PASS
# Source: validator output on the multi-seed trajectories
# ---------------------------------------------------------------------------

def fig_oracle_gap(out: Path):
    variants = ["off", "lru", "embedding"]
    loose = [2/3, 3/3, 3/3]   # loose oracle resolves
    real = [0/3, 0/3, 0/3]    # real-test FAIL_TO_PASS resolves

    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    x = np.arange(len(variants))
    w = 0.36
    ax.bar(x - w/2, loose, w, color=COLORS["loose"], label="loose oracle (line-overlap)")
    ax.bar(x + w/2, real, w, color=COLORS["real"], label="real-test FAIL_TO_PASS (Phase D validator)")

    for i, v in enumerate(loose):
        ax.text(i - w/2, v + 0.04, f"{int(v*3)}/3", ha="center", fontsize=10)
    for i, v in enumerate(real):
        ax.text(i + w/2, v + 0.04, f"{int(v*3)}/3", ha="center", fontsize=10,
                color=COLORS["real"])

    ax.set_xticks(x); ax.set_xticklabels(["off", "LRU", "embedding"])
    ax.set_ylabel("resolve rate")
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.33, 0.67, 1.0])
    ax.set_yticklabels(["0", "1/3", "2/3", "3/3"])
    ax.set_title("Loose oracle says 2/3 → 3/3. Real tests say 0/3 → 0/3.\n"
                 "Recall does not improve real resolve at this agent quality (Qwen3-30B, single seed).",
                 fontsize=11)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.30), ncol=2)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_mechanism(out: Path):
    """Naïve eviction vs block masking — the two-panel mechanism diagram.

    Left panel (naïve): KV positions 0..9; we delete [3..6]; remaining tokens
    have to renumber to fill the hole. RoPE positions baked at compute time
    no longer match → attention math breaks.

    Right panel (block masking): KV positions 0..9; we MASK [3..6] (still in
    memory, position indices unchanged); 7..9 still numbered 7..9 → RoPE
    stays consistent → model attends to {0..2, 7..9} as if 3..6 weren't there.
    Bidirectional: we can lift the mask later — bytes are recovered without
    re-prefill.
    """
    fig, axes = plt.subplots(1, 2, figsize=(13.0, 4.5))

    LIVE = "#0072B2"      # blue: attended
    EVICT_RED = "#D55E00" # vermilion: dropped + renumbered (broken)
    MASK = "#BFBFBF"      # gray: in memory but masked
    OUTLINE = "#1F2937"
    TEXT = "#1F2937"

    box_w = 0.85
    box_h = 0.7
    gap = 0.12

    def draw_token(ax, x, y, label, *, fill, edge=OUTLINE, text_color="white"):
        rect = plt.Rectangle((x, y), box_w, box_h, facecolor=fill, edgecolor=edge,
                             linewidth=1.5, zorder=2)
        ax.add_patch(rect)
        ax.text(x + box_w / 2, y + box_h / 2, str(label),
                ha="center", va="center", fontsize=11, color=text_color,
                fontweight="bold", zorder=3)

    # ----- LEFT panel: naïve eviction -----
    ax = axes[0]
    ax.set_xlim(0, 11.5); ax.set_ylim(-1.3, 4.5)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("Naïve eviction — renumbering breaks RoPE", fontsize=13, pad=10)

    # Row 1: positions 0..9 all live
    y1 = 3.0
    ax.text(-0.2, y1 + box_h/2, "before:", ha="right", va="center", fontsize=11, color=TEXT)
    for i in range(10):
        x = i * (box_w + gap)
        draw_token(ax, x, y1, str(i), fill=LIVE)

    # Row 2: positions 0..9, but [3..6] are deleted, then 7..9 SLIDE LEFT to fill hole
    y2 = 1.2
    ax.text(-0.2, y2 + box_h/2, "after:", ha="right", va="center", fontsize=11, color=TEXT)
    # Positions 0..2 stay in place
    for i in range(3):
        x = i * (box_w + gap)
        draw_token(ax, x, y2, str(i), fill=LIVE)
    # Positions 7..9 slide to fill positions 3..5 — RENUMBERED
    for new_i, old in enumerate([7, 8, 9]):
        x = (3 + new_i) * (box_w + gap)
        draw_token(ax, x, y2, str(old), fill=EVICT_RED, text_color="white")
    # Annotation pointing at the renumbered tokens
    ax.annotate("token 7 now at position 3\n→ RoPE mismatch",
                xy=(3 * (box_w + gap) + box_w/2, y2),
                xytext=(2.8, -0.7),
                fontsize=10.5, color=EVICT_RED, ha="center",
                arrowprops=dict(arrowstyle="->", color=EVICT_RED, lw=1.2))

    # Tiny legend
    ax.text(11.3, y1 + box_h/2, "live KV", fontsize=10, color=LIVE,
            ha="right", va="center", fontweight="bold")
    ax.text(11.3, y2 + box_h/2, "renumbered", fontsize=10, color=EVICT_RED,
            ha="right", va="center", fontweight="bold")

    # ----- RIGHT panel: block masking -----
    ax = axes[1]
    ax.set_xlim(0, 11.5); ax.set_ylim(-1.3, 4.5)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("Block masking — positions stay; attention skips the masked range",
                 fontsize=13, pad=10)

    # Row 1: positions 0..9 all live
    y1 = 3.0
    ax.text(-0.2, y1 + box_h/2, "before:", ha="right", va="center", fontsize=11, color=TEXT)
    for i in range(10):
        x = i * (box_w + gap)
        draw_token(ax, x, y1, str(i), fill=LIVE)

    # Row 2: positions 0..9, [3..6] masked (gray, but still numbered)
    y2 = 1.2
    ax.text(-0.2, y2 + box_h/2, "masked:", ha="right", va="center", fontsize=11, color=TEXT)
    for i in range(10):
        x = i * (box_w + gap)
        if 3 <= i <= 6:
            draw_token(ax, x, y2, str(i), fill=MASK, text_color="#1F2937")
        else:
            draw_token(ax, x, y2, str(i), fill=LIVE)

    # Annotation: positions still 7-9, KV still in memory
    ax.annotate("token 7 still at position 7\n(KV in memory, attention skips 3–6)",
                xy=(7 * (box_w + gap) + box_w/2, y2 + box_h),
                xytext=(7.2, -0.7),
                fontsize=10.5, color=LIVE, ha="center",
                arrowprops=dict(arrowstyle="->", color=LIVE, lw=1.2))

    # Tiny legend
    ax.text(11.3, y1 + box_h/2, "attended", fontsize=10, color=LIVE,
            ha="right", va="center", fontweight="bold")
    ax.text(11.3, y2 + box_h/2, "masked", fontsize=10, color="#5D6271",
            ha="right", va="center", fontweight="bold")

    fig.suptitle("KV-cache mechanism — why we can hide and restore obs without breaking the model",
                 fontsize=14, y=0.99)

    # Bottom caption: bidirectional masking
    fig.text(0.5, 0.02,
             "Bidirectional masking:  recover obs KV from offload, keep the historical suffix KV, flip the mask.  No recompute. "
             "v1 prototype: text-level (re-prefills from obs).  v2 target: KV-level (cost ≈ 0).",
             ha="center", fontsize=10, style="italic", color=OUTLINE)

    fig.tight_layout(rect=[0, 0.04, 1, 0.96])
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_v1_vs_v2(out: Path):
    """v1 text-level recall (eats a suffix cliff) vs v2 KV-level mask toggle
    (no prompt change, no cliff).

    Three rows of token tapes:
      Row 1: post-compaction state — obs masked, memento markers in place
      Row 2: v1 recall — prompt rewritten without markers, full obs back
              → suffix tokens shift positions → cache miss
      Row 3: v2 recall — prompt UNCHANGED, only engine mask flag flipped
              → no token shifts → full cache hit
    """
    fig, ax = plt.subplots(figsize=(13.5, 7.0))
    ax.set_xlim(-1.5, 14.5)
    ax.set_ylim(-2.0, 13.5)
    ax.axis("off")

    LIVE = "#0072B2"   # cached, fresh, attended (blue)
    MASK = "#BFBFBF"   # masked (gray, KV in memory but not attended)
    MEM = "#E69F00"    # memento markers + summary (orange)
    MISS = "#D55E00"   # cache miss (red)
    STALE = "#56B4E9"  # stale-but-kept (light blue) — KV present, prefilled
                       # under the mask; we keep using it as historical truth
    OUT = "#1F2937"

    bw, bh, gap = 0.95, 0.7, 0.08

    def box(x, y, label, *, fill, edge=OUT, text="white", small=False):
        rect = plt.Rectangle((x, y), bw, bh, facecolor=fill, edgecolor=edge,
                              linewidth=1.5)
        ax.add_patch(rect)
        ax.text(x + bw / 2, y + bh / 2, label,
                ha="center", va="center",
                fontsize=8.5 if small else 10.5,
                color=text, fontweight="bold")

    def tape(y, cells, label):
        """cells = list of (text, color, label?)"""
        ax.text(-0.4, y + bh/2, label, ha="right", va="center",
                fontsize=11, fontweight="bold", color=OUT)
        for i, (txt, color) in enumerate(cells):
            x = i * (bw + gap)
            txt_color = "#1F2937" if color in (MASK, "#FFFFFF") else "white"
            small = len(txt) > 6
            box(x, y, txt, fill=color, text=txt_color, small=small)
        return len(cells) * (bw + gap)

    title = ("v1: full re-prefill from the obs.   "
             "v2: recover obs KV from offload, keep the historical suffix as-is, flip the mask.  "
             "Generation continues — no recompute.")
    ax.set_title(title, fontsize=12.5, pad=12)

    # ROW 1 — post-compaction steady state
    y1 = 10.5
    cells_1 = [
        ("sys", LIVE),
        ("user", LIVE),
        ("asst1", LIVE),
        ("<tr>", MEM),
        ("obs", MASK),    # original obs, masked
        ("</tr>", MEM),
        ("<fp>", MEM),
        ("memento", MEM),
        ("<fm>", MEM),
        ("asst2", LIVE),
        ("obs2", LIVE),
        ("asst3", LIVE),
        ("obs3", LIVE),
    ]
    tape(y1, cells_1, "post-compaction:")

    # ROW 2 — v1 recall: prompt rewritten, suffix shifts position → miss
    y2 = 6.5
    cells_2 = [
        ("sys", LIVE),
        ("user", LIVE),
        ("asst1", LIVE),
        ("[tr]", MISS),
        ("obs", MISS),
        # markers/memento removed entirely; everything else shifts left
        ("asst2", MISS),
        ("obs2", MISS),
        ("asst3", MISS),
        ("obs3", MISS),
    ]
    tape(y2, cells_2, "v1 recall:")
    # Annotation ABOVE the row (between row 1 and row 2)
    ax.text(6.0 * (bw + gap), y2 + bh + 0.5,
            "↑ markers + memento removed → token IDs at all later positions change",
            ha="center", va="center", fontsize=10, color=MISS, fontweight="bold")
    # Annotation BELOW the row
    ax.text(6.0 * (bw + gap), y2 - 0.45,
            "all positions shifted → prefix cache misses → re-prefill the suffix",
            ha="center", va="center", fontsize=10, color=MISS, fontweight="bold")

    # ROW 3 — v2 recall: KV all kept; obs KV restored from offload; suffix
    # KV is "stale" (computed under the mask) but that's the historical
    # truth — the agent really did proceed with only the memento visible.
    # Future generation attends to obs (un-masked) AND historical suffix.
    y3 = 2.5
    cells_3 = [
        ("sys", LIVE),
        ("user", LIVE),
        ("asst1", LIVE),
        ("<tr>", MEM),
        ("obs", LIVE),       # un-masked, KV recovered from offload
        ("</tr>", MEM),
        ("<fp>", MEM),
        ("memento", MEM),
        ("<fm>", MEM),
        ("asst2", STALE),    # historical: suffix really did happen
        ("obs2", STALE),     # under memento-world. We keep the KV.
        ("asst3", STALE),
        ("obs3", STALE),
    ]
    tape(y3, cells_3, "v2 recall:")
    # Annotation ABOVE the row
    ax.text(6.0 * (bw + gap), y3 + bh + 0.5,
            "↑ obs KV recovered from CPU offload + mask flipped.  Suffix KV is kept — it's the historical truth.",
            ha="center", va="center", fontsize=9.5, color=LIVE, fontweight="bold")
    # Annotation BELOW the row — honest version
    ax.text(6.0 * (bw + gap), y3 - 0.45,
            "Cost ≈ 0.  Generation continues — attends to obs (un-masked) AND to the historical suffix (memento-world).",
            ha="center", va="center", fontsize=9.5, color=OUT, fontweight="bold")

    # Legend below — single row, generous spacing
    legend_y = -1.4
    items = [
        ("cached", LIVE),
        ("stale (kept; historical truth)", STALE),
        ("memento markers", MEM),
        ("masked (KV kept)", MASK),
        ("cache miss / re-prefill", MISS),
    ]
    # spread across full width
    lx = 0.0
    for txt, c in items:
        rect = plt.Rectangle((lx, legend_y), 0.45, 0.4, facecolor=c,
                              edgecolor=OUT, linewidth=1.0)
        ax.add_patch(rect)
        ax.text(lx + 0.55, legend_y + 0.2, txt,
                ha="left", va="center", fontsize=10, color=OUT)
        lx += 2.85

    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def main():
    fig_v0_per_step(FIG_DIR / "fig_memento_v0_per_step.png")
    fig_multi_seed(FIG_DIR / "fig_memento_multi_seed.png")
    fig_oracle_gap(FIG_DIR / "fig_memento_oracle_gap.png")
    fig_mechanism(FIG_DIR / "fig_memento_mechanism.png")
    fig_v1_vs_v2(FIG_DIR / "fig_memento_v1_vs_v2.png")


if __name__ == "__main__":
    main()
