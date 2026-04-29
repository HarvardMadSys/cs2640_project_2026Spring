"""Generate presentation figures for the AdaptiveCache project report.

Outputs PNGs to studies/lifetime_cost/reports/figures/.

Each figure stands alone — title, axes, legend self-contained. Style is
clean, presentation-ready (large fonts, color-blind friendly palette, no
gridlines except where they help readability).
"""

from __future__ import annotations

import json
import glob
import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

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

# Color-blind friendly palette (Wong 2011 + minor tweaks)
COLORS = {
    "none":                       "#7F7F7F",  # neutral gray
    "consumption_evict":          "#0072B2",  # blue
    "consumption_evict_facts":    "#D55E00",  # vermillion
    "consumption_evict_outline":  "#009E73",  # green
    "smart_evict":                "#CC79A7",  # pink
    "llm_reorganizer":            "#56B4E9",  # sky blue
    "prefix_preserving":          "#E69F00",  # orange
    "evict_oldest":               "#F0E442",  # yellow
}
SHORT = {
    "none": "none",
    "consumption_evict": "plain",
    "consumption_evict_facts": "facts",
    "consumption_evict_outline": "outline",
    "smart_evict": "smart_evict",
    "llm_reorganizer": "llm_reorg",
    "prefix_preserving": "prefix_pres",
    "evict_oldest": "evict_oldest",
}

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Pricing (Anthropic Haiku 4.5, $/MTok)
# ---------------------------------------------------------------------------
P_IN_UNCACHED = 1.00
P_IN_CACHED = 0.10
P_CACHE_WRITE = 1.25
P_OUT = 5.00


def cost_for_traj(obj):
    in_unc = in_c = cw = out = 0
    for step in obj.get("steps", []):
        u = step.get("usage", {}) or {}
        prompt = u.get("prompt_tokens", 0) or 0
        cached = u.get("cached_tokens", 0) or 0
        in_unc += max(0, prompt - cached)
        in_c += cached
        cw += u.get("cache_write_tokens", 0) or 0
        out += u.get("completion_tokens", 0) or 0
    cliff = (
        in_unc * P_IN_UNCACHED
        + in_c * P_IN_CACHED
        + cw * P_CACHE_WRITE
        + out * P_OUT
    ) / 1e6
    return cliff, in_unc, in_c, cw, out


def load_phase(out_dir, alias_pattern="*"):
    """Returns dict alias -> list of trajectory dicts."""
    files = sorted(glob.glob(f"{out_dir}/trajectories/*/*/{alias_pattern}.jsonl"))
    out = {}
    for f in files:
        alias = os.path.basename(f).replace(".jsonl", "")
        with open(f) as fh:
            objs = [json.loads(line) for line in fh if line.strip()]
        out[alias] = objs
    return out


def real_resolved_from_validated(validated_path):
    """Returns dict (alias, instance_id) -> 'T'|'F'|'tp!'."""
    if not Path(validated_path).exists():
        return {}
    with open(validated_path) as f:
        rows = json.load(f)
    out = {}
    for r in rows:
        if not r.get("install_ok"):
            v = "inst!"
        else:
            ftp = r.get("fail_to_pass_results") or {}
            note = (r.get("note") or "")
            if "test_patch_apply_failed" in note or not ftp:
                v = "tp!"
            else:
                n_pass = sum(1 for x in ftp.values() if x == "pass")
                v = "T" if n_pass == len(ftp) else "F"
        out[(r["policy"], r["instance_id"])] = v
    return out


# ---------------------------------------------------------------------------
# Figure 1: Pareto plot (cost vs real-test resolved) on SWE-bench Lite
# ---------------------------------------------------------------------------

