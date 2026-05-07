"""Aggregate all thrash_*.json into one comparison table + two bar plots."""

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
PROBES = ["CONC_SHARED", "CONC_PRIVATE", "CONC_BOTH"]


def load(v, p):
    f = OUT / f"thrash_{v}_{p}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def row(v, p, d):
    if not d:
        return None
    pq = d.get("t1_per_query", {})
    t1 = d.get("t1_delta", {})
    tenant_stats = d.get("tenant_stats", {})
    pw = np.mean([t.get("per_worker_qps", float("nan"))
                  for t in tenant_stats.values() if t.get("n", 0) > 0])
    total_n = sum(t.get("n", 0) for t in tenant_stats.values())
    # hnsw churn
    by_label = d.get("t2", {}).get("by_label", {})
    hnsw = by_label.get("hnsw_index", {})
    return dict(
        variant=v,
        probe=p,
        total_queries=total_n,
        per_worker_qps=pw,
        majflt_per_query=pq.get("majflt", float("nan")),
        refaults_per_query=pq.get("cg_workingset_refault_file", float("nan")),
        MB_read_per_query=pq.get("read_bytes", 0) / 1e6,
        hnsw_res_start_MB=hnsw.get("res_start", 0) / 1e6,
        hnsw_res_end_MB=hnsw.get("res_end", 0) / 1e6,
        hnsw_churn_MB=hnsw.get("churn", 0) / 1e6,
        activate_per_query=pq.get("cg_workingset_activate_file", float("nan")),
    )


def main():
    rows = []
    for v in VARIANTS:
        for p in PROBES:
            r = row(v, p, load(v, p))
            if r:
                rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "thrash_summary.csv", index=False)
    print(df.to_string(index=False))

    # Refaults/query bar chart
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.2))
    x = np.arange(len(VARIANTS))
    w = 0.25
    for i, p in enumerate(PROBES):
        vals = [df[(df.variant == v) & (df.probe == p)]["refaults_per_query"].iloc[0]
                if not df[(df.variant == v) & (df.probe == p)].empty else 0
                for v in VARIANTS]
        ax1.bar(x + (i - 1) * w, vals, w, color=PALETTE[i],
                label=p)
        for xi, val in zip(x + (i - 1) * w, vals):
            if val > 0:
                ax1.text(xi, val * 1.1, f"{val:,.0f}", ha="center",
                         fontsize=9, rotation=45)
    ax1.set_xticks(x)
    ax1.set_xticklabels([v.replace("wiki_", "") for v in VARIANTS])
    ax1.set_yscale("log")
    ax1.set_ylabel("workingset refaults / query (log)")
    ax1.set_title("Thrashing severity per probe")
    ax1.legend()

    # MB read per query
    for i, p in enumerate(PROBES):
        vals = [df[(df.variant == v) & (df.probe == p)]["MB_read_per_query"].iloc[0]
                if not df[(df.variant == v) & (df.probe == p)].empty else 0
                for v in VARIANTS]
        ax2.bar(x + (i - 1) * w, vals, w, color=PALETTE[i],
                label=p)
        for xi, val in zip(x + (i - 1) * w, vals):
            if val > 0:
                ax2.text(xi, val * 1.1, f"{val:,.0f}", ha="center",
                         fontsize=9, rotation=45)
    ax2.set_xticks(x)
    ax2.set_xticklabels([v.replace("wiki_", "") for v in VARIANTS])
    ax2.set_yscale("log")
    ax2.set_ylabel("disk MB read / query (log)")
    ax2.set_title("I/O traffic per probe")
    ax2.legend()

    fig.tight_layout()
    out = OUT / "thrash_compare.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
