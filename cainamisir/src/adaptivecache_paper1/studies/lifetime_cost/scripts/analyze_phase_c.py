"""Phase C analysis script.

Reads the trajectories from out_dir, computes per-policy stats, identifies
Pareto-frontier policies, and prints a clean summary table. Also dumps a
JSON summary for later programmatic comparison.

Usage:
  python -m studies.lifetime_cost.scripts.analyze_phase_c \
      --out_dir studies/lifetime_cost/out/phase_c_swebench_live
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from studies.lifetime_cost.pipeline.harness import load_trajectories
from studies.lifetime_cost.pipeline.pricing import PriceSheet, cost_of


def _cost_total(traj_cost, *, exclude_compaction: bool) -> float:
    if exclude_compaction:
        return traj_cost.total - traj_cost.compaction_dollars
    return traj_cost.total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--cost_model", default="qwen/qwen3-30b-a3b",
                    help="Pricing column to use for $-comparisons.")
    ap.add_argument("--exclude_compaction_costs", action="store_true",
                    help="Treat compaction LLM-call cost (summarizer / scorer) "
                         "as 'infrastructure overhead' and exclude from the "
                         "$/resolved comparison. Useful for separating agent-cost "
                         "from policy-cost.")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    sheet = PriceSheet()
    trajs = load_trajectories(out_dir)
    if not trajs:
        print(f"No trajectories in {out_dir}")
        return

    by_pol = defaultdict(list)
    for t in trajs:
        by_pol[t.policy].append(t)

    rows = []
    for pol in sorted(by_pol):
        ts = by_pol[pol]
        n = len(ts)
        n_res = sum(1 for t in ts if t.resolved)
        resolve_rate = n_res / n if n else 0.0
        n_comp = sum(t.num_compactions for t in ts)

        per_task_costs = []
        per_task_costs_agent_only = []
        per_task_steps = []
        per_task_max_p = []
        per_task_overflow = 0
        per_task_compaction_dollars = []
        for t in ts:
            c = cost_of(t, sheet, override_model=args.cost_model)
            per_task_costs.append(c.total)
            per_task_costs_agent_only.append(c.total - c.compaction_dollars)
            per_task_compaction_dollars.append(c.compaction_dollars)
            per_task_steps.append(len(t.steps))
            mp = max((s.usage.prompt_tokens for s in t.steps), default=0)
            per_task_max_p.append(mp)
            # overflow detection: any step whose response.content starts with overflow marker
            for s in t.steps:
                content = (s.response.content or "")
                if "context overflow" in content[:80].lower():
                    per_task_overflow += 1
                    break

        mean_cost = sum(per_task_costs) / max(n, 1)
        mean_cost_agent_only = sum(per_task_costs_agent_only) / max(n, 1)
        mean_compaction_cost = sum(per_task_compaction_dollars) / max(n, 1)
        cost_per_resolved = (sum(per_task_costs) / n_res) if n_res else float("inf")
        cost_per_resolved_agent_only = (sum(per_task_costs_agent_only) / n_res) if n_res else float("inf")
        mean_steps = sum(per_task_steps) / max(n, 1)
        mean_max_p = sum(per_task_max_p) / max(n, 1)

        rows.append({
            "policy": pol,
            "n_tasks": n,
            "n_resolved": n_res,
            "resolve_rate": resolve_rate,
            "n_compactions_total": n_comp,
            "mean_cost": mean_cost,
            "mean_cost_agent_only": mean_cost_agent_only,
            "mean_compaction_cost": mean_compaction_cost,
            "cost_per_resolved": cost_per_resolved,
            "cost_per_resolved_agent_only": cost_per_resolved_agent_only,
            "mean_steps": mean_steps,
            "mean_max_prompt_tokens": mean_max_p,
            "n_overflow_endings": per_task_overflow,
        })

    # Sort by primary cost-per-resolved metric (excl-compaction if flag set)
    sort_key = "cost_per_resolved_agent_only" if args.exclude_compaction_costs else "cost_per_resolved"
    rows.sort(key=lambda r: r[sort_key])

    excl_str = "  [agent-cost only, compaction excluded]" if args.exclude_compaction_costs else ""
    print(f"\n=== Phase C analysis (cost_model={args.cost_model}){excl_str} ===\n")
    print(f"{'policy':20s} {'res':>5s} {'resolve%':>9s} {'comps':>6s} "
          f"{'mean$_full':>11s} {'mean$_ag':>10s} {'$/res_full':>11s} {'$/res_ag':>10s} {'steps':>6s} {'maxp':>7s}")
    print("-" * 110)
    for r in rows:
        print(f"{r['policy']:20s} "
              f"{r['n_resolved']}/{r['n_tasks']:1d}  "
              f"{r['resolve_rate']*100:8.1f}% "
              f"{r['n_compactions_total']:6d} "
              f"{r['mean_cost']:11.5f} "
              f"{r['mean_cost_agent_only']:10.5f} "
              f"{r['cost_per_resolved']:11.5f} "
              f"{r['cost_per_resolved_agent_only']:10.5f} "
              f"{r['mean_steps']:6.1f} "
              f"{r['mean_max_prompt_tokens']:7.0f}")

    # Pareto: a policy is Pareto-dominated iff some other policy has BOTH
    # >= resolve_rate AND <= cost_per_resolved.
    print("\n=== Pareto frontier (>= resolve, <= $/resolved) ===")
    on_frontier = []
    for i, r in enumerate(rows):
        dominated_by = []
        for j, s in enumerate(rows):
            if i == j: continue
            if (s["resolve_rate"] >= r["resolve_rate"]
                and s["cost_per_resolved"] <= r["cost_per_resolved"]
                and (s["resolve_rate"] > r["resolve_rate"]
                     or s["cost_per_resolved"] < r["cost_per_resolved"])):
                dominated_by.append(s["policy"])
        if not dominated_by:
            on_frontier.append(r["policy"])
            print(f"  {r['policy']}  (resolve={r['resolve_rate']*100:.1f}%, $/res={r['cost_per_resolved']:.5f})")
        else:
            print(f"  [DOMINATED] {r['policy']}  by: {', '.join(dominated_by)}")

    # Headline: did anything beat `none`?
    none_row = next((r for r in rows if r["policy"] == "none"), None)
    if none_row:
        beats_none = []
        for r in rows:
            if r["policy"] == "none": continue
            if (r["resolve_rate"] >= none_row["resolve_rate"]
                and r["cost_per_resolved"] < none_row["cost_per_resolved"]):
                savings = (none_row["cost_per_resolved"] - r["cost_per_resolved"]) / none_row["cost_per_resolved"]
                beats_none.append((r["policy"], savings))
        print("\n=== Headline: policies Pareto-beating `none` ===")
        if beats_none:
            for pol, sav in beats_none:
                print(f"  ✓ {pol}: -{sav*100:.1f}% cost, equal-or-better resolve rate")
        else:
            print("  (none — no policy strictly dominates `none` on this benchmark)")

    # Dump structured for downstream
    summary_path = out_dir / "figures" / "phase_c_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump({"rows": rows, "frontier": on_frontier,
                   "cost_model": args.cost_model}, f, indent=2)
    print(f"\nSummary written to {summary_path}")


if __name__ == "__main__":
    main()
