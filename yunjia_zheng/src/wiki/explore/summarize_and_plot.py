"""Read existing bench_result CSVs for the three variants and summarise.

No Milvus calls, pure offline analysis. Produces:
  explore/out/summary.csv        table of QPS/lat/recall per (variant, mode)
  explore/out/qps_timeseries.png QPS vs time per variant, solo vs A+B
  explore/out/interference_bar.png  per-worker QPS solo vs A+B per variant
  explore/out/latency_box.png    p50/p95 latency comparison

A mode is one of: A (solo), B (solo), A_B (both tenants concurrent).
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style, PALETTE  # noqa: E402

apply_style()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

VARIANTS = ["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"]
MODES = ["A", "B", "A_B"]
TAG = "dur300s_ef200_k100"


def rpt_from_log(log_path: Path) -> int:
    """Grep request_per_tenant from the run log header."""
    if not log_path.exists():
        return -1
    with log_path.open() as f:
        head = f.readline()
    m = re.search(r"'request_per_tenant':\s*(\d+)", head)
    return int(m.group(1)) if m else -1


def load_summary() -> pd.DataFrame:
    rows = []
    for v in VARIANTS:
        bdir = ROOT / f"{v}_bench_result"
        for mode in MODES:
            pqfile = bdir / f"per_query_recall_{mode}_{TAG}.csv"
            if not pqfile.exists():
                continue
            log = bdir / f"run_{mode}_{TAG}.log"
            rpt = rpt_from_log(log)
            df = pd.read_csv(pqfile)
            for t in sorted(df["tenant"].unique()):
                sub = df[df["tenant"] == t]
                n = len(sub)
                lat = sub["latency_ms"].to_numpy()
                recall = sub["hits"].sum() / (n * 100) if n else float("nan")
                # Exclude warm-up: skip first 5% of rows to avoid cold-start skew.
                skip = max(1, n // 20)
                sub_ss = sub.iloc[skip:]
                t_span = sub_ss["t_rel_s"].max() - sub_ss["t_rel_s"].min()
                qps_ss = len(sub_ss) / t_span if t_span > 0 else float("nan")
                rows.append({
                    "variant": v,
                    "mode": mode,
                    "tenant": t,
                    "n": n,
                    "rpt": rpt,
                    "qps_ss": qps_ss,                       # steady-state QPS
                    "qps_per_worker": qps_ss / rpt if rpt > 0 else float("nan"),
                    "p50_ms": float(np.percentile(lat, 50)),
                    "p95_ms": float(np.percentile(lat, 95)),
                    "p99_ms": float(np.percentile(lat, 99)),
                    "recall": recall,
                })
    return pd.DataFrame(rows)


def plot_qps_timeseries(df_summary: pd.DataFrame):
    """One subplot per variant, showing QPS vs time for modes A, B, and A (under A+B)."""
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.0), sharey=True)
    for ax, v in zip(axes, VARIANTS):
        bdir = ROOT / f"{v}_bench_result"
        for mode, color in zip(["A", "B", "A_B"], [PALETTE[0], PALETTE[1], PALETTE[3]]):
            fpath = bdir / f"throughput_{mode}_{TAG}.csv"
            if not fpath.exists():
                continue
            d = pd.read_csv(fpath)
            if mode == "A_B":
                ax.plot(d["t_rel_s"], d["qps_A"], color=PALETTE[3], lw=1.5,
                        label="A+B: A", alpha=0.9)
                ax.plot(d["t_rel_s"], d["qps_B"], color=PALETTE[4], lw=1.5,
                        linestyle="--", label="A+B: B", alpha=0.9)
            else:
                ax.plot(d["t_rel_s"], d[f"qps_{mode}"], color=color, lw=1.6,
                        label=f"{mode} solo")
        ax.set_title(v.replace("wiki_", ""))
        ax.set_xlabel("time (s)")
        ax.grid(True, linestyle=":", alpha=0.5)
    axes[0].set_ylabel("QPS (5 s window)")
    axes[-1].legend(loc="upper right", fontsize=10)
    fig.suptitle("Throughput over time: solo vs. concurrent (A+B)", y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / "qps_timeseries.png", dpi=150)
    plt.close(fig)


def plot_interference_bar(df: pd.DataFrame):
    """Per-worker QPS under solo vs. A+B for each variant, to normalise away rpt."""
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    x = np.arange(len(VARIANTS))
    w = 0.35
    solo_perworker = []
    concur_perworker = []
    for v in VARIANTS:
        solo = df[(df.variant == v) & (df["mode"].isin(["A", "B"]))]
        concur = df[(df.variant == v) & (df["mode"] == "A_B")]
        solo_perworker.append(solo["qps_per_worker"].mean())
        concur_perworker.append(concur["qps_per_worker"].mean())
    ax.bar(x - w / 2, solo_perworker, w, color=PALETTE[0], label="solo (per worker)")
    ax.bar(x + w / 2, concur_perworker, w, color=PALETTE[3], label="A+B (per worker)")
    for i, (s, c) in enumerate(zip(solo_perworker, concur_perworker)):
        drop = 100.0 * (1 - c / s) if s else 0
        ax.text(i + w / 2, c + 0.02, f"-{drop:.0f}%", ha="center", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels([v.replace("wiki_", "") for v in VARIANTS])
    ax.set_ylabel("QPS per worker thread")
    ax.set_title("Interference: per worker throughput drop when sharing the node")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "interference_bar.png", dpi=150)
    plt.close(fig)


def plot_latency_box(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 4.2))
    labels = []
    vals_p50, vals_p95 = [], []
    for v in VARIANTS:
        for mode in ["A", "A_B"]:
            row = df[(df.variant == v) & (df["mode"] == mode) & (df.tenant == "A")]
            if row.empty:
                continue
            labels.append(f"{v.replace('wiki_','')}\n{mode}")
            vals_p50.append(row.iloc[0]["p50_ms"])
            vals_p95.append(row.iloc[0]["p95_ms"])
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2, vals_p50, w, color=PALETTE[0], label="p50")
    ax.bar(x + w / 2, vals_p95, w, color=PALETTE[3], label="p95")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0)
    ax.set_ylabel("latency (ms)")
    ax.set_title("Tenant A latency: solo vs. under A+B")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "latency_box.png", dpi=150)
    plt.close(fig)


def main():
    df = load_summary()
    df.to_csv(OUT / "summary.csv", index=False)
    print(df.to_string(index=False))
    plot_qps_timeseries(df)
    plot_interference_bar(df)
    plot_latency_box(df)
    print(f"\nWrote outputs under {OUT}")


if __name__ == "__main__":
    main()
