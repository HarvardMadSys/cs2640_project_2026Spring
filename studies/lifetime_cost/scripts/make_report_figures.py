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
    """Conceptual + measured: cliff cost amplification with worked example.

    Left panel: bar chart of next-call cost before vs. after cliff.
    Right panel: worked example for a typical compaction event.
    """
    fig, (ax, axE) = plt.subplots(1, 2, figsize=(13, 5.5),
                                  gridspec_kw={"width_ratios": [1.0, 1.05]})

    # Left panel: 25K-token suffix invalidated by a typical mid-trajectory fire
    K = 25000  # bytes from compaction position to prompt tail
    cached_cost   = K * P_IN_CACHED   / 1e6
    uncached_cost = K * P_IN_UNCACHED / 1e6
    delta         = uncached_cost - cached_cost
    save_per_step = 5000 * P_IN_CACHED / 1e6   # 5K-token obs replaced by 50-tok placeholder
    breakeven     = delta / save_per_step

    bars = ax.bar(["next call BEFORE cliff\n(prefix cached)",
                   "next call AFTER cliff\n(prefix re-uncached)"],
                  [cached_cost, uncached_cost],
                  color=["#56B4E9", "#D55E00"], edgecolor="black",
                  linewidth=0.7, width=0.55)
    for b, v in zip(bars, [cached_cost, uncached_cost]):
        ax.text(b.get_x() + b.get_width()/2, v + uncached_cost * 0.03,
                f"${v:.4f}", ha="center", fontsize=12, fontweight="bold")

    ax.set_ylabel("Cost of next API call's 25K-token suffix (USD)")
    ax.set_title(r"The cliff tax — 10$\times$ amplification" "\n"
                 r"\$1.00/MTok (uncached) vs \$0.10/MTok (cached)",
                 pad=10, fontsize=11.5)

    ax.annotate("", xy=(1, uncached_cost * 0.95),
                xytext=(0, cached_cost * 1.1),
                arrowprops=dict(arrowstyle="->", color="#D55E00", lw=2.5))
    ax.text(0.5, (cached_cost + uncached_cost) / 2, "10×\namplification",
            ha="center", fontsize=14, fontweight="bold", color="#D55E00",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#D55E00", lw=1.5))
    ax.set_ylim(0, uncached_cost * 1.25)

    # Right panel: worked example
    axE.set_axis_off()
    axE.set_xlim(0, 10)
    axE.set_ylim(0, 10)
    axE.add_patch(plt.Rectangle((0.2, 0.2), 9.6, 9.6, fill=True,
                                facecolor="#FFF8E7", edgecolor="#D55E00",
                                linewidth=1.5))
    axE.text(5.0, 9.3, "Worked example: one compaction event",
             ha="center", fontsize=12, fontweight="bold", color="#222")
    axE.text(5.0, 8.7, "(Anthropic Haiku 4.5 pricing, mid-trajectory fire)",
             ha="center", fontsize=9.5, color="#444", style="italic")

    rows = [
        ("Setup",                                    ""),
        ("• Prompt size at fire time",               "30,000 tokens"),
        ("• Tool obs being compacted",               "5,000 tokens at byte position 5,000"),
        ("• Placeholder it leaves behind",           "~50 tokens"),
        ("• Suffix re-billed (positions 5K → 30K)",  "25,000 tokens"),
        ("",                                          ""),
        ("Next-call cost",                           ""),
        ("• Without cliff (cached)",                 f"25,000 × $0.10/MTok = ${cached_cost:.4f}"),
        ("• With cliff (uncached)",                  f"25,000 × $1.00/MTok = ${uncached_cost:.4f}"),
        ("• Δ extra cost on next call",              f"${delta:.4f}"),
        ("",                                          ""),
        ("Future-step savings (per step)",           ""),
        ("• 5K fewer cached tokens × $0.10/MTok",    f"${save_per_step:.4f}/step"),
        ("",                                          ""),
        ("Break-even",                               ""),
        (f"  ${delta:.4f} ÷ ${save_per_step:.4f}/step",
                                                     f"≈ {breakeven:.0f} steps required"),
    ]
    y = 8.2
    for label, value in rows:
        if label == "":
            y -= 0.18
            continue
        if value == "" and not label.startswith("•") and not label.startswith(" "):
            axE.text(0.55, y, label, fontsize=10, fontweight="bold", color="#222")
        else:
            axE.text(0.55, y, label, fontsize=9.5, color="#222")
            if value:
                axE.text(9.45, y, value, fontsize=9.5, color="#222",
                         ha="right", family="monospace")
        y -= 0.42

    axE.add_patch(plt.Rectangle((0.4, 0.7), 9.2, 0.95, fill=True,
                                facecolor="#FFE3D1", edgecolor="#D55E00",
                                linewidth=1.0))
    axE.text(5.0, 1.35, "Typical agent trajectory length: ~26 steps total.",
             ha="center", fontsize=10, color="#222")
    axE.text(5.0, 0.95,
             f"Compaction needs {breakeven:.0f}+ remaining steps to amortize → cliff is structurally unrecoverable.",
             ha="center", fontsize=10, color="#D55E00", fontweight="bold")

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


def fig_vision():
    """The AdaptiveCache vision figure.

    Two horizontal strips:
      Top  — current layout (items in arrival order, all uncached after the system prompt)
      Bottom — after AdaptiveCache layout pass (stable/important promoted to
               the cached prefix zone; exhausted suffix items become holes)

    Annotations: cached prefix zone (frozen, reused step-to-step) vs.
    volatile suffix zone (where eviction happens for free).
    """
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 110)
    ax.axis("off")

    # Color palette by item type
    C_SYS    = "#3B5BA9"  # system prompt — always cached
    C_STABLE = "#0072B2"  # stable / important (function defs, problem statement)
    C_KEEP   = "#009E73"  # recent / source-of-truth tool obs
    C_USED   = "#E69F00"  # consumed / exhaust (will be evicted)
    C_HOLE   = "#D9D9D9"  # hole left by eviction
    C_NEW    = "#CC79A7"  # newly arrived

    # ---------- TOP STRIP: BEFORE ----------
    y0 = 78
    h  = 12

    items_before = [
        ("system\nprompt",         8,  C_SYS,    "cached"),
        ("file A\nread",           7,  C_USED,   "exhaust"),
        ("search\nresults",        6,  C_USED,   "exhaust"),
        ("important\nfunction",    7,  C_STABLE, "stable"),
        ("file B\nedited",         6,  C_USED,   "exhaust"),
        ("test run\noutput",       6,  C_USED,   "exhaust"),
        ("important\nstacktrace",  6,  C_STABLE, "stable"),
        ("redundant\nsearch",      5,  C_USED,   "exhaust"),
        ("recent\nfile read",      6,  C_KEEP,   "recent"),
        ("recent\ndialogue",       7,  C_KEEP,   "recent"),
    ]

    x = 5
    centers_before = {}
    for label, w, color, _kind in items_before:
        rect = plt.Rectangle((x, y0), w, h, facecolor=color, edgecolor="black", linewidth=0.7)
        ax.add_patch(rect)
        ax.text(x + w / 2, y0 + h / 2, label, ha="center", va="center",
                fontsize=8.5, fontweight="bold",
                color=("white" if color in (C_SYS, C_STABLE, C_USED) else "black"))
        centers_before[label] = (x + w / 2, y0)
        x += w + 0.6

    ax.text(50, y0 + h + 3,
            "BEFORE — items in arrival order; only the system prompt is cached across steps",
            ha="center", fontsize=12, fontweight="bold")

    # Cache zone bracket — only the system prompt
    sys_w = items_before[0][1]
    ax.annotate("", xy=(5, y0 - 1.5), xytext=(5 + sys_w + 0.6, y0 - 1.5),
                arrowprops=dict(arrowstyle="-", color="#3B5BA9", lw=2.5))
    ax.text(5 + (sys_w + 0.6) / 2, y0 - 5, "cached", ha="center", fontsize=10,
            fontweight="bold", color="#3B5BA9")

    ax.annotate("", xy=(5 + sys_w + 0.6, y0 - 1.5), xytext=(x, y0 - 1.5),
                arrowprops=dict(arrowstyle="-", color="#888", lw=2.5))
    ax.text((5 + sys_w + 0.6 + x) / 2, y0 - 5,
            "uncached every step (re-billed at 10× rate)",
            ha="center", fontsize=10, fontweight="bold", color="#888")

    # ---------- BOTTOM STRIP: AFTER ----------
    y1 = 35

    items_after = [
        ("system\nprompt",         8,  C_SYS,    "cached"),
        ("important\nfunction",    7,  C_STABLE, "promoted"),  # was index 3
        ("important\nstacktrace",  6,  C_STABLE, "promoted"),  # was index 6
        ("recent\nfile read",      6,  C_KEEP,   "recent"),
        ("recent\ndialogue",       7,  C_KEEP,   "recent"),
        ("hole",                   4,  C_HOLE,   "hole"),
        ("hole",                   4,  C_HOLE,   "hole"),
        ("hole",                   3,  C_HOLE,   "hole"),
        ("new\nfile read",         6,  C_NEW,    "new"),
    ]

    x = 5
    centers_after = {}
    for label, w, color, kind in items_after:
        rect = plt.Rectangle((x, y1), w, h, facecolor=color,
                             edgecolor="black", linewidth=0.7,
                             alpha=(0.4 if kind == "hole" else 1.0),
                             linestyle=("dashed" if kind == "hole" else "solid"))
        ax.add_patch(rect)
        if kind == "hole":
            ax.text(x + w / 2, y1 + h / 2, "(evicted)",
                    ha="center", va="center", fontsize=8, style="italic", color="#666")
        else:
            ax.text(x + w / 2, y1 + h / 2, label, ha="center", va="center",
                    fontsize=8.5, fontweight="bold",
                    color=("white" if color in (C_SYS, C_STABLE, C_USED) else "black"))
        centers_after[label + str(x)] = (x + w / 2, y1 + h)
        x += w + 0.6

    ax.text(50, y1 + h + 3,
            "AFTER — AdaptiveCache layout pass: stable items pinned to the cached prefix; "
            "exhausted items become holes",
            ha="center", fontsize=12, fontweight="bold")

    # Cache zone bracket — now extends across the pinned stable items
    pinned_x_start = 5
    pinned_x_end = 5 + 8 + 0.6 + 7 + 0.6 + 6 + 0.6
    ax.annotate("", xy=(pinned_x_start, y1 - 1.5), xytext=(pinned_x_end, y1 - 1.5),
                arrowprops=dict(arrowstyle="-", color="#3B5BA9", lw=2.5))
    ax.text((pinned_x_start + pinned_x_end) / 2, y1 - 5,
            "PINNED PREFIX — cached, reused next step",
            ha="center", fontsize=10, fontweight="bold", color="#3B5BA9")

    volatile_x_start = pinned_x_end
    ax.annotate("", xy=(volatile_x_start, y1 - 1.5), xytext=(x, y1 - 1.5),
                arrowprops=dict(arrowstyle="-", color="#888", lw=2.5))
    ax.text((volatile_x_start + x) / 2, y1 - 5,
            "VOLATILE SUFFIX — eviction happens here for free",
            ha="center", fontsize=10, fontweight="bold", color="#888")

    # Arrows: promoting important items from before-positions to after-positions
    promotions = [
        ("important\nfunction", "important\nfunction"),
        ("important\nstacktrace", "important\nstacktrace"),
    ]
    for src_label, dst_label in promotions:
        src = centers_before[src_label]
        dst_key = next(k for k in centers_after if k.startswith(dst_label))
        dst = centers_after[dst_key]
        ax.annotate("", xy=(dst[0], dst[1] + 1), xytext=(src[0], src[1] - 0.5),
                    arrowprops=dict(arrowstyle="->", color="#0072B2", lw=2,
                                    connectionstyle="arc3,rad=-0.15"))

    # Eviction arrows: exhausted items → holes
    evicted_sources = ["file A\nread", "search\nresults", "redundant\nsearch", "test run\noutput", "file B\nedited"]
    hole_xs = [78, 84.5, 89.5]
    for i, src_label in enumerate(evicted_sources[:3]):
        src = centers_before[src_label]
        ax.annotate("", xy=(hole_xs[i], y1 + h + 1), xytext=(src[0], src[1] - 0.5),
                    arrowprops=dict(arrowstyle="->", color="#E69F00", lw=1.4,
                                    alpha=0.7, connectionstyle="arc3,rad=0.10"))

    # Legend
    legend_handles = [
        mpatches.Patch(facecolor=C_SYS, edgecolor="black", label="System prompt (always cached)"),
        mpatches.Patch(facecolor=C_STABLE, edgecolor="black", label="Stable / high-importance (PROMOTED to prefix)"),
        mpatches.Patch(facecolor=C_KEEP, edgecolor="black", label="Recent / source-of-truth (kept in suffix)"),
        mpatches.Patch(facecolor=C_USED, edgecolor="black", label="Consumed / exhaust (EVICTED)"),
        mpatches.Patch(facecolor=C_HOLE, edgecolor="black", label="Hole (no recompute, no new tokens)"),
        mpatches.Patch(facecolor=C_NEW, edgecolor="black", label="New observation (just arrived)"),
    ]
    ax.legend(handles=legend_handles, loc="upper center", bbox_to_anchor=(0.5, 0.02),
              ncol=3, fontsize=10, frameon=False)

    # Title
    ax.text(50, 107,
            "AdaptiveCache vision — context as a live, ordered memory",
            ha="center", fontsize=15, fontweight="bold")
    ax.text(50, 102,
            "Reorganize the prompt on compaction: pin stable items at the front so they stay cached;\n"
            "leave holes in the suffix where exhausted observations used to be (free, no recompute).",
            ha="center", fontsize=11, color="#444")

    plt.savefig(OUT_DIR / "fig8_vision.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig8_vision.png'}")


