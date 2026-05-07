"""Experiment (C) from the agentic-Memento direction:

Quantify what fraction of agent-context tokens are tool observations vs
assistant messages vs user vs system. This sizes the prize for compressing
tool obs.

If tool_obs >= 70% of total agent-loop tokens, the agentic memento direction
has a big upper bound. If tool_obs < 40%, the prize is smaller and we should
focus on assistant compression instead.

Runs purely on CPU using existing Hermes loader. Output: a CSV summary +
console table. ~30 sec.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

from studies.lifetime_cost.pipeline.external_traces import load_hermes_agent_traces


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="kimi", choices=["kimi", "glm-5.1"])
    ap.add_argument("--max-traces", type=int, default=200)
    ap.add_argument("--out", default="studies/lifetime_cost/out/hermes/tool_obs_share.csv")
    args = ap.parse_args()

    print(f"Loading {args.max_traces} Hermes traces from {args.config}...")
    trajs = load_hermes_agent_traces(config=args.config, max_traces=args.max_traces)
    print(f"  got {len(trajs)} traces")

    # Per-trace breakdown
    rows = []
    by_role_total = defaultdict(int)
    by_trace_share = []
    for t in trajs:
        msgs = t.extra.get("trajectory_messages") or []
        if not msgs:
            continue
        per_role = defaultdict(int)
        for m in msgs:
            per_role[m["role"]] += int(m.get("_token_count") or 0)
        total = sum(per_role.values())
        if total == 0:
            continue
        for role, tok in per_role.items():
            by_role_total[role] += tok
        rows.append({
            "task_id": t.task_id,
            "n_msgs": len(msgs),
            "total_tokens": total,
            "tokens_system": per_role["system"],
            "tokens_user": per_role["user"],
            "tokens_assistant": per_role["assistant"],
            "tokens_tool": per_role["tool"],
            "share_tool_of_total": per_role["tool"] / total,
            "share_tool_of_nonsystem": per_role["tool"] / max(total - per_role["system"], 1),
            "share_assistant_of_nonsystem": per_role["assistant"] / max(total - per_role["system"], 1),
        })
        by_trace_share.append(per_role["tool"] / max(total - per_role["system"], 1))

    # Aggregate
    grand = sum(by_role_total.values())
    print("\n=== Aggregate token share by role (across all traces) ===")
    for role in ("system", "user", "assistant", "tool"):
        v = by_role_total.get(role, 0)
        print(f"  {role:10s}  {v:>11,} tokens  ({v/grand*100:5.1f}% of all)")

    nonsys = grand - by_role_total["system"]
    print(f"\n=== Excluding system prompt (which is mostly fixed tool definitions) ===")
    for role in ("user", "assistant", "tool"):
        v = by_role_total.get(role, 0)
        print(f"  {role:10s}  {v:>11,} tokens  ({v/nonsys*100:5.1f}% of agent loop)")

    print("\n=== Per-trace tool-share (of non-system tokens) ===")
    if by_trace_share:
        sorted_s = sorted(by_trace_share)
        n = len(sorted_s)
        print(f"  n={n}")
        print(f"  median = {statistics.median(sorted_s):.1%}")
        print(f"  mean   = {statistics.mean(sorted_s):.1%}")
        print(f"  p25    = {sorted_s[n//4]:.1%}")
        print(f"  p75    = {sorted_s[3*n//4]:.1%}")
        print(f"  p90    = {sorted_s[int(0.9*(n-1))]:.1%}")
        print(f"  max    = {max(sorted_s):.1%}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"\nWrote {args.out} ({len(rows)} rows)")

    # Decision summary
    median_share = statistics.median(by_trace_share) if by_trace_share else 0
    print("\n=== Decision ===")
    if median_share >= 0.70:
        print(f"  PASS: median tool_obs share = {median_share:.0%} ≥ 70%. Agentic-memento direction has big upside.")
    elif median_share >= 0.40:
        print(f"  WORTH PURSUING: median tool_obs share = {median_share:.0%}. Compressing tool obs gives meaningful but bounded savings.")
    else:
        print(f"  PIVOT: median tool_obs share = {median_share:.0%} < 40%. Compressing assistant messages may be more valuable.")


if __name__ == "__main__":
    main()
