"""Aggregate per_partition_cost_*.json for the three variants and draw a
single comparison figure and a numeric summary CSV."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style, PALETTE  # noqa: E402

apply_style()

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

VARIANTS = ["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"]
PROBES = [
    "SOLO_SHARED_ONLY",
    "SOLO_PRIVATE_ONLY",
    "SOLO_BOTH",
    "CONC_SHARED_X_SHARED",
    "CONC_PRIVATE_X_PRIVATE",
    "CONC_BOTH_X_BOTH",
]


def load_warm(variant: str) -> dict:
    p = OUT / f"per_partition_cost_{variant}_warm.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())["results"]


def flatten() -> pd.DataFrame:
    rows = []
    for v in VARIANTS:
        res = load_warm(v)
        for probe in PROBES:
            data = res.get(probe, {})
            for tenant_label, stats in data.items():
                rows.append(dict(
                    variant=v, probe=probe, tenant=tenant_label,
                    per_worker_qps=stats.get("per_worker_qps", float("nan")),
                    qps=stats.get("qps", float("nan")),
                    p50_ms=stats.get("p50", float("nan")),
                    p95_ms=stats.get("p95", float("nan")),
                ))
    return pd.DataFrame(rows)


def plot_perworker(df: pd.DataFrame):
    """Per-worker QPS by probe, grouped per variant.
    For concurrent probes we average A and B."""
    fig, ax = plt.subplots(figsize=(11, 4.2))
    probe_labels = PROBES
    x = np.arange(len(probe_labels))
    w = 0.26
    colors = [PALETTE[0], PALETTE[1], PALETTE[3]]
    for i, v in enumerate(VARIANTS):
        vals = []
        for probe in probe_labels:
            sub = df[(df.variant == v) & (df.probe == probe)]
            vals.append(sub["per_worker_qps"].mean())
        ax.bar(x + (i - 1) * w, vals, w, color=colors[i],
               label=v.replace("wiki_", ""))
        for xi, val in zip(x + (i - 1) * w, vals):
            if np.isfinite(val):
                ax.text(xi, val + 0.05, f"{val:.2f}", ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([p.replace("_", "\n") for p in probe_labels], fontsize=10)
    ax.set_ylabel("per-worker QPS (warm cache, 20 s measurement)")
    ax.set_title("Per-partition probe: where is the HNSW cost and where does interference hit")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "probe_perworker.png", dpi=150)
    plt.close(fig)


def plot_latency(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(11, 4.2))
    probe_labels = PROBES
    x = np.arange(len(probe_labels))
    w = 0.26
    colors = [PALETTE[0], PALETTE[1], PALETTE[3]]
    for i, v in enumerate(VARIANTS):
        vals = []
        for probe in probe_labels:
            sub = df[(df.variant == v) & (df.probe == probe)]
            vals.append(sub["p50_ms"].mean())
        ax.bar(x + (i - 1) * w, vals, w, color=colors[i],
               label=v.replace("wiki_", ""))
    ax.set_xticks(x)
    ax.set_xticklabels([p.replace("_", "\n") for p in probe_labels], fontsize=10)
    ax.set_ylabel("p50 latency (ms)")
    ax.set_yscale("log")
    ax.set_title("p50 latency per probe (log scale)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "probe_latency.png", dpi=150)
    plt.close(fig)


def main():
    df = flatten()
    df.to_csv(OUT / "probe_summary.csv", index=False)
    print(df.to_string(index=False))
    plot_perworker(df)
    plot_latency(df)
    print(f"\nWrote {OUT/'probe_summary.csv'}, probe_perworker.png, probe_latency.png")


if __name__ == "__main__":
    main()