# ---------------------------------------------------------------------------
# Per-method figures: one panel per compaction policy with before/after strip
# ---------------------------------------------------------------------------

def _draw_strip(ax, y, h, items, alphas=None, dashes=None, label_color_overrides=None):
    """Draw a horizontal strip of context items at row y (item width per item)."""
    x = 5
    centers = []
    alphas = alphas or [1.0] * len(items)
    dashes = dashes or [False] * len(items)
    label_color_overrides = label_color_overrides or {}
    for (label, w, color), alpha, dashed in zip(items, alphas, dashes):
        rect = plt.Rectangle((x, y), w, h, facecolor=color,
                             edgecolor="black", linewidth=0.6,
                             alpha=alpha, linestyle=("dashed" if dashed else "solid"))
        ax.add_patch(rect)
        text_color = label_color_overrides.get(label,
                        "white" if color in ("#3B5BA9", "#0072B2", "#E69F00") else "black")
        if alpha < 0.6:
            text_color = "#666"
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=8, fontweight="bold", color=text_color, style=("italic" if alpha < 0.6 else "normal"))
        centers.append((x + w / 2, y))
        x += w + 0.6
    return x, centers


def fig_methods_panels():
    """One small figure per compaction method, with title + tagline + before/after strip.

    Each output is a slide-ready ~1500x600 PNG.
    """
    C_SYS    = "#3B5BA9"
    C_STABLE = "#0072B2"
    C_KEEP   = "#009E73"
    C_USED   = "#E69F00"
    C_HOLE   = "#D9D9D9"
    C_NEW    = "#CC79A7"
    C_SUMM   = "#9D7CD8"  # purple — summarized block

    # The "before" template — wider boxes so labels fit comfortably.
    BEFORE = [
        ("system",      9, C_SYS),
        ("read A",      9, C_USED),
        ("search 1",    9, C_USED),
        ("def f()",     9, C_STABLE),
        ("edit B",      9, C_USED),
        ("tests",       9, C_USED),
        ("trace",       9, C_STABLE),
        ("search 2",    9, C_USED),
        ("recent file", 9, C_KEEP),
        ("dialogue",    9, C_KEEP),
    ]

    # AFTER lists are length-10 lists (same as BEFORE); each entry is either
    # a kept BEFORE[i] item or a replacement (hole / summary block). Widths
    # mirror BEFORE positionally so the strips line up visually.
    def keep(idx):
        return BEFORE[idx]
    def hole(idx, label="(evicted)"):
        return (label, BEFORE[idx][1], C_HOLE)
    def summ(idx, label):
        return (label, BEFORE[idx][1], C_SUMM)

    none_after        = [keep(i) for i in range(10)]
    naive_after          = [keep(0)] + [summ(i, "[ summary ]") for i in range(1, 9)] + [keep(9)]
    micro_after          = [keep(0), summ(1, "[micro]"), keep(2), keep(3), keep(4),
                            keep(5), keep(6), keep(7), keep(8), keep(9)]
    prefix_after         = [keep(0), keep(1), keep(2)] + [summ(i, "[ summary ]") for i in range(3, 8)] + [keep(8), keep(9)]
    # position_aware reorders — pin important content right after system
    position_after       = [keep(0), keep(3), keep(6), hole(3), hole(4), hole(5), hole(7), keep(2), keep(8), keep(9)]
    evict_oldest_after   = [keep(0), hole(1), hole(2), hole(3), keep(4), keep(5), keep(6), keep(7), keep(8), keep(9)]
    smart_after          = [keep(0), keep(1), hole(2), keep(3), keep(4), hole(5), keep(6), hole(7), keep(8), keep(9)]
    score_periodic_after = [keep(0), hole(1), keep(2), hole(3), hole(4), keep(5), hole(6), hole(7), keep(8), keep(9)]
    llm_reorg_after      = [keep(0), keep(3), hole(2), hole(4), keep(6), hole(5), hole(7), keep(1), keep(8), keep(9)]
    cons_plain_after     = [keep(0), hole(1, "[evicted]"), hole(2, "[evicted]"), keep(3), keep(4),
                            hole(5, "[evicted]"), keep(6), hole(7, "[evicted]"), keep(8), keep(9)]
    cons_facts_after     = [keep(0), hole(1, "[+ fact]"), hole(2, "[+ fact]"), keep(3), keep(4),
                            hole(5, "[+ fact]"), keep(6), hole(7, "[+ fact]"), keep(8), keep(9)]
    cons_outline_after   = [keep(0), hole(1, "[+ outline]"), hole(2, "[+ outline]"), keep(3), keep(4),
                            hole(5, "[+ outline]"), keep(6), hole(7, "[+ outline]"), keep(8), keep(9)]

    methods = [
        ("01_none",
         "Baseline: `none` (no compaction)",
         "Context grows step by step. Cached prefix never extends past the system prompt.",
         none_after,
         "Reference cost. Loses on very long sessions because everything past the system prompt re-bills uncached on every new tool call. WINS at our N=10 SWE-bench scale because compaction overhead exceeds savings."),
        ("02_naive_summary",
         "naive_summary",
         "When budget hits, replace EVERYTHING in the middle with one LLM-written summary.",
         naive_after,
         "Smallest result, biggest cliff. Every fire writes brand-new summary tokens that nothing has cached. Loses on cost. Classic baseline."),
        ("03_microcompact",
         "microcompact (Claude-Code style)",
         "Find the BIGGEST tool obs and replace IT alone with a one-line summary. Don't reorder.",
         micro_after,
         "Cheapest cliff — only one item changes. Only kicks in when there's an outlier-large message. Doesn't help when context is many medium-size obs."),
        ("04_prefix_preserving",
         "prefix_preserving",
         "Keep first K turns frozen + an LLM summary of the middle + recent K turns. Classic.",
         prefix_after,
         "Stable first-K cached. But every fire writes new summary tokens that re-bill everything after the summary at uncached rate. Loses on cost."),
        ("05_position_aware",
         "position_aware (proto-AdaptiveCache)",
         "Pin attention-heavy items right after the system prompt. Hole-leave the rest of the middle.",
         position_after,
         "First step toward the AdaptiveCache vision: REORDERS the prompt to pin important content. Hole-leaving (no recompute) for evicted items. Picks 'important' from position prior — sometimes wrong."),
        ("06_evict_oldest",
         "evict_oldest (FIFO)",
         "Drop the oldest tool obs first. Leave a hole. Keep arrival order.",
         evict_oldest_after,
         "Simplest possible policy. Often drops what the agent ALREADY USED — that's good. But also drops irreplaceable old context. Coarse signal."),
        ("07_smart_evict",
         "smart_evict",
         "Score each obs by type prior + textual reference count. Drop the lowest scored.",
         smart_after,
         "No LLM call. Cheap heuristic scoring. Protects function-def-shaped content. Still cliff-bound on cost. WINS on weak Qwen3 (prevents catastrophic failures)."),
        ("08_score_periodic",
         "score_periodic",
         "Every N steps, run an LLM scorer over all messages. Drop the bottom-K.",
         score_periodic_after,
         "LLM scorer adds quality signal. But the scorer call itself costs tokens, and it fires periodically rather than only when needed."),
        ("09_llm_reorganizer",
         "llm_reorganizer",
         "Small LLM scores every tool obs 1–10. Reorders + drops the bottom-K.",
         llm_reorg_after,
         "Could in principle reorder + drop. In practice the scorer's $/MTok overhead exceeds the bytes saved on most tasks."),
        ("10_consumption_evict_plain",
         "consumption_evict (plain) — OUR NOVEL POLICY",
         "Drop only what the agent's OWN later actions made stale. Leave a minimal placeholder.",
         cons_plain_after,
         "No LLM, no attention. Uses agent tool-call semantics: read_file consumed by edit_file; run_tests cascade; search consumed by following the lead. WINS at small N. Replicates the placeholder mechanism finding."),
        ("11_consumption_evict_facts",
         "consumption_evict_facts — losing variant",
         "Same eviction signal, but leave a SUMMARY (function defs) in the placeholder.",
         cons_facts_after,
         "Counter-intuitive: anchors agent on surface details (function names) → wrong-region edit-revert loops. LOSES pytest-7490 with 7–18× edits. The mechanism finding."),
        ("12_consumption_evict_outline",
         "consumption_evict_outline — middle-ground",
         "Same eviction signal, leave a structural OUTLINE + 're-read to act' instruction.",
         cons_outline_after,
         "Location breadcrumbs (line numbers) + explicit re-read prompt. Ties plain on resolve. Cleanest design point in the family."),
    ]

    for fname, title, tagline, after, footer in methods:
        fig, ax = plt.subplots(figsize=(14, 5.5))
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.axis("off")

        # Title
        ax.text(50, 92, title, ha="center", fontsize=15, fontweight="bold")
        ax.text(50, 86, tagline, ha="center", fontsize=11, color="#333", style="italic")

        # BEFORE strip
        ax.text(5, 75, "before:", ha="left", fontsize=10, color="#666", fontweight="bold")
        _draw_strip(ax, 60, 12, BEFORE)

        # AFTER strip — alphas/dashes derived from item color (holes & summaries)
        alphas = [0.5 if it[2] == C_HOLE else 1.0 for it in after]
        dashes = [it[2] == C_HOLE for it in after]
        ax.text(5, 47, "after:", ha="left", fontsize=10, color="#666", fontweight="bold")
        _draw_strip(ax, 32, 12, after, alphas=alphas, dashes=dashes)

        # Footer text
        ax.text(50, 12, footer, ha="center", fontsize=10.5, color="#222",
                wrap=True,
                bbox=dict(boxstyle="round,pad=0.6", fc="#f5f5f5", ec="#ccc", lw=0.6))

        plt.savefig(OUT_DIR / f"method_{fname}.png", bbox_inches="tight")
        plt.close()
        print(f"  wrote {OUT_DIR / f'method_{fname}.png'}")


