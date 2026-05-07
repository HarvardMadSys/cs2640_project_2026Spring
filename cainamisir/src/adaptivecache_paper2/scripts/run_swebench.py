#!/usr/bin/env python3
"""CLI entry point for SWE-bench experiments with AdaptiveCache.

Examples:
    # Dry run — load instances and print config
    python scripts/run_swebench.py --dry-run

    # Run single instance with AdaptiveCache
    python scripts/run_swebench.py --instance-ids sympy__sympy-20590 --policy adaptive

    # Run first 5 instances with FIFO baseline
    python scripts/run_swebench.py --slice 0:5 --policy fifo --budget 64000

    # Full run with Anthropic API
    python scripts/run_swebench.py --model anthropic/claude-sonnet-4-5-20250929 --policy adaptive
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from harness.swe_config import SWEConfig
from harness.swe_runner import load_instances, run_experiment


def main():
    parser = argparse.ArgumentParser(description="Run SWE-bench with AdaptiveCache")

    # Dataset
    parser.add_argument("--dataset", default="lite", help="SWE-bench subset: lite, verified, full")
    parser.add_argument("--split", default="dev", help="Dataset split")
    parser.add_argument("--instance-ids", nargs="*", default=[], help="Specific instance IDs")
    parser.add_argument("--slice", default="", help="Slice spec (e.g., '0:5')")

    # Model
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-5-20250929", help="LiteLLM model name")

    # Cache
    parser.add_argument("--policy", default="adaptive", choices=["adaptive", "fifo", "lru", "window", "summarize", "compaction", "none"])
    parser.add_argument("--budget", type=int, default=64_000, help="Token budget")

    # Agent
    parser.add_argument("--max-steps", type=int, default=30, help="Max agent steps per instance")
    parser.add_argument("--cost-limit", type=float, default=3.0, help="Max cost per instance (USD)")

    # Execution
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers")
    parser.add_argument("--output", default="results", help="Output directory")
    parser.add_argument("--run-id", default="", help="Run identifier")

    # Debug
    parser.add_argument("--dry-run", action="store_true", help="Load instances and print config, don't run")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = SWEConfig(
        dataset=args.dataset,
        split=args.split,
        instance_ids=args.instance_ids,
        slice_spec=args.slice,
        model_name=args.model,
        cache_policy=args.policy,
        cache_budget=args.budget,
        max_steps=args.max_steps,
        cost_limit=args.cost_limit,
        workers=args.workers,
        output_dir=args.output,
        run_id=args.run_id,
    )

    if args.dry_run:
        print("=== Experiment Config ===")
        print(json.dumps({
            "dataset": config.dataset,
            "split": config.split,
            "model": config.model_name,
            "policy": config.cache_policy,
            "budget": config.cache_budget,
            "max_steps": config.max_steps,
        }, indent=2))
        print()

        instances = load_instances(config)
        print(f"Loaded {len(instances)} instances:")
        for inst in instances[:10]:
            print(f"  {inst['instance_id']}")
        if len(instances) > 10:
            print(f"  ... and {len(instances) - 10} more")
        return

    output_dir = run_experiment(config)
    print(f"\nResults saved to: {output_dir}")


if __name__ == "__main__":
    main()
