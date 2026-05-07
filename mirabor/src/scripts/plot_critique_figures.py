#!/usr/bin/env python3
"""Three additional figures requested in the report critique:

1. miss_vs_cf.pdf       — miss-ratio-vs-cache-fraction curves
                          (2x2: workload x metric, lines per policy, alpha=1.0)
2. congress_alpha_crossover.pdf
                        — Congress alpha sweep at cf=0.01 showing
                          LHD-Full -> GDSF crossover
3. methodology_diagram.pdf
                        — block diagram: traces -> simulator -> policies
                          -> metrics -> cost model -> decision, with SHARDS
                          and seed sweep as validation/replication arms
"""
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "v5_lhdfull" / "figures"
OUT.mkdir(parents=True, exist_ok=True)


def load_rows():
    """Merge LHD-Full + LHD-Lite + LRU coverage across the three result CSVs."""
    rows = []
    seen = set()  # (workload, alpha, cf, policy)
    for path, rename, only_pols in [
        (ROOT / "results" / "v5_lhdfull" / "summary.csv", {}, None),
        (ROOT / "results" / "v4_lhd" / "summary.csv",
         {"LHD": "LHD-Lite"}, {"LHD", "LRU"}),
        (ROOT / "results" / "v4" / "summary.csv", {}, {"LRU"}),
    ]:
        for r in csv.DictReader(open(path)):
            pol = r["policy"]
            if only_pols is not None and pol not in only_pols:
                continue
            pol = rename.get(pol, pol)
            r["policy"] = pol
            r["alpha"] = float(r["alpha"])
            r["cache_frac"] = float(r["cache_frac"])
            r["obj_miss_mean"] = float(r["obj_miss_mean"])
            r["obj_miss_std"] = float(r["obj_miss_std"])
            r["byte_miss_mean"] = float(r["byte_miss_mean"])
            r["byte_miss_std"] = float(r["byte_miss_std"])
            key = (r["workload"], r["alpha"], r["cache_frac"], pol)
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    return rows


POLICY_COLORS = {
    "LRU":       "#7f7f7f",
    "SIEVE":     "#1f77b4",
    "W-TinyLFU": "#d62728",
    "GDSF":      "#e67e22",
    "LHD-Lite":  "#a0a0a0",
    "LHD-Full":  "#17becf",
}
POLICY_MARKERS = {
    "LRU": "o", "SIEVE": "s", "W-TinyLFU": "D", "GDSF": "P",
    "LHD-Lite": "v", "LHD-Full": "*",
}
POLICY_ORDER = ["LRU", "SIEVE", "W-TinyLFU", "GDSF", "LHD-Lite", "LHD-Full"]


# -----------------------------------------------------------------------------
# Figure 1: miss-ratio vs cache-fraction (the curves the report was missing)
# -----------------------------------------------------------------------------
def fig_miss_vs_cf(rows):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.6), sharex=True)
    workloads = [
        ("court",    "Court (heavy-tail size, 335x ratio)"),
        ("congress", "Congress (light-tail size, 29x ratio)"),
    ]
    metrics = [
        ("obj_miss_mean",  "obj_miss_std",  "object-miss ratio"),
        ("byte_miss_mean", "byte_miss_std", "byte-miss ratio"),
    ]

    alpha_pin = 1.0  # Court native; Congress this is a deployment-relevant point

    for col, (mkey, skey, mlabel) in enumerate(metrics):
        for row, (wname, wtitle) in enumerate(workloads):
            ax = axes[row, col]
            for pol in POLICY_ORDER:
                pts = sorted(
                    [r for r in rows
                     if r["workload"] == wname
                     and abs(r["alpha"] - alpha_pin) < 1e-9
                     and r["policy"] == pol],
                    key=lambda r: r["cache_frac"],
                )
                if not pts:
                    continue
                xs = [r["cache_frac"] for r in pts]
                ys = [r[mkey] for r in pts]
                es = [r[skey] for r in pts]
                ax.errorbar(
                    xs, ys, yerr=es,
                    color=POLICY_COLORS[pol],
                    marker=POLICY_MARKERS[pol],
                    markersize=8 if pol.startswith("LHD") else 6,
                    linewidth=1.6, capsize=3,
                    markeredgecolor="black", markeredgewidth=0.5,
                    label=pol,
                )
            ax.set_xscale("log")
            ax.set_xlabel("cache_frac (fraction of working-set bytes)", fontsize=10)
            ax.set_ylabel(mlabel, fontsize=10)
            ax.grid(alpha=0.3)
            if row == 0 and col == 0:
                ax.legend(fontsize=8, loc="lower left", ncol=2, framealpha=0.92)
            ax.set_title(f"{wtitle}\n{mlabel}, alpha={alpha_pin}", fontsize=10)

    fig.suptitle(
        "Miss-ratio vs. cache fraction at alpha=1.0 (5-seed mean ± 1 sigma).\n"
        "Left: object-miss (deployment cost on public-records APIs). "
        "Right: byte-miss (deployment cost on byte-priced backends).",
        fontsize=11, y=1.00,
    )
    fig.tight_layout()
    out = OUT / "miss_vs_cf.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