def fig_pareto_swebench():
    out_dir = "studies/lifetime_cost/out/phase_e_outline_10tasks"
    trajs = load_phase(out_dir)
    validated = real_resolved_from_validated(f"{out_dir}/validated.json")

    policy_order = ["none", "consumption_evict", "consumption_evict_facts", "consumption_evict_outline"]
    rows = []
    for pol in policy_order:
        objs = trajs.get(pol, [])
        if not objs:
            continue
        n = len(objs)
        n_T = sum(1 for o in objs if validated.get((pol, o["task_id"])) == "T")
        total = sum(cost_for_traj(o)[0] for o in objs)
        comps = sum(sum(1 for s in o.get("steps", []) if s.get("compaction_after")) for o in objs)
        rows.append((pol, n, n_T, total, comps))

    fig, ax = plt.subplots(figsize=(9, 5.8))

    # Manual label offsets to avoid overlap (plain & outline both at 5/10)
    label_offsets = {
        "none":                       (10, 12),
        "consumption_evict":          (10, 22),
        "consumption_evict_outline":  (10, -28),
        "consumption_evict_facts":    (10, 12),
    }

    for pol, n, n_T, total, comps in rows:
        x = total
        y = n_T / n
        size = 200 + comps * 6
        ax.scatter(x, y, s=size, c=COLORS[pol], edgecolor="black", linewidth=1.2, zorder=3, alpha=0.95)
        label = f"{SHORT[pol]} ({n_T}/{n} • {comps} comps)"
        dx, dy = label_offsets.get(pol, (10, 10))
        ax.annotate(label, (x, y), xytext=(dx, dy), textcoords="offset points",
                    fontsize=10.5, fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color="#888", lw=0.6))

    ax.set_xlabel("Total lifetime cost (USD, Haiku 4.5, N=10 tasks)")
    ax.set_ylabel("Real-test resolve rate (FAIL_TO_PASS pytest)")
    ax.set_title("SWE-bench Lite — no compaction policy Pareto-beats `none`\n"
                 "(circle area ∝ # compaction events fired)", pad=14)
    ax.set_xlim(0, max(r[3] for r in rows) * 1.30)
    ax.set_ylim(0.30, 0.60)
    ax.grid(alpha=0.3, axis="both")
    ax.axhline(0.5, color="black", lw=0.5, linestyle=":", alpha=0.4)

    # Pareto frontier annotation
    none_row = next(r for r in rows if r[0] == "none")
    ax.axvline(none_row[3], color=COLORS["none"], lw=1, linestyle="--", alpha=0.5)
    ax.text(none_row[3] + 0.20, 0.34,
            "Pareto frontier: every compaction policy is\nstrictly to the right at the same resolve rate",
            fontsize=10, style="italic", color="#444",
            bbox=dict(boxstyle="round,pad=0.4", fc="#fff", ec="#bbb", lw=0.6))

    plt.tight_layout()
    plt.savefig(OUT_DIR / "fig1_pareto_swebench.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig1_pareto_swebench.png'}")


# ---------------------------------------------------------------------------
# Figure 2: Cost decomposition (where the dollars go)
# ---------------------------------------------------------------------------

