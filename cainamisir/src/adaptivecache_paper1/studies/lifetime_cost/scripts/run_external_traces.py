"""Run zero-token cliff + lifetime-cost analysis on publicly released
trace dumps (Applied Compute workloads; optionally Hermes / TRAIL).

Usage:
    # First fetch the workload files (one-time):
    python -m studies.lifetime_cost.scripts.run_external_traces \\
        --config studies/lifetime_cost/configs/external_traces.yaml \\
        --fetch

    # Then run the analysis:
    python -m studies.lifetime_cost.scripts.run_external_traces \\
        --config studies/lifetime_cost/configs/external_traces.yaml
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path

import yaml

from studies.lifetime_cost.pipeline.analysis import plot_cliff, plot_lifetime_cost
from studies.lifetime_cost.pipeline.external_traces import (
    evaluate_policies_on_traces,
    load_applied_compute_workload,
    load_hermes_agent_traces,
    load_trail_traces,
)
from studies.lifetime_cost.pipeline.pricing import PriceSheet
from studies.lifetime_cost.pipeline.replay import analyze_trajectory


AC_BASE_URL = "https://raw.githubusercontent.com/Applied-Compute/trie/main/workloads/"
AC_FILES = ["agentic_coding_8k.jsonl", "code_qa_8k.jsonl", "office_work_8k.jsonl"]


def fetch_applied_compute(out_dir: Path):
    """Download Applied Compute's three public workloads."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname in AC_FILES:
        target = out_dir / fname
        if target.exists():
            print(f"  exists: {target}")
            continue
        url = AC_BASE_URL + fname
        print(f"  fetching: {url}")
        urllib.request.urlretrieve(url, target)
    print(f"Done. Files at {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--fetch", action="store_true",
                    help="Download Applied Compute workloads, then exit.")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    if args.fetch:
        ac_dir = Path("studies/lifetime_cost/external_traces/applied_compute")
        fetch_applied_compute(ac_dir)
        return

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    trajectories = []
    if cfg["sources"]["applied_compute"]["enabled"]:
        for spec in cfg["sources"]["applied_compute"]["workloads"]:
            p = Path(spec["path"])
            if not p.exists():
                print(f"  missing: {p}  (run with --fetch first)")
                continue
            trajs = load_applied_compute_workload(
                p,
                benchmark_name=spec["benchmark"],
                max_traces=cfg["sources"]["applied_compute"].get("max_traces_per_workload"),
            )
            trajectories.extend(trajs)
            print(f"  loaded {len(trajs)} traces from {p.name}")

    if cfg["sources"]["hermes"]["enabled"]:
        trajs = load_hermes_agent_traces(
            config=cfg["sources"]["hermes"].get("config", "kimi"),
            max_traces=cfg["sources"]["hermes"]["max_traces"],
        )
        trajectories.extend(trajs)
        print(f"  loaded {len(trajs)} Hermes traces")

    if cfg["sources"]["trail"]["enabled"]:
        trajs = load_trail_traces(max_traces=cfg["sources"]["trail"]["max_traces"])
        trajectories.extend(trajs)
        print(f"  loaded {len(trajs)} TRAIL traces")

    print(f"\nTotal: {len(trajectories)} trajectories.")
    if not trajectories:
        print("Nothing to analyze. Did you run --fetch?")
        return

    # ------------------------------------------------------------------
    # Cliff analysis on the recorded trajectories (no policy applied)
    # ------------------------------------------------------------------
    sheet = PriceSheet()
    raw_reports = []
    for t in trajectories:
        td = t.to_dict()
        td["task_id"] = t.task_id
        td["model"] = t.model
        td["policy"] = t.policy
        td["source_file"] = "external"
        td["step_messages"] = [s["messages_in"] for s in td["steps"]]
        try:
            raw_reports.append(analyze_trajectory(td))
        except Exception as e:
            print(f"  skip {t.task_id}: {e}")

    # ------------------------------------------------------------------
    # Simulate each policy on each trajectory; compute cost under each model
    # ------------------------------------------------------------------
    print("\nSimulating policies...")
    result = evaluate_policies_on_traces(
        trajectories,
        cfg["policies"],
        sheet=sheet,
        cost_models=cfg["cost_models"],
        budget_tokens=cfg["budget_tokens"],
        hard_budget_tokens=cfg["hard_budget_tokens"],
        summarizer_mode=cfg.get("summarizer_mode", "llm"),
    )

    # CSV dump
    csv_path = out / "results.csv"
    with open(csv_path, "w", newline="") as f:
        if result["rows"]:
            w = csv.DictWriter(f, fieldnames=list(result["rows"][0].keys()))
            w.writeheader()
            w.writerows(result["rows"])
    print(f"Wrote {csv_path}")

    # JSON dump (full)
    with open(out / "results.json", "w") as f:
        json.dump(result["rows"], f, indent=2)

    # ------------------------------------------------------------------
    # Aggregate: cost-per-task by (benchmark, policy, cost_model)
    # ------------------------------------------------------------------
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "cost_sum": 0.0, "comp_sum": 0})
    for row in result["rows"]:
        key = (row["benchmark"], row["policy"], row["cost_model"])
        agg[key]["n"] += 1
        agg[key]["cost_sum"] += row["lifetime_cost"]
        agg[key]["comp_sum"] += row["n_compactions"]

    summary_path = out / "summary.csv"
    with open(summary_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "policy", "cost_model", "n", "mean_cost_per_task", "mean_compactions"])
        for (b, p, cm), v in sorted(agg.items()):
            w.writerow([b, p, cm, v["n"], f"{v['cost_sum']/v['n']:.6f}", f"{v['comp_sum']/v['n']:.2f}"])
    print(f"Wrote {summary_path}")

    # ------------------------------------------------------------------
    # Plots
    # ------------------------------------------------------------------
    figs = out / "figures"
    figs.mkdir(parents=True, exist_ok=True)

    # Lifetime cost bars per benchmark
    by_bench = defaultdict(list)
    for pol_name, sims in result["simulated"].items():
        for sim in sims:
            by_bench[sim.benchmark].append(sim)
    for bench, sims in by_bench.items():
        plot_lifetime_cost(
            sims, sheet, figs / f"lifetime_cost__{bench}.png",
            cost_models=cfg["cost_models"],
            title=f"Lifetime $/task on {bench}",
        )

    # Cliff plot of the worst raw trajectories
    sorted_reports = sorted(
        raw_reports,
        key=lambda r: r.n_steps,
        reverse=True,
    )[:8]
    if sorted_reports:
        plot_cliff(sorted_reports, figs / "cliff_recorded.png",
                   title="Recorded trajectory shapes (no compaction)")

    print(f"\nFigures written under {figs}")
    print("Done.")


if __name__ == "__main__":
    main()
