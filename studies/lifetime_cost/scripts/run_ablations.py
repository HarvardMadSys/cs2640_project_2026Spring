"""Run the ablation sweeps and produce per-sweep summary plots.

Usage:
    python -m studies.lifetime_cost.scripts.run_ablations \\
        --config studies/lifetime_cost/configs/ablations.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from studies.lifetime_cost.pipeline.ablations import planned_sweeps, sweep
from studies.lifetime_cost.pipeline.analysis import plot_lifetime_cost, plot_pareto
from studies.lifetime_cost.pipeline.harness import load_trajectories
from studies.lifetime_cost.pipeline.pricing import PriceSheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--only", help="Run only one sweep (target_policy:param)", default=None)
    ap.add_argument("--skip-run", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out_dir = Path(cfg["out_dir"])

    sweeps = planned_sweeps()
    if args.only:
        target, param = args.only.split(":")
        sweeps = [s for s in sweeps if s["target_policy"] == target and s["param"] == param]
        if not sweeps:
            raise SystemExit(f"No matching sweep for {args.only}")

    for s in sweeps:
        sub = f"{s['target_policy']}__{s['param']}"
        print(f"\n=== Sweep: {sub} over {s['values']} ===")
        if not args.skip_run:
            sweep(cfg, target_policy=s["target_policy"], param=s["param"],
                  values=s["values"], out_subdir=f"sweeps/{sub}")

        # Plot per-value lifetime cost
        sheet = PriceSheet()
        sweep_root = out_dir / "sweeps" / sub
        for v_dir in sorted(sweep_root.glob("*=*")):
            trajs = load_trajectories(v_dir)
            if not trajs:
                continue
            figs = v_dir / "figures"
            plot_lifetime_cost(trajs, sheet, figs / "lifetime_cost.png",
                               title=f"{sub}={v_dir.name.split('=')[1]}")
            plot_pareto(trajs, sheet, figs / "pareto.png")
        print(f"  figures under {sweep_root}")


if __name__ == "__main__":
    main()
