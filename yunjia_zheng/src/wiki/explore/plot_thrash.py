"""Plot T1 counters + T2 residency time series for one or more thrash probes.

Usage:
  python plot_thrash.py wiki_QC08_SS08_CONC_SHARED wiki_QC08_SS08_CONC_BOTH
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style, PALETTE  # noqa: E402

apply_style()

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def load_tag(tag: str):
    t1 = OUT / f"thrash_{tag}_t1.csv"
    t2 = OUT / f"thrash_{tag}_t2.csv"
    js = OUT / f"thrash_{tag}.json"
    summary = json.loads(js.read_text())
    df_t1 = pd.read_csv(t1) if t1.exists() else pd.DataFrame()
    df_t2 = pd.read_csv(t2) if t2.exists() else pd.DataFrame()
    return df_t1, df_t2, summary


def plot_counters(tags):
    """Plot major faults and refaults per second vs time for each tag.
    Times normalised so measure_start=0."""
    n = len(tags)
    fig, axes = plt.subplots(2, n, figsize=(5 * n, 6.5), sharex="col")
    if n == 1:
        axes = axes.reshape(2, 1)
    for i, tag in enumerate(tags):
        df_t1, _df_t2, summary = load_tag(tag)
        if df_t1.empty:
            continue
        t_measure = summary["phase_ts"]["measure_start"]
        df_t1 = df_t1.sort_values("t").reset_index(drop=True)
        df_t1["t_rel"] = df_t1["t"] - t_measure

        # derivative per second for majflt and refault
        for col, ax, label, color in [
            ("majflt",                        axes[0, i], "process major faults", PALETTE[1]),
            ("cg_workingset_refault_file",    axes[1, i], "workingset refaults",  PALETTE[3]),
        ]:
            if col not in df_t1.columns:
                continue
            dv = df_t1[col].diff().fillna(0)
            dt = df_t1["t"].diff().fillna(1.0).clip(lower=1e-6)
            rate = dv / dt
            ax.plot(df_t1["t_rel"], rate, color=color, lw=1.3)
            ax.axvline(0, color="0.5", linestyle="--", lw=1)
            ax.set_title(f"{tag}\n{label} (/ s)")
            ax.set_ylabel("/ s")
        axes[-1, i].set_xlabel("time from measure_start (s)")
    fig.tight_layout()
    out = OUT / ("counters_" + "_vs_".join(tags) + ".png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Wrote {out}")


def plot_residency(tags):
    n = len(tags)
    fig, axes = plt.subplots(1, n, figsize=(6 * n, 4.0), sharey=True)
    if n == 1:
        axes = [axes]
    for i, tag in enumerate(tags):
        _df_t1, df_t2, summary = load_tag(tag)
        if df_t2.empty:
            continue
        t_measure = summary["phase_ts"]["measure_start"]
        df_t2["t_rel"] = df_t2["t"] - t_measure
        ax = axes[i]
        # aggregate resident_bytes per label per time
        agg = df_t2.groupby(["t_rel", "label"])["resident_bytes"].sum().unstack("label")
        for j, lbl in enumerate(sorted(agg.columns)):
            ax.plot(agg.index, agg[lbl] / 1e9, lw=1.5,
                    color=PALETTE[j % len(PALETTE)], label=lbl)
        ax.axvline(0, color="0.5", linestyle="--", lw=1)
        ax.set_title(tag)
        ax.set_xlabel("time from measure_start (s)")
        ax.legend(fontsize=10)
    axes[0].set_ylabel("resident bytes across sampled files (GB)")
    fig.suptitle("Sampled-segment page-cache residency", y=1.02)
    fig.tight_layout()
    out = OUT / ("residency_" + "_vs_".join(tags) + ".png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    args = ap.parse_args()
    plot_counters(args.tags)
    plot_residency(args.tags)


if __name__ == "__main__":
    main()