def fig_method_family_tree():
    """A single 'design space' figure that groups the 12 methods into 5 families.

    Goal: lets the audience see that 'compaction' is a 5-branch design tree, not
    a single technique. Colors encode the family. Branch label = the *signal*
    each family uses to decide what to drop. Leaf style encodes structural
    transform (replace-in-place / hole-leave / reorder).
    """
    fig, ax = plt.subplots(figsize=(18, 10))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # Family palette (color-blind friendly)
    FAM = {
        "noop":       "#7F7F7F",
        "summarize":  "#9D7CD8",
        "heuristic":  "#E69F00",
        "reorder":    "#56B4E9",
        "llm_score":  "#CC79A7",
        "actiongraph":"#0072B2",
    }

    # Title
    ax.text(50, 96, "The compaction design space — six approaches, one root question",
            ha="center", fontsize=16, fontweight="bold")
    ax.text(50, 91,
            "All 12 methods we tested answer the same question — \"what should the agent's context "
            "look like after we touch it?\" — \nbut split on which signal they trust to decide.",
            ha="center", fontsize=11, color="#444")

    # Root node
    root_x, root_y = 50, 80
    ax.add_patch(plt.Rectangle((root_x - 18, root_y - 3), 36, 6,
                                facecolor="white", edgecolor="black", linewidth=1.5))
    ax.text(root_x, root_y, "How do we keep context cheap?",
            ha="center", va="center", fontsize=12, fontweight="bold")

    # Family nodes — laid out left-to-right with extra space for big families
    families = [
        ("noop",       "do nothing",            "no signal",                 6),
        ("summarize",  "rewrite middle",        "context-window pressure",  24),
        ("heuristic",  "drop oldest / scored",  "position + text heuristic",43),
        ("reorder",    "promote + hole",        "attention prior",           58),
        ("llm_score",  "LLM scorer",            "LLM judgement",             73),
        ("actiongraph","drop-when-superseded",  "agent's own actions (NEW)", 91),
    ]
    fam_y = 64
    fam_pos = {}
    for fkey, ftitle, fsig, fx in families:
        col = FAM[fkey]
        # Connector from root to family
        ax.plot([root_x, fx], [root_y - 3, fam_y + 4], color=col, linewidth=1.6, alpha=0.7)
        # Family box
        ax.add_patch(plt.Rectangle((fx - 7.5, fam_y - 4), 15, 8,
                                    facecolor=col, edgecolor="black", linewidth=1.0, alpha=0.85))
        ax.text(fx, fam_y + 0.8, ftitle, ha="center", va="center",
                fontsize=10, fontweight="bold", color=("white" if fkey != "heuristic" else "black"))
        ax.text(fx, fam_y - 2.4, f"signal: {fsig}", ha="center", va="center",
                fontsize=8, color=("white" if fkey != "heuristic" else "black"), style="italic")
        fam_pos[fkey] = (fx, fam_y - 4)

    # Leaves: (family, short_label, marker_style, footnote)
    # marker_style: 'square' = replace-in-place, 'circle' = hole-leave, 'star' = reorder
    leaves = [
        ("noop",        "none",            "circle", ""),
        ("summarize",   "naive\nsummary",  "square", ""),
        ("summarize",   "micro\ncompact",  "square", ""),
        ("summarize",   "prefix\npreserve","square", ""),
        ("heuristic",   "evict\noldest",   "circle", ""),
        ("heuristic",   "smart\nevict",    "circle", "wins on\nweak Qwen3"),
        ("reorder",     "position\naware", "star",   "proto-\nAdaptiveCache"),
        ("llm_score",   "score\nperiodic", "circle", ""),
        ("llm_score",   "llm\nreorg",      "star",   ""),
        ("actiongraph", "cons_evict\nplain",   "circle", "wins at\nsmall N"),
        ("actiongraph", "cons_evict\nfacts",   "square", "loses\npytest-7490"),
        ("actiongraph", "cons_evict\noutline", "square", "ties plain"),
    ]

    # Position leaves under each family
    leaves_per_fam = {}
    for fkey, *_ in leaves:
        leaves_per_fam.setdefault(fkey, 0)
        leaves_per_fam[fkey] += 1

    leaf_y = 40
    fam_count = {k: 0 for k in leaves_per_fam}
    for fkey, label, mstyle, footnote in leaves:
        fx, _ = fam_pos[fkey]
        n = leaves_per_fam[fkey]
        idx = fam_count[fkey]
        # Spread leaves horizontally under family. ~5 units per leaf for label room.
        if n == 1:
            offsets = [0]
        elif n == 2:
            offsets = [-3.5, 3.5]
        else:  # 3
            offsets = [-5.5, 0, 5.5]
        lx = fx + offsets[idx]
        col = FAM[fkey]
        # Connector family→leaf
        ax.plot([fx, lx], [fam_y - 4, leaf_y + 4.5], color=col, linewidth=1.0, alpha=0.5)
        # Marker
        if mstyle == "square":
            patch = plt.Rectangle((lx - 1.2, leaf_y + 2.8), 2.4, 2.4,
                                   facecolor=col, edgecolor="black", linewidth=0.8)
            ax.add_patch(patch)
        elif mstyle == "circle":
            patch = mpatches.Circle((lx, leaf_y + 4.0), 1.3,
                                     facecolor=col, edgecolor="black", linewidth=0.8)
            ax.add_patch(patch)
        elif mstyle == "star":
            ax.scatter([lx], [leaf_y + 4.0], marker="*", s=260,
                       facecolor=col, edgecolor="black", linewidth=0.8, zorder=5)
        # Label
        ax.text(lx, leaf_y, label, ha="center", va="top", fontsize=8.0,
                fontweight="bold", color="#222")
        # Footnote
        if footnote:
            ax.text(lx, leaf_y - 7.5, footnote, ha="center", va="top", fontsize=7.0,
                    color="#0072B2", style="italic")
        fam_count[fkey] += 1

    # Legend for marker styles
    legend_y = 16
    ax.text(50, legend_y + 4, "Structural action", ha="center", fontsize=10,
            fontweight="bold", color="#444")
    legend_items = [
        ("square", "replace-in-place\n(rewrites tokens — invalidates cache)", 25),
        ("circle", "drop with hole\n(no recompute, no new tokens)", 50),
        ("star",   "drop + reorder\n(promote stable items to prefix)", 75),
    ]
    for mstyle, ltext, lx in legend_items:
        if mstyle == "square":
            ax.add_patch(plt.Rectangle((lx - 1.4, legend_y - 1.4), 2.8, 2.8,
                                        facecolor="#888", edgecolor="black"))
        elif mstyle == "circle":
            ax.add_patch(mpatches.Circle((lx, legend_y), 1.5,
                                          facecolor="#888", edgecolor="black"))
        elif mstyle == "star":
            ax.scatter([lx], [legend_y], marker="*", s=320,
                       facecolor="#888", edgecolor="black", zorder=5)
        ax.text(lx + 4, legend_y, ltext, ha="left", va="center", fontsize=8.5, color="#222")

    # Footer takeaway
    ax.text(50, 4,
            "Takeaway: every family was tried. Only `none` and the action-graph (NOVEL) family "
            "produce no cache-invalidating writes — the rest pay a 'cliff tax' on every fire.",
            ha="center", fontsize=10.5, color="#222",
            bbox=dict(boxstyle="round,pad=0.5", fc="#f5f5f5", ec="#ccc", lw=0.6))

    plt.savefig(OUT_DIR / "fig9_method_family_tree.png", bbox_inches="tight")
    plt.close()
    print(f"  wrote {OUT_DIR / 'fig9_method_family_tree.png'}")


def main():
    print(f"Generating figures into {OUT_DIR}/")
    fig_pareto_swebench()
    fig_cost_decomposition()
    fig_placeholder_ablation()
    fig_chain_firing()
    fig_cliff_amplification()
    fig_project_arc()
    fig_compaction_wins()
    fig_vision()
    fig_methods_panels()
    fig_method_family_tree()
    print("Done.")


if __name__ == "__main__":
    main()
