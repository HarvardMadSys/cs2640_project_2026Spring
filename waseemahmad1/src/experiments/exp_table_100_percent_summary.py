"""Generate a 100% slide table with absolute + percentage recompute reduction."""

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

HEADER_BG = "#EEF2FF"
ALT_ROW = "#F8F9FB"
BORDER = "#D6DAE3"
TEXT = "#1F2937"


def save_table(df: pd.DataFrame, title: str, output: Path) -> None:
    fig_h = 0.7 + 0.46 * (len(df) + 1)
    fig_w = 11.5
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("#FFFFFF")
    ax.axis("off")

    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
        colWidths=[0.13, 0.19, 0.19, 0.15, 0.15, 0.18, 0.14],
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
            cell.set_facecolor(ALT_ROW if r % 2 == 0 else "#FFFFFF")
            cell.set_text_props(color=TEXT)

    ax.set_title(title, fontsize=15, fontweight="semibold", color=TEXT, pad=14)
    plt.tight_layout()
    plt.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    data_path = ROOT / "results" / "data" / "shared_global_compare.csv"
    out_path = ROOT / "results" / "figures" / "table_shared_global_impact_with_pct.png"

    df = pd.read_csv(data_path)
    rows = []

    for policy in ["fifo", "lru"]:
        local = df[(df["policy"] == policy) & (df["mode"] == "local")].iloc[0]
        shared = df[(df["policy"] == policy) & (df["mode"] == "shared_global")].iloc[0]

        recompute_abs = local["recomputation_cost_mean"] - shared["recomputation_cost_mean"]
        recompute_pct = (recompute_abs / local["recomputation_cost_mean"]) * 100 if local["recomputation_cost_mean"] else 0.0

        rows.append(
            {
                "Policy": policy.upper(),
                "Local Hit Rate": f"{local['hit_rate_mean']:.4f}",
                "Shared Hit Rate": f"{shared['hit_rate_mean']:.4f}",
                "Hit Gain": f"{(shared['hit_rate_mean'] - local['hit_rate_mean']):+.4f}",
                "Local Recompute": f"{local['recomputation_cost_mean']:.1f}",
                "Recompute Reduction": f"{recompute_abs:.1f}",
                "Reduction %": f"{recompute_pct:.1f}%",
            }
        )

    table_df = pd.DataFrame(rows)
    save_table(
        table_df,
        "100% Goal: Shared Global Cache Impact (Absolute + Percentage)",
        out_path,
    )
    print("Saved:", out_path)


if __name__ == "__main__":
    main()
