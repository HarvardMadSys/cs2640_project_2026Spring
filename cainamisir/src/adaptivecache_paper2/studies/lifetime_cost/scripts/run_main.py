"""Run the main experiment matrix and produce all paper figures.

Usage:
    python -m studies.lifetime_cost.scripts.run_main \\
        --config studies/lifetime_cost/configs/main.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from studies.lifetime_cost.pipeline.analysis import (
    plot_lifetime_cost,
    plot_pareto,
    write_summary_table,
)
from studies.lifetime_cost.pipeline.harness import load_trajectories, run_matrix
from studies.lifetime_cost.pipeline.pricing import PriceSheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--skip-run", action="store_true",
                    help="Skip experiment runs, only re-render plots from out_dir")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    out_dir = Path(cfg["out_dir"])

    if not args.skip_run:
        run_matrix(cfg)

    sheet = PriceSheet()
    trajs = load_trajectories(out_dir)
    print(f"Loaded {len(trajs)} trajectories from {out_dir}")

    figures = out_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    cost_models = sheet.names()

    plot_lifetime_cost(trajs, sheet, figures / "lifetime_cost.png", cost_models=cost_models)
    plot_pareto(trajs, sheet, figures / "pareto.png")
    write_summary_table(trajs, sheet, figures / "summary.csv", cost_models=cost_models)
    print(f"Figures + summary at {figures}")


if __name__ == "__main__":
    main()
