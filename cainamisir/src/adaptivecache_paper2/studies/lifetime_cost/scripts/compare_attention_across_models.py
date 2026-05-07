"""Compare attention extraction across model sizes (Qwen3-0.6B/1.7B/4B/8B).

Tests whether the patterns we found at 0.6B replicate at larger scales:
  1. Per-role attention ranking (user > assistant > tool)
  2. Position primacy (first 10% gets >>> middle)
  3. Big tool obs get more attention than small ones (counterintuitive but consistent)

Reads:  studies/lifetime_cost/out/hermes/attention_scores*.csv (one per model)
Writes: studies/lifetime_cost/out/hermes/attention_across_models.png
        studies/lifetime_cost/out/hermes/attention_across_models.csv
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

from studies.lifetime_cost.pipeline.external_traces import load_hermes_agent_traces


HERMES_DIR = Path("studies/lifetime_cost/out/hermes")


def load_attention(csv_path: Path) -> list[dict]:
    rows = []
    if not csv_path.exists():
        return rows
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "task_id": r["task_id"],
                    "msg_index": int(r["msg_index"]),
                    "role": r["role"],
                    "tokens": int(r["n_msg_tokens"]),
                    "attn_per_tok": float(r["attn_per_token"]),
                    "attn_total": float(r["attn_total"]),
                })
            except Exception:
                pass
    return rows


def main():
    # Find all attention CSVs
    csv_paths = sorted(HERMES_DIR.glob("attention_scores*.csv"))
    if not csv_paths:
        print("No attention CSVs found")
        return

    # Pre-load trace lengths for position calculation
    trajs = load_hermes_agent_traces(config="kimi", max_traces=30)
    trace_lens = {t.task_id: len(t.extra.get("trajectory_messages") or []) for t in trajs}

    # Load each model
    by_model: dict[str, list[dict]] = {}
    for p in csv_paths:
        # extract model name from path
        name = p.stem.replace("attention_scores__", "").replace("_", "/")
        if name == "attention_scores":
            name = "Qwen/Qwen3-0.6B"   # the original baseline file
        rows = load_attention(p)
        if rows:
            by_model[name] = rows
            print(f"  {name:30s}  {len(rows):5d} rows from {p.name}")

    print()
    print("=== Per-role median attention/token, by model ===")
    print(f"{'model':<30} {'system':>10} {'user':>10} {'assistant':>10} {'tool':>10}")
    table_rows = []
    for model, rows in sorted(by_model.items()):
        per_role = defaultdict(list)
        for r in rows:
            per_role[r["role"]].append(r["attn_per_tok"])
        cells = []
        for role in ("system", "user", "assistant", "tool"):
            v = per_role.get(role, [])
            cells.append(statistics.median(v) if v else None)
        cells_fmt = [f"{c:.4f}" if c is not None else "-" for c in cells]
        print(f"{model:<30} {cells_fmt[0]:>10} {cells_fmt[1]:>10} {cells_fmt[2]:>10} {cells_fmt[3]:>10}")
        table_rows.append({"model": model, "median_system": cells[0], "median_user": cells[1],
                           "median_assistant": cells[2], "median_tool": cells[3]})

    print()
    print("=== Position primacy: median attn/tok by relative position bucket ===")
    print(f"{'model':<30} {'first 10%':>10} {'10-25%':>10} {'25-50%':>10} {'50-75%':>10} {'75-100%':>10}")
    for model, rows in sorted(by_model.items()):
        rows_pos = []
        for r in rows:
            ml = trace_lens.get(r["task_id"], 0)
            if ml > 0:
                rows_pos.append((r["msg_index"] / ml, r["attn_per_tok"]))
        cells = []
        for lo, hi in [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)]:
            bucket = [a for p, a in rows_pos if lo <= p < hi]
            cells.append(statistics.median(bucket) if bucket else None)
        cells_fmt = [f"{c:.4f}" if c is not None else "-" for c in cells]
        print(f"{model:<30}", " ".join(f"{c:>10}" for c in cells_fmt))

    print()
    print("=== Tool size effect: median attn/tok by tool size bucket ===")
    print(f"{'model':<30} {'<200 tok':>10} {'200-2000':>10} {'>=2000':>10}")
    for model, rows in sorted(by_model.items()):
        small = [r["attn_per_tok"] for r in rows if r["role"] == "tool" and r["tokens"] < 200]
        mid = [r["attn_per_tok"] for r in rows if r["role"] == "tool" and 200 <= r["tokens"] < 2000]
        big = [r["attn_per_tok"] for r in rows if r["role"] == "tool" and r["tokens"] >= 2000]
        cells = [statistics.median(s) if s else None for s in (small, mid, big)]
        cells_fmt = [f"{c:.4f}" if c is not None else "-" for c in cells]
        print(f"{model:<30}", " ".join(f"{c:>10}" for c in cells_fmt))

    # Plot: position primacy across models
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5))

    bins = [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)]
    bin_labels = ["0-10%", "10-25%", "25-50%", "50-75%", "75-100%"]
    colors = plt.cm.tab10.colors
    for i, (model, rows) in enumerate(sorted(by_model.items())):
        rows_pos = []
        for r in rows:
            ml = trace_lens.get(r["task_id"], 0)
            if ml > 0:
                rows_pos.append((r["msg_index"] / ml, r["attn_per_tok"]))
        ys = []
        for lo, hi in bins:
            bucket = [a for p, a in rows_pos if lo <= p < hi]
            ys.append(statistics.median(bucket) if bucket else 0)
        ax1.plot(bin_labels, ys, "-o", label=model.split("/")[-1], color=colors[i % len(colors)])
    ax1.set_ylabel("Median attention per token (log)")
    ax1.set_xlabel("Position in trace")
    ax1.set_title("Position primacy across model sizes")
    ax1.set_yscale("log")
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Per-role bars
    roles = ["user", "assistant", "tool"]
    import numpy as np
    bw = 0.8 / max(len(by_model), 1)
    x = np.arange(len(roles))
    for i, (model, rows) in enumerate(sorted(by_model.items())):
        per_role = defaultdict(list)
        for r in rows:
            per_role[r["role"]].append(r["attn_per_tok"])
        ys = [statistics.median(per_role.get(role, [0])) if per_role.get(role) else 0 for role in roles]
        ax2.bar(x + i * bw, ys, bw, label=model.split("/")[-1], color=colors[i % len(colors)])
    ax2.set_xticks(x + bw * (len(by_model) - 1) / 2)
    ax2.set_xticklabels(roles)
    ax2.set_ylabel("Median attention per token")
    ax2.set_yscale("log")
    ax2.set_title("Per-role attention across model sizes")
    ax2.legend(fontsize=9)
    ax2.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    out_path = HERMES_DIR / "attention_across_models.png"
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"\nWrote {out_path}")

    # CSV summary
    csv_out = HERMES_DIR / "attention_across_models.csv"
    with open(csv_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "n_rows",
                    "median_user", "median_assistant", "median_tool",
                    "median_first_10pct", "median_25_50pct", "median_75_100pct",
                    "median_tool_small", "median_tool_mid", "median_tool_big"])
        for model, rows in sorted(by_model.items()):
            per_role = defaultdict(list)
            for r in rows:
                per_role[r["role"]].append(r["attn_per_tok"])
            rows_pos = []
            for r in rows:
                ml = trace_lens.get(r["task_id"], 0)
                if ml > 0:
                    rows_pos.append((r["msg_index"] / ml, r["attn_per_tok"]))
            small = [r["attn_per_tok"] for r in rows if r["role"]=="tool" and r["tokens"]<200]
            mid_t = [r["attn_per_tok"] for r in rows if r["role"]=="tool" and 200<=r["tokens"]<2000]
            big = [r["attn_per_tok"] for r in rows if r["role"]=="tool" and r["tokens"]>=2000]
            def med(xs): return statistics.median(xs) if xs else None
            w.writerow([
                model, len(rows),
                med(per_role["user"]), med(per_role["assistant"]), med(per_role["tool"]),
                med([a for p, a in rows_pos if p < 0.1]),
                med([a for p, a in rows_pos if 0.25 <= p < 0.5]),
                med([a for p, a in rows_pos if p >= 0.75]),
                med(small), med(mid_t), med(big),
            ])
    print(f"Wrote {csv_out}")


if __name__ == "__main__":
    main()