# -----------------------------------------------------------------------------
# Figure 2: Congress alpha crossover
# -----------------------------------------------------------------------------
def fig_congress_alpha(rows):
    fig, ax = plt.subplots(1, 1, figsize=(7.4, 5.0))
    cf_pin = 0.01

    # Show the four focal policies from Table 4
    focal = ["SIEVE", "W-TinyLFU", "GDSF", "LHD-Full"]
    for pol in focal:
        pts = sorted(
            [r for r in rows
             if r["workload"] == "congress"
             and abs(r["cache_frac"] - cf_pin) < 1e-9
             and r["policy"] == pol],
            key=lambda r: r["alpha"],
        )
        if not pts:
            continue
        xs = [r["alpha"] for r in pts]
        ys = [r["obj_miss_mean"] for r in pts]
        es = [r["obj_miss_std"] for r in pts]
        ax.errorbar(
            xs, ys, yerr=es,
            color=POLICY_COLORS[pol],
            marker=POLICY_MARKERS[pol],
            markersize=10 if pol.startswith("LHD") else 8,
            linewidth=2.0, capsize=3,
            markeredgecolor="black", markeredgewidth=0.5,
            label=pol,
        )

    # Crossover annotation: at alpha~0.9 GDSF passes LHD-Full
    ax.axvspan(0.85, 0.95, alpha=0.12, color="gray")
    ax.annotate(
        "GDSF <-> LHD-Full\ncrossover",
        xy=(0.9, 0.55), xytext=(1.05, 0.78),
        fontsize=9,
        arrowprops=dict(arrowstyle="->", color="black", lw=0.8),
    )

    ax.set_xlabel("Zipf alpha (popularity skew)", fontsize=11)
    ax.set_ylabel("object-miss ratio (lower better)", fontsize=11)
    ax.set_title(
        "Congress, cache_frac=0.01: LHD-Full -> GDSF crossover with alpha\n"
        "(LHD-Full's residual-reuse-distance wins under weak frequency; "
        "GDSF's size-priority wins as alpha grows)",
        fontsize=10,
    )
    ax.grid(alpha=0.3)
    ax.legend(fontsize=10, loc="upper right", framealpha=0.92)
    ax.set_xticks([0.6, 0.8, 1.0, 1.2])

    fig.tight_layout()
    out = OUT / "congress_alpha_crossover.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