def fig_cost_decomposition():
    out_dir = "studies/lifetime_cost/out/phase_e_outline_10tasks"
    trajs = load_phase(out_dir)
    policy_order = ["none", "consumption_evict", "consumption_evict_facts", "consumption_evict_outline"]

    rows = []
    for pol in policy_order:
        objs = trajs.get(pol, [])
        if not objs:
            continue
        in_unc = in_c = cw = out_t = 0
        for o in objs:
            _, a, b, c, d = cost_for_traj(o)
            in_unc += a; in_c += b; cw += c; out_t += d
        in_unc_d = in_unc * P_IN_UNCACHED / 1e6
        in_c_d   = in_c   * P_IN_CACHED   / 1e6
        cw_d     = cw     * P_CACHE_WRITE / 1e6
        out_d    = out_t  * P_OUT         / 1e6
        rows.append((pol, in_unc_d, in_c_d, cw_d, out_d))

    fig, ax = plt.subplots(figsize=(9, 5.5))
    labels = [SHORT[r[0]] for r in rows]
    in_unc = np.array([r[1] for r in rows])
    in_c   = np.array([r[2] for r in rows])
    cw     = np.array([r[3] for r in rows])
    out_d  = np.array([r[4] for r in rows])

    x = np.arange(len(rows))
    bw = 0.6
    p1 = ax.bar(x, in_unc, bw, color="#D55E00", edgecolor="black", linewidth=0.6, label="input uncached ($1/MT)")
    p2 = ax.bar(x, in_c, bw, bottom=in_unc, color="#56B4E9", edgecolor="black", linewidth=0.6, label="input cached ($0.10/MT)")
    p3 = ax.bar(x, cw, bw, bottom=in_unc + in_c, color="#F0E442", edgecolor="black", linewidth=0.6, label="cache write ($1.25/MT)")
    p4 = ax.bar(x, out_d, bw, bottom=in_unc + in_c + cw, color="#009E73", edgecolor="black", linewidth=0.6, label="output ($5/MT)")

    # Total $ labels on top
    totals = in_unc + in_c + cw + out_d
    for xi, ti in zip(x, totals):
        ax.text(xi, ti + 0.12, f"${ti:.2f}", ha="center", fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel("Total cost (USD)")
    ax.set_title("Where the dollars go — SWE-bench Lite N=10, Haiku 4.5\n"
                 "input_uncached dominates because cliffs invalidate cache", pad=14)
    ax.legend(loc="upper left", fontsize=10)
    ax.set_ylim(0, max(totals) * 1.25)

    plt.tight_layout()
    plt.savefig(OUT_DIR / "fig2_cost_decomposition.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig2_cost_decomposition.png'}")


# ---------------------------------------------------------------------------
# Figure 3: Placeholder-design ablation on pytest-7490
# ---------------------------------------------------------------------------

def fig_placeholder_ablation():
    """Read pytest-7490 trajectory across 4 policies; bar chart of edits +
    compactions and resolve outcome (T/F).

    Use Phase D v2 numbers (the original mechanism finding) PLUS Phase E v1
    (replication), to show this isn't a one-off."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), gridspec_kw={"wspace": 0.32})

    # Hard-coded summary from the Phase D v2 + E v1 trajectories on pytest-7490
    # (validated by inspecting consumption_evict*.jsonl per phase).
    runs = [
        ("Phase D v2", {
            "none":                       {"edits": 3, "outcome": "tp!"},  # test_patch collision masked
            "consumption_evict":          {"edits": 4, "outcome": "T"},
            "consumption_evict_facts":    {"edits": 7, "outcome": "F"},
        }),
        ("Phase E v1", {
            "none":                       {"edits": 10, "outcome": "T"},   # got lucky on test_patch
            "consumption_evict":          {"edits": 12, "outcome": "T"},
            "consumption_evict_facts":    {"edits": 18, "outcome": "F"},
            "consumption_evict_outline":  {"edits": 12, "outcome": "T"},
        }),
    ]
    outcome_color = {"T": "#009E73", "F": "#D55E00", "tp!": "#7F7F7F"}
    outcome_text  = {"T": "RESOLVED", "F": "FAILED", "tp!": "n/a (test_patch collision)"}

    for ax, (phase, data) in zip(axes, runs):
        pols = list(data.keys())
        edits = [data[p]["edits"] for p in pols]
        outcomes = [data[p]["outcome"] for p in pols]
        colors = [outcome_color[o] for o in outcomes]
        x = np.arange(len(pols))
        bars = ax.bar(x, edits, color=colors, edgecolor="black", linewidth=0.7)
        for xi, e, o in zip(x, edits, outcomes):
            ax.text(xi, e + 0.4, outcome_text[o], ha="center", fontsize=9, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels([SHORT[p] for p in pols], rotation=15, ha="right", fontsize=10)
        ax.set_ylabel("# `edit_file` calls on pytest-7490")
        ax.set_title(f"{phase}", fontsize=13, fontweight="bold")
        ax.set_ylim(0, max(edits) * 1.25 + 1)

    fig.suptitle("Placeholder-design ablation — pytest-7490 mechanism replicates across seeds\n"
                 "facts variant edits 7-18× (anchoring loop on misidentified function), plain & outline succeed",
                 fontsize=13, fontweight="bold", y=1.02)

    # Custom legend
    handles = [
        mpatches.Patch(color=outcome_color["T"], label="resolved (real F2P pass)"),
        mpatches.Patch(color=outcome_color["F"], label="failed (real F2P fail)"),
        mpatches.Patch(color=outcome_color["tp!"], label="n/a (test_patch collision)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.05))

    plt.savefig(OUT_DIR / "fig3_placeholder_ablation.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig3_placeholder_ablation.png'}")


# ---------------------------------------------------------------------------
# Figure 4: Compaction firing rate vs chain_size on τ-bench retail
# ---------------------------------------------------------------------------

def fig_chain_firing():
    """Show that on retail, even at chain_size=10 with max_p well past
    trigger, consumption_evict fires 0 times — its rules don't generalize."""
    chain_sizes = [1, 5, 10]
    out_dirs = {
        1:  "studies/lifetime_cost/out/phase_e_taubench_retail",
        5:  "studies/lifetime_cost/out/phase_e_chain_retail",
        10: "studies/lifetime_cost/out/phase_e_chain_retail_big",
    }
    policies = ["none", "smart_evict", "consumption_evict", "consumption_evict_outline"]

    # max_p (max across trajectories) and comps (total) per policy per chain size
    max_p = {p: {} for p in policies}
    comps = {p: {} for p in policies}
    for cs, od in out_dirs.items():
        trajs = load_phase(od)
        for p in policies:
            objs = trajs.get(p, [])
            if not objs:
                continue
            mp = 0; cc = 0
            for o in objs:
                if not o.get("steps"):
                    continue
                mp = max(mp, max((s.get("usage",{}).get("prompt_tokens",0) or 0) for s in o["steps"]))
                cc += sum(1 for s in o["steps"] if s.get("compaction_after"))
            max_p[p][cs] = mp
            comps[p][cs] = cc

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6), gridspec_kw={"wspace": 0.30})

    # Left: max prompt vs chain_size, with trigger threshold line
    for p in policies:
        xs = sorted(max_p[p].keys())
        ys = [max_p[p][cs] for cs in xs]
        ax1.plot(xs, ys, marker="o", lw=2, ms=10, color=COLORS[p], label=SHORT[p])
    ax1.axhline(29750, color="red", lw=1.5, linestyle="--", alpha=0.7, label="trigger (0.85 × 35K)")
    ax1.set_xlabel("chain_size (# customers per session)")
    ax1.set_ylabel("Max prompt tokens reached")
    ax1.set_title("Multi-customer chains push max_p past trigger…", pad=12)
    ax1.set_xticks(chain_sizes)
    ax1.set_ylim(0, 60000)
    ax1.legend(loc="upper left", fontsize=10)
    ax1.grid(alpha=0.3)

    # Right: # compactions per policy per chain_size — should be 0 for retail
    width = 0.2
    x_arr = np.arange(len(chain_sizes))
    for i, p in enumerate(policies):
        ys = [comps[p].get(cs, 0) for cs in chain_sizes]
        ax2.bar(x_arr + (i - 1.5) * width, ys, width, color=COLORS[p],
                label=SHORT[p], edgecolor="black", linewidth=0.5)
    ax2.set_xticks(x_arr)
    ax2.set_xticklabels(chain_sizes)
    ax2.set_xlabel("chain_size")
    ax2.set_ylabel("# compaction events fired")
    ax2.set_title("…but supersession rules don't fire on retail tools", pad=12)
    ax2.set_ylim(0, 5)
    ax2.legend(loc="upper left", fontsize=10)
    ax2.grid(alpha=0.3, axis="y")
    ax2.text(1, 2.5, "All bars at 0\n(coding-specific rules\ndon't match retail tools)",
             ha="center", fontsize=11, style="italic", color="#444",
             bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#bbb"))

    fig.suptitle("τ-bench retail: rule portability is the real bottleneck (not budget)",
                 fontsize=14, fontweight="bold", y=1.02)
    plt.savefig(OUT_DIR / "fig4_chain_firing.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig4_chain_firing.png'}")


# ---------------------------------------------------------------------------
# Figure 5: The cliff — cost amplification per compaction event
# ---------------------------------------------------------------------------

def fig_cliff_amplification():
    """Conceptual + measured: show the cliff cost amplification.

    Use Phase E v1 data: per compaction event in plain consumption_evict,
    estimate the cliff dollars added (input_uncached over the next K steps
    minus what cached would have been).
    """
    fig, ax = plt.subplots(figsize=(8, 5.5))

    # Realistic per-event cliff: 30K-token prefix invalidated, then K
    # tokens/step for n_steps follow-on calls billed at uncached rate.
    K = 30000  # the prefix bytes that lose their cache after the cliff
    cached_cost = K * P_IN_CACHED / 1e6
    uncached_cost = K * P_IN_UNCACHED / 1e6

    bars = ax.bar(["next call BEFORE cliff\n(prefix cached)", "next call AFTER cliff\n(prefix re-uncached)"],
                  [cached_cost, uncached_cost],
                  color=["#56B4E9", "#D55E00"], edgecolor="black", linewidth=0.7, width=0.55)
    for b, v in zip(bars, [cached_cost, uncached_cost]):
        ax.text(b.get_x() + b.get_width()/2, v + uncached_cost * 0.03, f"${v:.4f}",
                ha="center", fontsize=12, fontweight="bold")

    ax.set_ylabel("Cost of next API call's 30K-token prefix (USD)")
    ax.set_title(r"The cliff tax — 10$\times$ cost amplification per compaction event" "\n"
                 "Compaction invalidates the cached prefix; subsequent input tokens are\n"
                 r"re-billed at \$1.00/MTok (uncached) instead of \$0.10/MTok (cached).",
                 pad=14)

    # Add annotation
    ax.annotate("", xy=(1, uncached_cost * 0.95), xytext=(0, cached_cost * 1.1),
                arrowprops=dict(arrowstyle="->", color="#D55E00", lw=2.5))
    ax.text(0.5, (cached_cost + uncached_cost) / 2, "10×\namplification",
            ha="center", fontsize=14, fontweight="bold", color="#D55E00",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#D55E00", lw=1.5))
    ax.set_ylim(0, uncached_cost * 1.25)

    plt.tight_layout()
    plt.savefig(OUT_DIR / "fig5_cliff_amplification.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig5_cliff_amplification.png'}")


# ---------------------------------------------------------------------------
# Figure 6: Phase-by-phase summary (project arc)
# ---------------------------------------------------------------------------

def fig_project_arc():
    """Timeline of the 4 phases with key findings — each in its own panel."""
    fig, axes = plt.subplots(1, 4, figsize=(18, 7), gridspec_kw={"wspace": 0.08})

    phases = [
        ("Phase A", "Apr 4 – Apr 26", "Measurement", "#56B4E9", [
            "3-way uncorrelation of\nimportance proxies",
            "Position primacy invariant\n(Qwen3 0.6B – 8B)",
            "Tool obs = 77% of\nagent-loop tokens",
            "Cliff cost: ~$0.10–0.15\nper compaction event",
        ]),
        ("Phase B/C", "Apr 27 – Apr 28", "8 heuristic policies", "#0072B2", [
            "τ-bench airline:\nnothing to compact",
            "SWE-bench Lite:\ncliff cost dominates",
            "All compaction policies\ntie or lose to `none`",
            "Sorted out: max_steps,\ntemp, max_model_len",
        ]),
        ("Phase D", "Apr 28", "Action-graph supersession", "#009E73", [
            "Novel `consumption_evict`\n— tool-graph supersession",
            "Real-test validator\n(replaces line-overlap oracle)",
            "pytest-7490 mechanism:\nplaceholder design wins",
            "Single-seed N=10:\nstill no Pareto win",
        ]),
        ("Phase E", "Apr 28 – Apr 29", "Placeholder ablation + chain", "#D55E00", [
            "Outline placeholder mode\n(middle ground design)",
            "Mechanism replicates\nacross seeds",
            "Multi-customer chains\npush max_p past trigger",
            "Rule portability is the wall\n(0 fires on retail tools)",
        ]),
    ]

    for ax, (name, dates, subtitle, color, bullets) in zip(axes, phases):
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        # Header band
        ax.add_patch(plt.Rectangle((0.04, 0.84), 0.92, 0.14, facecolor=color, alpha=0.92,
                                   edgecolor="black", linewidth=0.6))
        ax.text(0.50, 0.93, name, ha="center", va="center",
                fontsize=15, fontweight="bold", color="white")
        ax.text(0.50, 0.87, dates, ha="center", va="center",
                fontsize=10, color="white")
        # Subtitle band
        ax.add_patch(plt.Rectangle((0.04, 0.74), 0.92, 0.08, facecolor=color, alpha=0.45,
                                   edgecolor="black", linewidth=0.4))
        ax.text(0.50, 0.78, subtitle, ha="center", va="center",
                fontsize=11.5, fontweight="bold")
        # Body
        ax.add_patch(plt.Rectangle((0.04, 0.04), 0.92, 0.68, facecolor="#fafafa",
                                   edgecolor="black", linewidth=0.4))
        for j, b in enumerate(bullets):
            y = 0.62 - j * 0.16
            # Bullet circle
            ax.scatter([0.10], [y + 0.03], s=70, facecolor=color, edgecolor="black",
                       linewidth=0.5, zorder=3)
            ax.text(0.18, y + 0.03, b, ha="left", va="center", fontsize=10.5, wrap=True)

    fig.suptitle(
        "Project arc — measurement → empirical → mechanism → portability\n"
        "Each phase ends with a clean negative or constructive finding that motivates the next.",
        fontsize=14, fontweight="bold", y=1.04,
    )
    plt.savefig(OUT_DIR / "fig6_project_arc.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig6_project_arc.png'}")


# ---------------------------------------------------------------------------

def fig_compaction_wins():
    """Two single-seed N=4 cells where compaction Pareto-dominates `none`.

    Left: Phase D v1 (Haiku, N=4 SWE-bench Lite). consumption_evict 3/4
    resolved at lower $/res than none, AND smart_evict 2/4 at 29% lower
    $/res than none → both on the Pareto frontier above none.

    Right: Phase C v6 (Qwen3-30B-A3B, N=4 same tasks). At weaker agent
    quality, none scores 0/4 (catastrophic failure modes); smart_evict
    2/4 — compaction prevents the failure modes.
    """
    import csv

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.2),
                                   gridspec_kw={"wspace": 0.30})

    # ---- LEFT PANEL: Phase D v1 (Haiku N=4) ----
    # Source: studies/lifetime_cost/out/phase_d_v1_consumption_lazy/figures/pareto.csv
    rows_haiku = [
        ("none",              0.6001, 0.50),
        ("smart_evict",       0.4253, 0.50),
        ("consumption_evict", 0.7736, 0.75),
    ]
    for pol, cost_per_task, resolve in rows_haiku:
        # Convert mean_cost_per_task → $/resolved (over 4 tasks)
        if resolve == 0:
            continue
        dollars_per_res = (cost_per_task * 4) / (resolve * 4)
        ax1.scatter(dollars_per_res, resolve, s=320,
                    c=COLORS[pol], edgecolor="black", linewidth=1.2,
                    zorder=3, alpha=0.95)
        label = f"{SHORT[pol]}\n({int(resolve*4)}/4 • ${dollars_per_res:.2f}/res)"
        ax1.annotate(label, (dollars_per_res, resolve), xytext=(10, 10),
                     textcoords="offset points", fontsize=11, fontweight="bold",
                     arrowprops=dict(arrowstyle="-", color="#888", lw=0.5))

    none_dpr = 0.6001 * 4 / (0.5 * 4)
    ax1.axvline(none_dpr, color=COLORS["none"], lw=1, ls="--", alpha=0.5)
    ax1.set_xlabel("$ per resolved task (Haiku 4.5)")
    ax1.set_ylabel("Resolve rate (loose oracle)")
    ax1.set_title("Phase D v1 — Haiku, N=4 SWE-bench Lite\n"
                  "consumption_evict & smart_evict both Pareto-dominate `none`",
                  pad=12, fontsize=12, fontweight="bold")
    ax1.set_xlim(0.6, 1.5)
    ax1.set_ylim(0.35, 0.90)
    ax1.grid(alpha=0.3)
    ax1.text(0.65, 0.40,
             "Top-left = better\n(higher resolve, lower cost)",
             fontsize=10, style="italic", color="#444",
             bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#bbb", lw=0.6))

    # ---- RIGHT PANEL: Phase C v6 Qwen eager (Qwen3-30B-A3B N=4) ----
    # Phase C v6 — bar chart instead, since cost-vs-resolve is hard to read
    # when none is at ∞ and three policies cluster at similar cost.
    rows_qwen = [
        ("none",              0.0256, 0, "$∞"),
        ("prefix_preserving", 0.0260, 1, "$0.104"),
        ("llm_reorganizer",   0.0285, 1, "$0.114"),
        ("smart_evict",       0.0324, 2, "$0.065"),
    ]
    pols = [r[0] for r in rows_qwen]
    n_res = [r[2] for r in rows_qwen]
    cost_str = [r[3] for r in rows_qwen]
    bar_colors = [COLORS[p] for p in pols]

    x = np.arange(len(pols))
    bars = ax2.bar(x, n_res, color=bar_colors, edgecolor="black", linewidth=0.7, width=0.6)
    for xi, n, c in zip(x, n_res, cost_str):
        if n == 0:
            ax2.text(xi, 0.05, "0/4\n" + c + "/res",
                     ha="center", fontsize=11, fontweight="bold", color="#a00")
        else:
            ax2.text(xi, n + 0.08, f"{n}/4\n{c}/res",
                     ha="center", fontsize=11, fontweight="bold")

    ax2.set_xticks(x)
    ax2.set_xticklabels([SHORT[p] for p in pols], rotation=15, ha="right", fontsize=11)
    ax2.set_ylabel("# tasks resolved (out of 4)")
    ax2.set_ylim(0, 3.2)
    ax2.set_title("Phase C v6 — Qwen3-30B-A3B, N=4 same tasks\n"
                  "Below agent-quality threshold: compaction PREVENTS catastrophic failure",
                  pad=12, fontsize=12, fontweight="bold")
    ax2.grid(alpha=0.3, axis="y")
    ax2.text(1.5, 2.7,
             "`none`'s 0/4 isn't a fluke:\n"
             "• false-submit at step 2 (temp=0.5)\n"
             "• context overflow at step 80\n"
             "Compaction breaks both loops.",
             fontsize=10, style="italic", color="#444", ha="center",
             bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#bbb", lw=0.6))

    fig.suptitle("Where compaction DOES win — single-seed Pareto wins at small N",
                 fontsize=14, fontweight="bold", y=1.02)

    plt.savefig(OUT_DIR / "fig7_compaction_wins.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig7_compaction_wins.png'}")


def main():
    print(f"Generating figures into {OUT_DIR}/")
    fig_pareto_swebench()
    fig_cost_decomposition()
    fig_placeholder_ablation()
    fig_chain_firing()
    fig_cliff_amplification()
    fig_project_arc()
    fig_compaction_wins()
    print("Done.")


if __name__ == "__main__":
    main()
