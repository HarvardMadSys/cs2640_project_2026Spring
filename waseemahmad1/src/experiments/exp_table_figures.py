"""Generate slide-ready table images from experiment CSV outputs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "results" / ".mplconfig"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

PURPLE = "#A78BFA"
TEAL = "#2A9D8F"
GREEN = "#6D28D9"
HEADER_BG = "#EEF2FF"
ALT_ROW = "#F8F9FB"
BORDER = "#D6DAE3"
TEXT = "#1F2937"


def _save_table_image(df: pd.DataFrame, title: str, output: Path, col_widths: list[float]) -> None:
    fig_h = 0.7 + 0.42 * (len(df) + 1)
    fig_w = max(8.5, sum(col_widths) * 2.0)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("#FFFFFF")
    ax.axis("off")

    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
        colWidths=col_widths,
    )

    table.auto_set_font_size(False)
    table.set_fontsize(10.5)
    table.scale(1.0, 1.35)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(BORDER)
        cell.set_linewidth(0.7)
        if r == 0:
            cell.set_facecolor(HEADER_BG)
            cell.set_text_props(weight="bold", color=TEXT)
        else:
            if r % 2 == 0:
                cell.set_facecolor(ALT_ROW)
            else:
                cell.set_facecolor("#FFFFFF")
            cell.set_text_props(color=TEXT)

    ax.set_title(title, fontsize=15, fontweight="semibold", color=TEXT, pad=14)
    plt.tight_layout()
    plt.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    data_dir = ROOT / "results" / "data"
    fig_dir = ROOT / "results" / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Table 1: baseline summary
    baseline = pd.read_csv(data_dir / "policy_compare.csv")
    pivot = baseline.pivot(index="workload", columns="policy", values="hit_rate_mean").reset_index()
    pivot = pivot.rename(columns={"fifo": "FIFO Hit Rate", "lru": "LRU Hit Rate", "workload": "Workload"})
    pivot["Abs Gain (LRU-FIFO)"] = pivot["LRU Hit Rate"] - pivot["FIFO Hit Rate"]
    pivot["Rel Gain %"] = (pivot["Abs Gain (LRU-FIFO)"] / pivot["FIFO Hit Rate"]).replace([float("inf")], 0) * 100
    pivot = pivot[["Workload", "FIFO Hit Rate", "LRU Hit Rate", "Abs Gain (LRU-FIFO)", "Rel Gain %"]]
    for c in ["FIFO Hit Rate", "LRU Hit Rate", "Abs Gain (LRU-FIFO)"]:
        pivot[c] = pivot[c].map(lambda x: f"{x:.4f}")
    pivot["Rel Gain %"] = pivot["Rel Gain %"].map(lambda x: f"{x:.2f}%")
    _save_table_image(
        pivot,
        "Baseline Policy Comparison (Capacity = 128, n = 15)",
        fig_dir / "table_baseline_policy_compare.png",
        [0.22, 0.17, 0.17, 0.22, 0.15],
    )

    # Table 2: cache size sweep
    sweep = pd.read_csv(data_dir / "cache_size_sweep.csv")
    fifo = sweep[sweep["policy"] == "fifo"][["capacity", "hit_rate_mean", "recomputation_cost_mean"]]
    lru = sweep[sweep["policy"] == "lru"][["capacity", "hit_rate_mean", "recomputation_cost_mean"]]
    merged = fifo.merge(lru, on="capacity", suffixes=("_fifo", "_lru")).sort_values("capacity")
    t2 = pd.DataFrame(
        {
            "Capacity": merged["capacity"],
            "FIFO Hit Rate": merged["hit_rate_mean_fifo"],
            "LRU Hit Rate": merged["hit_rate_mean_lru"],
            "Hit Gain": merged["hit_rate_mean_lru"] - merged["hit_rate_mean_fifo"],
            "Recompute Reduction": merged["recomputation_cost_mean_fifo"]
            - merged["recomputation_cost_mean_lru"],
        }
    )
    t2["Capacity"] = t2["Capacity"].astype(int).astype(str)
    for c in ["FIFO Hit Rate", "LRU Hit Rate", "Hit Gain"]:
        t2[c] = t2[c].map(lambda x: f"{x:.4f}")
    t2["Recompute Reduction"] = t2["Recompute Reduction"].map(lambda x: f"{x:.1f}")
    _save_table_image(
        t2,
        "Long-Context Cache Size Sweep (n = 15)",
        fig_dir / "table_cache_size_sweep.png",
        [0.12, 0.17, 0.17, 0.16, 0.22],
    )

    # Table 3: shared global impact
    shared = pd.read_csv(data_dir / "shared_global_compare.csv")
    rows = []
    for policy in ["fifo", "lru"]:
        local = shared[(shared["policy"] == policy) & (shared["mode"] == "local")].iloc[0]
        global_ = shared[(shared["policy"] == policy) & (shared["mode"] == "shared_global")].iloc[0]
        rows.append(
            {
                "Policy": policy.upper(),
                "Local Hit Rate": local["hit_rate_mean"],
                "Shared Hit Rate": global_["hit_rate_mean"],
                "Hit Gain": global_["hit_rate_mean"] - local["hit_rate_mean"],
                "Recompute Reduction": local["recomputation_cost_mean"] - global_["recomputation_cost_mean"],
            }
        )
    t3 = pd.DataFrame(rows)
    for c in ["Local Hit Rate", "Shared Hit Rate", "Hit Gain"]:
        t3[c] = t3[c].map(lambda x: f"{x:.4f}")
    t3["Recompute Reduction"] = t3["Recompute Reduction"].map(lambda x: f"{x:.1f}")
    _save_table_image(
        t3,
        "100% Goal: Shared-Global vs Local Isolated (n = 15)",
        fig_dir / "table_shared_global_impact.png",
        [0.12, 0.18, 0.18, 0.16, 0.24],
    )

    # Table 4: adaptive vs lru
    adaptive = pd.read_csv(data_dir / "adaptive_controller_delta_vs_lru.csv")
    t4 = adaptive[adaptive["policy"] == "adaptive"][
        ["shift_level", "capacity", "hit_rate_gain_vs_lru", "recompute_reduction_vs_lru"]
    ].copy()
    t4 = t4.rename(
        columns={
            "shift_level": "Shift",
            "capacity": "Capacity",
            "hit_rate_gain_vs_lru": "Hit Gain vs LRU",
            "recompute_reduction_vs_lru": "Recompute Reduction vs LRU",
        }
    )
    t4["Shift"] = t4["Shift"].str.title()
    t4["Capacity"] = t4["Capacity"].astype(int).astype(str)
    t4["Hit Gain vs LRU"] = t4["Hit Gain vs LRU"].map(lambda x: f"{x:+.4f}")
    t4["Recompute Reduction vs LRU"] = t4["Recompute Reduction vs LRU"].map(lambda x: f"{x:+.1f}")
    _save_table_image(
        t4,
        "125% Goal: Adaptive Controller vs LRU (n = 15)",
        fig_dir / "table_adaptive_vs_lru.png",
        [0.14, 0.12, 0.23, 0.28],
    )

    # Table 5: mooncake context (manual research framing)
    t5 = pd.DataFrame(
        [
            ["System Type", "Production serving system", "Controlled simulator"],
            ["Cache Scope", "Global/shared KV cache", "Global/shared KV cache simulation"],
            ["Policy Focus", "System-level cache-aware scheduling", "FIFO/LRU + adaptive cost-aware"],
            ["Evaluation", "Real deployment-scale measurements", "15-seed synthetic workload experiments"],
        ],
        columns=["Aspect", "MOONCAKE", "This Project"],
    )
    _save_table_image(
        t5,
        "Context Table: MOONCAKE vs This Project",
        fig_dir / "table_mooncake_context.png",
        [0.22, 0.34, 0.34],
    )

    print("Saved:", fig_dir / "table_baseline_policy_compare.png")
    print("Saved:", fig_dir / "table_cache_size_sweep.png")
    print("Saved:", fig_dir / "table_shared_global_impact.png")
    print("Saved:", fig_dir / "table_adaptive_vs_lru.png")
    print("Saved:", fig_dir / "table_mooncake_context.png")


if __name__ == "__main__":
    main()
