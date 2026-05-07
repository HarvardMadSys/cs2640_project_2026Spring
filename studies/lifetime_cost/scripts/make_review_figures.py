"""Generate figures for the comprehensive AdaptiveCache project review.

Outputs three new PNGs to studies/lifetime_cost/reports/figures/:
  fig_review_timeline.png       - full project arc Apr-May 2026
  fig_review_paper2_progress.png - v0 -> v1/v2 -> v3 -> v4 -> Phase 8 -> Phase 9
  fig_review_phase9_mechanism.png - capture/release/restore/rotate/dual-key

Style matches make_report_figures.py.
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

C_PROPOSAL = "#7F7F7F"
C_PAPER1   = "#0072B2"
C_PIVOT    = "#D55E00"
C_PAPER2   = "#E69F00"
C_PHASE9   = "#009E73"


def fig_timeline(out: Path):
    """Horizontal Gantt-style of the whole project arc."""
    rows = [
        # (start_day, end_day, label, color, lane)
        (0,  3,  "Proposal\n(Apr 4)",                          C_PROPOSAL, 0),
        (3,  20, "Lit survey + design refinement\n(Layout-gap thesis, midterm)", C_PROPOSAL, 0),
        (20, 24, "Phase A: measurement\n(importance proxies, position primacy, cliff $)", C_PAPER1, 1),
        (24, 26, "Phase B: tau-bench airline\n(nothing to compact)", C_PAPER1, 1),
        (24, 27, "Phase C: SWE-bench Lite v1-v6\n(8 policies; cliff dominates)", C_PAPER1, 1),
        (27, 28, "Phase D: action-graph supersession\nv1 promising, v2 N=10 doesn't replicate", C_PAPER1, 1),
        (28, 29, "Phase E: placeholder ablation +\nchain harness (rules don't transfer)", C_PAPER1, 1),
        (28, 29, "PIVOT: cliff tax = wall.\nKeep the bytes -> Paper 2.", C_PIVOT, 2),
        (28, 29, "Paper 2 v0: Memento overlay\n(microbench -12%, swebench -17%)", C_PAPER2, 3),
        (29, 32, "v1 inplace + v2 append recall\n(prompt-layer recall, both work)", C_PAPER2, 3),
        (32, 34, "v3: KV capture + CPU offload\n+ scheduler restore (Phase 3a-3c)", C_PAPER2, 3),
        (33, 35, "v4 KV mask: pin obs blocks +\nblock_table filter (4a-4d)", C_PAPER2, 3),
        (34, 35, "Phase 4e: recall via attn-unmask\n(-29% wall in recall-heavy regime)", C_PAPER2, 3),
        (35, 36, "Phase 8: dual-key chain hashes\n(no-pin lru-inplace cache hit 40->>90%)", C_PHASE9, 4),
        (36, 37, "Phase 9: K-rotation kernel\nrelease + restore + rotate + dual-key", C_PHASE9, 4),
    ]

    fig, ax = plt.subplots(figsize=(12.5, 6.6))
    lane_labels = {
        0: "Pre-empirical",
        1: "Paper 1 - lifetime cost study",
        2: "Pivot",
        3: "Paper 2 - KV-pointer recall",
        4: "Paper 2 - Phase 8/9 (no re-prefill)",
    }
    for s, e, lbl, c, lane in rows:
        ax.barh(lane, e - s, left=s, height=0.7, color=c, alpha=0.85, edgecolor="white")
        ax.text(s + (e - s) / 2, lane, lbl, ha="center", va="center",
                fontsize=8.5, color="white", weight="bold")

    ax.set_yticks(sorted(lane_labels.keys()))
    ax.set_yticklabels([lane_labels[k] for k in sorted(lane_labels.keys())])
    ax.invert_yaxis()
    ax.set_xlim(-0.5, 38)
    ax.set_xlabel("Days since proposal (Apr 4 -> May 6, 2026)")
    ax.set_title("AdaptiveCache project arc: 33 days from proposal to Phase 9 KV-rotation",
                 fontsize=13, weight="bold")

    # Annotate phase boundaries
    for x, label in [(20, "midterm"), (28, "Paper 1 negative result"), (35, "v4 wins on recall-heavy")]:
        ax.axvline(x, color="black", lw=0.6, ls=":", alpha=0.4)
        ax.text(x, -0.85, label, rotation=0, fontsize=8.5, ha="center", color="#444")

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_paper2_progress(out: Path):
    """Wall-time progress chart across Paper 2 milestones."""
    # Each milestone: (label, baseline_wall_s, mechanism_wall_s, note_color)
    rows = [
        ("v0 microbench\n20-turn synthetic",      4.05, 3.55, C_PAPER2),  # 4052ms vs 3554ms
        ("v0 swebench\nrequests-3362, 15 steps",  11.3, 9.3,  C_PAPER2),  # baseline 11.3s vs memento 9.3s chat
        ("v4 Phase 4d\nembedding-append, pytest-7490",  56.8, 25.7, C_PAPER2),  # v3 baseline vs v4 mask
        ("v4 Phase 4e\n4 tasks x 4 seeds, recall-heavy",  438.7, 392.7, C_PHASE9),  # lru-append vs lru-attmask
        ("Phase 9 smoke\n(release+restore+rotate)", float("nan"), 57.9, C_PHASE9),  # b4zbb5be4 single-task
    ]

    labels = [r[0] for r in rows]
    bases  = [r[1] for r in rows]
    mechs  = [r[2] for r in rows]
    cols   = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(11.0, 5.4))
    x = np.arange(len(rows))
    w = 0.36

    # Draw baseline bars where data exists
    for xi, b in zip(x, bases):
        if not np.isnan(b):
            ax.bar(xi - w/2, b, w, color=C_PROPOSAL, alpha=0.85, label="baseline / prior" if xi == 0 else None)

    # Mechanism bars
    for xi, m, c in zip(x, mechs, cols):
        ax.bar(xi + w/2, m, w, color=c, alpha=0.95)

    # delta annotations
    for xi, b, m in zip(x, bases, mechs):
        if not np.isnan(b):
            pct = (m - b) / b * 100
            ax.text(xi + w/2, m + max(bases) * 0.012, f"{pct:+.0f}%",
                    ha="center", fontsize=9, color="#222")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("wall time (s)")
    ax.set_title("Paper 2 progress: each milestone where the mechanism beats its predecessor",
                 fontsize=12)

    # custom legend
    ax.legend(handles=[
        mpatches.Patch(color=C_PROPOSAL, label="baseline (no compaction or prior phase)"),
        mpatches.Patch(color=C_PAPER2,   label="Memento prompt-layer mechanism"),
        mpatches.Patch(color=C_PHASE9,   label="KV-level mechanism (Phase 4e / 8 / 9)"),
    ], loc="upper left", fontsize=9)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_phase9_mechanism(out: Path):
    """Boxes-and-arrows diagram of capture -> release -> restore -> rotate -> dual-key."""
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

    # Top row: lifecycle boxes
    box(0.2, 4.2, 2.5, 1.4, "1. Capture", C_PAPER2,
        sub="GPU→CPU pinned\nat compaction")
    box(3.0, 4.2, 2.5, 1.4, "2. Release", C_PIVOT,
        sub="no refcount pin\nblocks fall to LRU")
    box(5.8, 4.2, 2.5, 1.4, "3. Restore", C_PAPER2,
        sub="CPU→fresh GPU\nat recall time")
    box(8.6, 4.2, 2.5, 1.4, "4. Rotate", C_PHASE9,
        sub="apply R(Δ) to suffix K\nin-place rotary kernel")
    box(11.4, 4.2, 2.5, 1.4, "5. Dual-key", C_PHASE9,
        sub="insert recall-chain\nhashes (append-only)")

    # arrows
    for x in [2.7, 5.5, 8.3, 11.1]:
        arrow(x, 4.9, x + 0.3, 4.9)

    # Bottom: per-stage measurement
    ax.text(7.0, 3.2, "Mathematical core: R(p+Δ) = R(Δ) ∘ R(p)  -> reuse vLLM rotary kernel",
            ha="center", fontsize=10, weight="bold", color="#222")
    ax.text(7.0, 2.5, "Microbench in fp32: cosine similarity 1.0000000 across all (p,Δ) tested",
            ha="center", fontsize=9, color="#222")
    ax.text(7.0, 1.85, "On bf16 paged KV layout: mean abs error ~3e-3, V untouched",
            ha="center", fontsize=9, color="#222")

    # Smoke result row
    ax.text(7.0, 0.95, "Smoke `bqkcximvt` on Qwen3-30B-A3B / FlashInfer / H100:",
            ha="center", fontsize=9, color="#222", weight="bold")
    ax.text(7.0, 0.45,
            "compactions=3   captures=3   restores=4   rotations=2 (1260 blocks)   pins_applied=0",
            ha="center", fontsize=9, color="#222", family="monospace")

    ax.set_title("Phase 9 mechanism: position-mobile prefix cache via in-place K-vector rotation",
                 fontsize=13, weight="bold", y=0.96)

    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    fig_timeline(FIG_DIR / "fig_review_timeline.png")
    fig_paper2_progress(FIG_DIR / "fig_review_paper2_progress.png")
    fig_phase9_mechanism(FIG_DIR / "fig_review_phase9_mechanism.png")