# -----------------------------------------------------------------------------
# Figure 3: methodology / system diagram
# -----------------------------------------------------------------------------
def fig_methodology():
    """Five-row vertical pipeline. Validation/replication arms attach
    to the metrics row from the side; cost-model selects the verdict."""
    fig, ax = plt.subplots(figsize=(11, 7.4))
    ax.set_xlim(0, 22)
    ax.set_ylim(0, 14)
    ax.axis("off")

    def box(x, y, w, h, label, fc="#f0f0f0", ec="black", fs=9, bold=False):
        b = FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.08,rounding_size=0.18",
            linewidth=1.0, edgecolor=ec, facecolor=fc,
        )
        ax.add_patch(b)
        weight = "bold" if bold else "normal"
        ax.text(x + w / 2, y + h / 2, label,
                ha="center", va="center", fontsize=fs, fontweight=weight)

    def arrow(x1, y1, x2, y2, label=None, color="black",
              style="-|>", lpos=None, lalign="center"):
        a = FancyArrowPatch(
            (x1, y1), (x2, y2),
            arrowstyle=style, mutation_scale=14,
            linewidth=1.2, color=color,
        )
        ax.add_patch(a)
        if label:
            lx, ly = lpos if lpos is not None else (
                (x1 + x2) / 2, (y1 + y2) / 2 + 0.22)
            ax.text(lx, ly, label,
                    ha=lalign, va="bottom", fontsize=8, style="italic",
                    color=color)

    # Row 1 (top): traces -> overlay -> simulator -> policies (4 boxes)
    y_main = 11.5
    box(0.4, y_main, 4.0, 1.6,
        "Real traces\n"
        "Congress.gov  20,692 reqs\n"
        "CourtListener 20,000 reqs",
        fc="#e8f0ff", bold=True)
    box(5.0, y_main, 4.4, 1.6,
        "Replay-Zipf overlay\n"
        "(real keys + sizes,\n"
        "resampled popularity)",
        fc="#fff5e0")
    box(10.0, y_main, 4.8, 1.6,
        "Cache simulator\n"
        "(Rust, 7 M acc/sec)\n"
        "5 seeds x 4 alpha x 4 cf",
        fc="#f0fff0", bold=True)
    box(15.4, y_main, 6.2, 1.6,
        "10 policies\n"
        "LRU, FIFO, CLOCK, S3-FIFO,\n"
        "SIEVE, W-TinyLFU, GDSF,\n"
        "GDSF-Cost, LHD-Lite, LHD-Full",
        fc="#f0fff0", fs=8)

    # Row 2: per-cell metrics (centered)
    y_metrics = 8.0
    box(7.4, y_metrics, 7.2, 1.6,
        "Per-cell metrics\n"
        "object-miss / byte-miss (5-seed mean ± sigma)",
        fc="#ffe6e6", bold=True, fs=10)

    # Validation arms (left side, separate)
    y_val = 8.0
    box(0.4, y_val, 6.2, 1.6,
        "Validation\n"
        "SHARDS 1%/10% MAE = 0.038 (< 0.05 gate)\n"
        "Memcached-synth: LHD-Full beats LRU 13-18pp",
        fc="#f6e6ff", fs=8)

    # Mechanism arm (right side)
    box(15.4, y_val, 6.2, 1.6,
        "Mechanism analysis\n"
        "3 controls (size / high-freq / matched-card)\n"
        "-> size-outlier rejection (not freq-gradient)",
        fc="#f5f5f5", fs=8)

    # Row 3: cost model (full-width, the selection step)
    y_cost = 4.6
    box(0.4, y_cost, 21.2, 1.8,
        "Cost-attribution layer (this paper's core move)\n"
        "Public-records APIs: object-miss is binding (rate-limit + TTFB-dominated origin)\n"
        "Byte-priced backends (S3): byte-miss is binding (egress dollars)",
        fc="#fffac0", bold=True, fs=10)

    # Row 4: verdicts (3 columns)
    y_verdict = 1.2
    box(0.4, y_verdict, 6.8, 2.4,
        "Court (heavy-tail size)\n"
        "+ request-priced\n"
        "-> GDSF\n"
        "at cf >= 0.01",
        fc="#d4f4dd", bold=True, fs=10)
    box(7.6, y_verdict, 6.8, 2.4,
        "Congress (light-tail size)\n"
        "+ low alpha (<= 0.8)\n"
        "-> LHD-Full\n"
        "(GDSF at alpha >= 1.0)",
        fc="#d4f4dd", bold=True, fs=10)
    box(14.8, y_verdict, 6.8, 2.4,
        "Court + byte-priced\n"
        "(S3 egress dollars)\n"
        "-> W-TinyLFU\n"
        "(verdict flips)",
        fc="#d4f4dd", bold=True, fs=10)

    # Top-row arrows (left to right)
    arrow(4.4, y_main + 0.8, 5.0, y_main + 0.8)
    arrow(9.4, y_main + 0.8, 10.0, y_main + 0.8)
    arrow(14.8, y_main + 0.8, 15.4, y_main + 0.8)

    # Top-row down to metrics (from policies)
    arrow(18.5, y_main, 14.6, y_metrics + 1.6,
          color="#444")

    # Validation -> metrics (horizontal)
    arrow(6.6, y_metrics + 0.8, 7.4, y_metrics + 0.8,
          color="#7733aa", label="validates",
          lpos=(7.0, y_metrics + 0.95), lalign="center")

    # Mechanism <- metrics (horizontal)
    arrow(14.6, y_metrics + 0.8, 15.4, y_metrics + 0.8,
          color="#555", label="diagnoses",
          lpos=(15.0, y_metrics + 0.95), lalign="center")

    # Metrics -> cost model (down)
    arrow(11.0, y_metrics, 11.0, y_cost + 1.8,
          color="#444")

    # Cost model -> verdicts (down, three arrows)
    arrow(3.8, y_cost, 3.8, y_verdict + 2.4,
          color="#cc8800", label="selects",
          lpos=(4.4, (y_cost + y_verdict + 2.4) / 2 - 0.05),
          lalign="left")
    arrow(11.0, y_cost, 11.0, y_verdict + 2.4,
          color="#cc8800")
    arrow(18.2, y_cost, 18.2, y_verdict + 2.4,
          color="#cc8800")

    fig.suptitle(
        "Methodology pipeline: traces -> overlay -> simulator -> metrics; "
        "cost-attribution layer selects per-workload verdict",
        fontsize=11, y=0.98,
    )
    fig.tight_layout()
    out = OUT / "methodology_diagram.pdf"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} rows total")
    fig_miss_vs_cf(rows)
    fig_congress_alpha(rows)
    fig_methodology()


if __name__ == "__main__":
    main()
