"""Run the cheap first experiment: detect prefix-cache cliffs from
existing trajectories. Zero model calls.

Usage:
    python -m studies.lifetime_cost.scripts.run_replay \\
        --config studies/lifetime_cost/configs/cliff_replay.yaml

Or programmatically:
    from studies.lifetime_cost.pipeline.replay import load_trajectories, analyze_trajectory
    ...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from studies.lifetime_cost.pipeline.analysis import plot_cliff
from studies.lifetime_cost.pipeline.replay import (
    aggregate_cliffs,
    analyze_trajectory,
    load_trajectories,
    report_to_dict,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    reports = []
    for results_dir in cfg["results_dirs"]:
        for traj in load_trajectories(Path(results_dir)):
            try:
                rep = analyze_trajectory(traj, block_chars=cfg.get("block_chars", 1024))
                reports.append(rep)
            except Exception as e:
                print(f"  skip {traj.get('source_file')}: {e}")

    print(f"Loaded {len(reports)} trajectories.")

    # Per-trajectory JSON
    detail_path = out / "per_trajectory.json"
    with open(detail_path, "w") as f:
        json.dump([report_to_dict(r) for r in reports], f, indent=2, default=str)
    print(f"Wrote {detail_path}")

    # Aggregate
    agg = aggregate_cliffs(reports)
    agg_path = out / "aggregate.json"
    with open(agg_path, "w") as f:
        json.dump(agg, f, indent=2)
    print(f"Wrote {agg_path}")
    print(f"Median cliff: {agg.get('median_cliff')}, p90: {agg.get('p90_cliff')}")

    # Plot top-N most cliff-y trajectories
    top_n = cfg.get("plot_top_n", 12)
    sorted_reports = sorted(
        reports,
        key=lambda r: max((c.cliff_ratio_blocks for c in r.compactions), default=0),
        reverse=True,
    )[:top_n]

    if sorted_reports:
        plot_path = out / "cliff_top.png"
        plot_cliff(sorted_reports, plot_path,
                   title=f"Top-{top_n} cliff trajectories — median cliff {agg.get('median_cliff')}")
        print(f"Wrote {plot_path}")

    # Decision-gate output
    gate_a_pass = (agg.get("median_cliff") or 0) > 3.0
    print(f"\nDecision gate A (median cliff > 3x): {'PASS' if gate_a_pass else 'FAIL'}")


if __name__ == "__main__":
    main()
