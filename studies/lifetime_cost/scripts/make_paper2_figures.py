"""Generate Paper-2-engineering figures with descriptive (not internal) labels.

Outputs:
  fig_cache_hit_recall.png         - cache hit rate across recall variants
  fig_per_cell_recall.png          - per-cell wall: attn-unmask wins big or loses
  fig_eviction_modes.png           - byte-level eviction vs mask-based eviction wall
  fig_overlay_arch.png             - system architecture (engine subproc + IPC queues)
  fig_chain_hash.png               - the chain-hash invalidation problem and the fix
  fig_kv_recall_mechanism.png      - capture/release/restore/rotate/dual-key
  fig_paper2_progress.png          - mechanism progress (no internal phase numbers)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "reports" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 180,
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "legend.frameon": False,
    "font.family": "DejaVu Sans",
})

C_NONE  = "#7F7F7F"
C_GOOD  = "#009E73"
C_BAD   = "#D55E00"
C_PA1   = "#0072B2"
C_PA2   = "#E69F00"


def fig_cache_hit_recall(out: Path):
    variants = ["no recall\n(compact only)",
                "in-place\nrecall",
                "append\nrecall"]
    cache_pct = [73.0, 40.3, 67.4]
    colors = [C_NONE, C_BAD, C_PA1]

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    bars = ax.bar(variants, cache_pct, color=colors, alpha=0.9, edgecolor="white")
    for b, v in zip(bars, cache_pct):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.1f}%",
                ha="center", fontsize=11, weight="bold")

    ax.axhline(95, color="black", lw=0.8, ls="--", alpha=0.5)
    ax.text(2.4, 95.5, "target after\nauxiliary chain-hash\nregistration",
            fontsize=8.5, color="#222")

    ax.set_ylim(0, 105)
    ax.set_ylabel("prefix-cache hit rate (%)")
    ax.set_title("In-place recall eats a 27-point cache penalty:\n"
                 "the suffix tokens are unchanged, but their chain-hash key changes",
                 fontsize=11)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_eviction_modes(out: Path):
    """Byte-level eviction vs mask-based eviction across five recall regimes."""
    variants = ["compact-only", "LRU recall", "append recall",
                "embedding-driven\nrecall", "embedding-driven\nappend recall"]
    byte_eviction = [23.5, 18.8, 19.0, 10.3, 56.8]
    mask_eviction = [24.9, 26.0, 29.0, 34.7, 25.7]
    deltas = [a - b for a, b in zip(mask_eviction, byte_eviction)]

    x = np.arange(len(variants))
    w = 0.36

    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.bar(x - w/2, byte_eviction, w, color=C_PA1, alpha=0.9,
           label="byte-level eviction (KV restored on recall)")
    ax.bar(x + w/2, mask_eviction, w, color=C_PA2, alpha=0.9,
           label="attention-mask eviction (KV pinned, mask flips)")

    for xi, d in zip(x, deltas):
        ymax = max(byte_eviction[xi], mask_eviction[xi])
        sign = "+" if d > 0 else ""
        ax.text(xi, ymax + 1.5, f"{sign}{d:.0f}s",
                ha="center", fontsize=9.5,
                color=C_BAD if d > 0 else C_GOOD,
                weight="bold")

    ax.annotate("mask-based wins on the regime\nwith the most compaction events",
                xy=(4 + w/2, 25.7), xytext=(2.0, 50),
                fontsize=9.5, color=C_GOOD,
                arrowprops=dict(arrowstyle="->", color=C_GOOD, lw=1.2))

    ax.set_xticks(x)
    ax.set_xticklabels(variants, fontsize=9.5)
    ax.set_ylabel("wall time (s, mean across 3 seeds)")
    ax.set_title("Byte-level vs mask-based eviction:\n"
                 "the trade-off depends on how often compaction fires",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=9.5)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_per_cell_recall(out: Path):
    """Per-cell wall: text-append recall vs attention-unmask recall."""
    cells = [
        ("task A / seed 0",   87.2, 16.4),
        ("task B / seed 2",   81.7, 10.9),
        ("task C / seed 0",   41.9,  9.4),
        ("task D / seed 1",   23.3, 54.7),
        ("task D / seed 2",    6.2, 48.4),
    ]
    labels = [c[0] for c in cells]
    appends = [c[1] for c in cells]
    unmasks = [c[2] for c in cells]
    deltas = [b - a for a, b in zip(appends, unmasks)]

    x = np.arange(len(cells))
    w = 0.36

    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.bar(x - w/2, appends, w, color=C_PA1, alpha=0.9, label="text-append recall")
    ax.bar(x + w/2, unmasks, w, color=C_PA2, alpha=0.9, label="attention-unmask recall")

    for xi, d in zip(x, deltas):
        ymax = max(appends[xi], unmasks[xi])
        sign = "+" if d > 0 else ""
        ax.text(xi, ymax + 2.5, f"{sign}{d:.0f}s",
                ha="center", fontsize=9.5,
                color=C_BAD if d > 0 else C_GOOD,
                weight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylabel("wall time (s)")
    ax.set_title("Per-cell wall: attention-unmask recall wins big on recall-heavy cells,\n"
                 "loses when the agent's trajectory diverges and triggers more compactions",
                 fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    ax.text(2.5, 92, "wins: 4-8x faster", color=C_GOOD, fontsize=10, weight="bold")
    ax.text(3.5, 60, "losses: trajectory\ndivergence", color=C_BAD, fontsize=10, weight="bold")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_overlay_arch(out: Path):
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 9)
    ax.set_axis_off()

    def box(x, y, w, h, label, color, sub=""):
        rect = mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.18",
            linewidth=1.2, edgecolor="#222", facecolor=color, alpha=0.85,
        )
        ax.add_patch(rect)
        if sub:
            ax.text(x + w/2, y + h/2 + 0.20, label,
                    ha="center", va="center", fontsize=10.5, weight="bold", color="white")
            ax.text(x + w/2, y + h/2 - 0.35, sub,
                    ha="center", va="center", fontsize=8.5, color="white", style="italic")
        else:
            ax.text(x + w/2, y + h/2, label,
                    ha="center", va="center", fontsize=10.5, weight="bold", color="white")

    def arrow(x0, y0, x1, y1, label="", labelpos=(0, 0.2), color="#444"):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.4))
        if label:
            mx, my = (x0+x1)/2 + labelpos[0], (y0+y1)/2 + labelpos[1]
            ax.text(mx, my, label, fontsize=8.5, ha="center", color="#222")

    box(0.5, 6.5, 4.5, 1.6, "Agent loop + compaction policy", C_PA1,
        sub="(main process; budget tokenizer)")
    box(7.0, 6.5, 6.0, 1.6, "Inference engine subprocess", C_PA2,
        sub="(model tokenizer; paged KV cache)")

    box(5.2, 4.0, 3.8, 2.2, "IPC queues (JSONL files)", C_BAD)
    queue_lines = [
        "KV capture",
        "KV restore",
        "K rotation",
        "auxiliary chain-hash insert",
    ]
    for i, q in enumerate(queue_lines):
        ax.text(7.1, 5.7 - 0.32 * i, "- " + q, fontsize=9, color="white", family="monospace")

    box(7.3, 2.0, 2.6, 1.6, "Scheduler", "#1A6FA0",
        sub="drains queues each step;\nsplices block tables")
    box(10.2, 2.0, 2.6, 1.6, "GPU worker", "#1A6FA0",
        sub="executes capture / restore /\nrotate against page tensors")

    box(7.3, 0.2, 5.5, 1.4, "Side cache (engine-side)", "#456985",
        sub="content-hash key -> CPU pinned KV blocks")

    arrow(2.5, 6.4, 6.0, 6.0, "policy writes recall and capture\nrequests across the boundary",
          labelpos=(-1.6, -0.15))
    arrow(7.3, 5.3, 8.0, 3.7, "scheduler drains\non each step", labelpos=(0.6, 0.0))
    arrow(8.0, 2.0, 8.5, 1.7, color="#456985")
    arrow(11.0, 2.0, 10.5, 1.7, color="#456985")

    ax.text(0.6, 5.4, "Hash determinism:", fontsize=9, color="#222", weight="bold")
    ax.text(0.6, 5.0, "matching tokenizer + matching seed", fontsize=8.5, color="#222")
    ax.text(0.6, 4.6, "across BOTH processes is required.",
            fontsize=8.5, color="#222")

    ax.text(7.0, 8.4, "System architecture: agent + scheduler + worker over IPC",
            ha="center", fontsize=13, weight="bold")
    ax.text(7.0, 0.0,
            "Cross-process state is the source of most build-time bugs: any divergence "
            "between policy and engine views \nmanifests as a silent recall miss or a hard scheduler assert.",
            ha="center", fontsize=8.5, color="#444", style="italic")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_chain_hash(out: Path):
    fig, ax = plt.subplots(figsize=(11.5, 5.6))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 6.5)
    ax.set_axis_off()

    def block(x, y, w, h, label, color, hashlabel=""):
        rect = mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.10",
            linewidth=1.0, edgecolor="#222", facecolor=color, alpha=0.85,
        )
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, label,
                ha="center", va="center", fontsize=9.5, weight="bold", color="white")
        if hashlabel:
            ax.text(x + w/2, y - 0.3, hashlabel,
                    ha="center", va="center", fontsize=8, color="#222", family="monospace")

    ax.text(0.2, 5.8, "Step t (before compaction):", fontsize=10, weight="bold", color="#222")
    block(0.5, 4.5, 2.0, 1.0, "prefix",       "#7F7F7F", "H_0")
    block(2.7, 4.5, 3.0, 1.0, "tool obs (5K)", C_GOOD,   "H_1 = h(H_0, obs)")
    block(5.9, 4.5, 4.5, 1.0, "suffix (25K)",  C_PA1,    "H_2..H_n = h(H_{i-1}, ...)")
    ax.text(10.7, 5.0, "all cached", fontsize=10, color=C_GOOD, weight="bold")

    ax.text(0.2, 3.9, "Step t+1 (after compaction):", fontsize=10, weight="bold", color="#222")
    block(0.5, 2.5, 2.0, 1.0, "prefix",       "#7F7F7F", "H_0 (same)")
    block(2.7, 2.5, 0.9, 1.0, "[ev]",          C_BAD,    "H_1' != H_1")
    block(3.8, 2.5, 4.5, 1.0, "suffix (25K, same tokens)", C_PA1, "H_2'..H_n' (chained from H_1')")
    ax.text(8.5, 3.0, "chain mismatch:\nsuffix re-prefills",
            fontsize=10, color=C_BAD, weight="bold")

    ax.text(0.2, 1.7, "Auxiliary chain-hash registration:", fontsize=10, weight="bold", color="#222")
    ax.text(0.2, 1.2, "for each cached suffix block under H_i, ALSO register it under H_i'",
            fontsize=9.5, color="#222")
    ax.text(0.2, 0.7, "the cache walk hits the recall chain. Pure metadata operation. No data movement.",
            fontsize=9.5, color="#222")
    ax.text(0.2, 0.2, "the OLD entries are NEVER invalidated (the cache stays append-only).",
            fontsize=9.5, color=C_GOOD, weight="bold")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_kv_recall_mechanism(out: Path):
    """Five-stage mechanism diagram (no internal phase numbers)."""
    fig, ax = plt.subplots(figsize=(13.0, 5.6))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 7)
    ax.set_axis_off()

    def box(x, y, w, h, label, color, sub=""):
        rect = mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.18",
            linewidth=1.2, edgecolor="#222", facecolor=color, alpha=0.85,
        )
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2 + (0.18 if sub else 0), label,
                ha="center", va="center", fontsize=10.5, weight="bold", color="white")
        if sub:
            ax.text(x + w/2, y + h/2 - 0.42, sub,
                    ha="center", va="center", fontsize=8.5, color="white", style="italic")

    def arrow(x0, y0, x1, y1, label=""):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color="#444", lw=1.4))
        if label:
            ax.text((x0+x1)/2, (y0+y1)/2 + 0.18, label, fontsize=8.5, ha="center", color="#222")

    box(0.2, 4.2, 2.5, 1.4, "1. Capture", C_PA2,
        sub="GPU -> CPU pinned\nat compaction")
    box(3.0, 4.2, 2.5, 1.4, "2. Release", C_BAD,
        sub="no refcount pin;\nblocks fall to LRU")
    box(5.8, 4.2, 2.5, 1.4, "3. Restore", C_PA2,
        sub="CPU -> fresh GPU\nat recall")
    box(8.6, 4.2, 2.5, 1.4, "4. Rotate", C_GOOD,
        sub="apply R(delta)\nto suffix K")
    box(11.4, 4.2, 2.5, 1.4, "5. Aux. chain key",
        C_GOOD, sub="register under\nrecall chain (append-only)")

    for x in [2.7, 5.5, 8.3, 11.1]:
        arrow(x, 4.9, x + 0.3, 4.9)

    ax.text(7.0, 3.2, "Mathematical core: R(p+delta) = R(delta) o R(p)  ->  reuse engine rotary kernel",
            ha="center", fontsize=10, weight="bold", color="#222")
    ax.text(7.0, 2.5, "fp32 cosine similarity 1.0000000 across all (p, delta) tested",
            ha="center", fontsize=9, color="#222")
    ax.text(7.0, 1.85, "bf16 in paged KV layout: mean abs error ~3e-3, V untouched",
            ha="center", fontsize=9, color="#222")

    ax.text(7.0, 0.95, "End-to-end smoke (Qwen3-30B-A3B / FlashInfer / H100):",
            ha="center", fontsize=9, color="#222", weight="bold")
    ax.text(7.0, 0.45,
            "captures=3   restores=4   rotations=2 (1260 blocks)   pins_applied=0",
            ha="center", fontsize=9, color="#222", family="monospace")

    ax.set_title("Position-mobile prefix cache via in-place RoPE rotation",
                 fontsize=13, weight="bold", y=0.96)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_paper2_progress(out: Path):
    """Progress chart with descriptive labels (no internal phase numbers)."""
    rows = [
        ("synthetic 20-turn microbench",            4.05, 3.55, C_PA2),
        ("real swebench (15 steps,\n requests-3362)",  11.3, 9.3,  C_PA2),
        ("recall-heavy regime\n (mask vs append, 4 seeds)",  438.7, 392.7, C_GOOD),
        ("end-to-end smoke\n (release + restore + rotate)", float("nan"), 57.9, C_GOOD),
    ]
    labels = [r[0] for r in rows]
    bases  = [r[1] for r in rows]
    mechs  = [r[2] for r in rows]
    cols   = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    x = np.arange(len(rows))
    w = 0.36

    for xi, b in zip(x, bases):
        if not np.isnan(b):
            ax.bar(xi - w/2, b, w, color=C_NONE, alpha=0.85,
                   label="baseline / prior mechanism" if xi == 0 else None)
    for xi, m, c in zip(x, mechs, cols):
        ax.bar(xi + w/2, m, w, color=c, alpha=0.95)

    for xi, b, m in zip(x, bases, mechs):
        if not np.isnan(b):
            pct = (m - b) / b * 100
            ax.text(xi + w/2, m + max(bases) * 0.012, f"{pct:+.0f}%",
                    ha="center", fontsize=9, color="#222")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylabel("wall time (s)")
    ax.set_title("Mechanism progress (each entry beats its own predecessor; NOT 'beats no compaction')",
                 fontsize=11.5)

    ax.legend(handles=[
        mpatches.Patch(color=C_NONE, label="baseline (no compaction or prior mechanism)"),
        mpatches.Patch(color=C_PA2,  label="prompt-bounding mechanism"),
        mpatches.Patch(color=C_GOOD, label="KV-level mechanism"),
    ], loc="upper left", fontsize=9.5)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    fig_cache_hit_recall(FIG_DIR / "fig_cache_hit_recall.png")
    fig_eviction_modes(FIG_DIR / "fig_eviction_modes.png")
    fig_per_cell_recall(FIG_DIR / "fig_per_cell_recall.png")
    fig_overlay_arch(FIG_DIR / "fig_overlay_arch.png")
    fig_chain_hash(FIG_DIR / "fig_chain_hash.png")
    fig_kv_recall_mechanism(FIG_DIR / "fig_kv_recall_mechanism.png")
    fig_paper2_progress(FIG_DIR / "fig_paper2_progress.png")
