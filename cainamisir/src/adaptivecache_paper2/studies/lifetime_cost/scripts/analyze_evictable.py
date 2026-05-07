"""From the Hermes reference graph, compute what fraction of context could
be safely dropped (i.e. messages whose content is never referenced again
in the same trajectory).

Outputs:
  studies/lifetime_cost/out/hermes/evictable_summary.csv
  studies/lifetime_cost/out/hermes/evictable_distribution.png
  studies/lifetime_cost/out/hermes/savings_by_threshold.png
  prints a table + a few real examples of zero-cite messages
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from studies.lifetime_cost.pipeline.external_traces import load_hermes_agent_traces


REF_CSV = Path("studies/lifetime_cost/out/hermes/reference_graph.csv")
OUT = Path("studies/lifetime_cost/out/hermes")


def load_ref_graph():
    by_task = defaultdict(list)
    with open(REF_CSV) as f:
        for row in csv.DictReader(f):
            by_task[row["task_id"]].append({
                "msg_index": int(row["msg_index"]),
                "role": row["role"],
                "tokens": int(row["tokens"]),
                "cites": int(row["downstream_cites"]),
            })
    return by_task


def main():
    by_task = load_ref_graph()
    print(f"Loaded reference graph for {len(by_task)} traces")

    # ------------------------------------------------------------------
    # 1) Aggregate evictable-token stats at different thresholds
    # ------------------------------------------------------------------
    thresholds = [0, 1, 2, 3, 5]  # "evict if cites <= threshold"
    rows = []
    for task_id, msgs in by_task.items():
        if not msgs:
            continue
        total_tok = sum(m["tokens"] for m in msgs)
        for thr in thresholds:
            # Always protect the system prompt (msg_index == 0) and the
            # most recent assistant turn (last assistant message)
            last_asst_idx = max(
                (m["msg_index"] for m in msgs if m["role"] == "assistant"),
                default=-1,
            )
            evict_tok = sum(
                m["tokens"] for m in msgs
                if m["cites"] <= thr
                and m["msg_index"] != 0
                and m["msg_index"] != last_asst_idx
            )
            rows.append({
                "task_id": task_id,
                "threshold": thr,
                "total_tokens": total_tok,
                "evictable_tokens": evict_tok,
                "frac": evict_tok / max(total_tok, 1),
            })

    # Aggregate by threshold
    agg = defaultdict(list)
    for r in rows:
        agg[r["threshold"]].append(r["frac"])

    print("\n=== If you drop messages with cites <= threshold (preserving system + last assistant) ===")
    print(f"{'threshold':>10}  {'mean_frac_evicted':>18}  {'median':>10}  {'p90':>10}")
    summary = []
    for thr in thresholds:
        fracs = agg[thr]
        mean_f = statistics.mean(fracs)
        med_f = statistics.median(fracs)
        p90 = sorted(fracs)[int(0.9 * (len(fracs) - 1))]
        print(f"{thr:>10}  {mean_f*100:>16.1f}%  {med_f*100:>9.1f}%  {p90*100:>9.1f}%")
        summary.append({"threshold": thr, "mean_frac": mean_f, "median_frac": med_f, "p90_frac": p90})

    summary_path = OUT / "evictable_summary.csv"
    with open(summary_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold", "mean_frac_evictable", "median_frac_evictable", "p90_frac_evictable"])
        for s in summary:
            w.writerow([s["threshold"], f"{s['mean_frac']:.4f}", f"{s['median_frac']:.4f}", f"{s['p90_frac']:.4f}"])
    print(f"\nWrote {summary_path}")

    # ------------------------------------------------------------------
    # 2) Plots
    # ------------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Distribution of zero-cite-token fraction across traces
    zero_fracs = [r["frac"] for r in rows if r["threshold"] == 0]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(zero_fracs, bins=30, color="#1f77b4", edgecolor="black")
    ax.axvline(statistics.mean(zero_fracs), color="red", linestyle="--",
               label=f"mean = {statistics.mean(zero_fracs)*100:.1f}%")
    ax.axvline(statistics.median(zero_fracs), color="orange", linestyle="--",
               label=f"median = {statistics.median(zero_fracs)*100:.1f}%")
    ax.set_xlabel("Fraction of trace tokens with 0 downstream citations")
    ax.set_ylabel("Number of trajectories")
    ax.set_title("Hermes — token-fraction safely droppable per trajectory (threshold=0)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "evictable_distribution.png", dpi=140)
    plt.close(fig)
    print(f"Wrote {OUT / 'evictable_distribution.png'}")

    # Mean savings vs threshold
    means = [statistics.mean(agg[t]) for t in thresholds]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([str(t) for t in thresholds], [m * 100 for m in means], color="#2ca02c")
    for i, m in enumerate(means):
        ax.text(i, m * 100, f"{m*100:.1f}%", ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("Mean fraction of tokens evictable (%)")
    ax.set_xlabel("Citation threshold (drop if ≤ this many downstream citations)")
    ax.set_title("Hermes — context savings as we relax the deletion threshold")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "savings_by_threshold.png", dpi=140)
    plt.close(fig)
    print(f"Wrote {OUT / 'savings_by_threshold.png'}")

    # ------------------------------------------------------------------
    # 3) Concrete examples — load the actual messages and show some 0-cite
    # ------------------------------------------------------------------
    print("\n=== Concrete examples of 0-cite messages from real traces ===")
    print("(loading 5 traces to peek at the actual content)\n")
    trajs = load_hermes_agent_traces(config="kimi", max_traces=5)
    shown = 0
    for t in trajs:
        msgs = t.extra.get("trajectory_messages") or []
        ref = by_task.get(t.task_id, [])
        ref_by_idx = {r["msg_index"]: r for r in ref}
        for i, m in enumerate(msgs):
            r = ref_by_idx.get(i, {"cites": 0})
            if r["cites"] == 0 and m["role"] in ("tool", "assistant") and (m.get("_token_count") or 0) > 200:
                content = (m.get("content") or "").replace("\n", " ")
                print(f"  [{t.task_id[:8]}/msg{i}] role={m['role']:9s} tokens={m['_token_count']:5d} cites=0")
                print(f"    snippet: {content[:300]!r}")
                print()
                shown += 1
                if shown >= 6:
                    return


if __name__ == "__main__":
    main()
