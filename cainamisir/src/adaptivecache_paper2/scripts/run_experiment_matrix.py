#!/usr/bin/env python3
"""Run the AdaptiveCache experiment matrix on SWE-bench Lite.

Runs all policy × budget combinations on a slice of instances,
collecting traces for comparison.

Usage:
    # Smoke test: 2 instances × 3 configs
    python scripts/run_experiment_matrix.py --slice 0:2 --smoke

    # Small experiment: 5 instances × all configs
    python scripts/run_experiment_matrix.py --slice 0:5

    # Full experiment: 20 instances × all configs
    python scripts/run_experiment_matrix.py --slice 0:20
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("experiment_matrix")

# Experiment configurations
FULL_MATRIX = [
    {"policy": "none",       "budget": 999_999},  # ReAct baseline — full context
    {"policy": "compaction", "budget": 8_000},     # Anthropic server-side compaction
    {"policy": "compaction", "budget": 16_000},
    {"policy": "summarize",  "budget": 8_000},     # LLM summarization (OpenHands-style)
    {"policy": "summarize",  "budget": 16_000},
    {"policy": "fifo",       "budget": 8_000},     # Naive baseline
    {"policy": "adaptive",   "budget": 8_000},     # AdaptiveCache
    {"policy": "adaptive",   "budget": 16_000},
]

SMOKE_MATRIX = [
    {"policy": "none",       "budget": 999_999},
    {"policy": "summarize",  "budget": 8_000},
    {"policy": "fifo",       "budget": 8_000},
    {"policy": "adaptive",   "budget": 8_000},
]


def run_config(policy: str, budget: int, slice_spec: str, model: str,
               output_base: str, max_steps: int, cost_limit: float) -> dict:
    """Run a single configuration."""
    run_id = f"{policy}_{budget}"
    output_dir = Path(output_base) / run_id

    logger.info("=== Running: policy=%s budget=%d ===", policy, budget)

    cmd = [
        sys.executable, "scripts/run_swebench.py",
        "--model", model,
        "--policy", policy,
        "--budget", str(budget),
        "--slice", slice_spec,
        "--split", "test",
        "--output", str(output_dir),
        "--run-id", run_id,
        "--max-steps", str(max_steps),
        "--cost-limit", str(cost_limit),
    ]

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    elapsed = time.time() - start

    if result.returncode != 0:
        logger.error("FAILED: %s\n%s", run_id, result.stderr[-500:] if result.stderr else "")
    else:
        logger.info("DONE: %s (%.1fs)", run_id, elapsed)

    return {
        "policy": policy,
        "budget": budget,
        "run_id": run_id,
        "elapsed_seconds": elapsed,
        "returncode": result.returncode,
        "output_dir": str(output_dir / run_id),
    }


def collect_results(output_base: str) -> list[dict]:
    """Collect results from all runs into a summary."""
    results = []
    base = Path(output_base)

    # Find all preds.json files recursively
    for preds_file in sorted(base.rglob("preds.json")):
        run_dir = preds_file.parent
        if True:

            preds = json.loads(preds_file.read_text())
            config_file = run_dir / "experiment_config.json"
            config = json.loads(config_file.read_text()) if config_file.exists() else {}

            for instance_id, pred in preds.items():
                traj_file = run_dir / instance_id / f"{instance_id}.traj.json"
                traj = {}
                if traj_file.exists():
                    traj = json.loads(traj_file.read_text())

                info = traj.get("info", {})
                cache_trace = traj.get("cache_trace", [])
                model_stats = info.get("model_stats", {})

                # Aggregate real cache stats across all steps
                total_cache_read = sum(t.get("cache_read_tokens", 0) for t in cache_trace)
                total_prompt = sum(t.get("prompt_tokens", 0) for t in cache_trace)
                total_evicted = sum(t.get("messages_evicted", 0) for t in cache_trace)
                avg_cache_rate = total_cache_read / total_prompt if total_prompt > 0 else 0

                results.append({
                    "instance_id": instance_id,
                    "policy": config.get("config", {}).get("cache_policy", run_dir.name.split("_")[0]),
                    "budget": config.get("config", {}).get("cache_budget", ""),
                    "exit_status": info.get("exit_status", ""),
                    "has_patch": bool(pred.get("model_patch", "")),
                    "cost": model_stats.get("instance_cost", 0),
                    "api_calls": model_stats.get("api_calls", 0),
                    "total_prompt_tokens": total_prompt,
                    "total_cache_read": total_cache_read,
                    "cache_hit_rate": avg_cache_rate,
                    "total_msgs_evicted": total_evicted,
                    "num_steps": len(cache_trace),
                })

    return results


def print_summary(results: list[dict]) -> None:
    """Print a summary table of results."""
    if not results:
        print("No results found.")
        return

    # Group by policy+budget
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        key = f"{r['policy']}/{r['budget']}"
        groups[key].append(r)

    print(f"\n{'Config':<20} {'N':>3} {'Patch':>5} {'Avg$':>8} {'Steps':>6} {'PromptTok':>10} {'CacheRead':>10} {'HitRate':>8} {'Evicted':>8}")
    print("-" * 90)

    for key in sorted(groups.keys()):
        runs = groups[key]
        n = len(runs)
        patches = sum(1 for r in runs if r["has_patch"])
        avg_cost = sum(r["cost"] for r in runs) / n
        avg_steps = sum(r["num_steps"] for r in runs) / n
        avg_prompt = sum(r.get("total_prompt_tokens", 0) for r in runs) / n
        avg_cache = sum(r.get("total_cache_read", 0) for r in runs) / n
        avg_hit = sum(r.get("cache_hit_rate", 0) for r in runs) / n
        avg_evict = sum(r.get("total_msgs_evicted", 0) for r in runs) / n

        print(f"{key:<20} {n:>3} {patches:>5} ${avg_cost:>7.3f} {avg_steps:>6.0f} {avg_prompt:>10.0f} {avg_cache:>10.0f} {avg_hit:>7.1%} {avg_evict:>8.0f}")


def main():
    parser = argparse.ArgumentParser(description="Run AdaptiveCache experiment matrix")
    parser.add_argument("--slice", default="0:5", help="Instance slice (e.g., '0:5')")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-5-20250929")
    parser.add_argument("--output", default="results/matrix", help="Output base directory")
    parser.add_argument("--max-steps", type=int, default=0, help="0 = no limit")
    parser.add_argument("--cost-limit", type=float, default=0, help="0 = no limit")
    parser.add_argument("--smoke", action="store_true", help="Run smoke test (4 configs instead of 8)")
    parser.add_argument("--parallel", action="store_true", help="Run all configs in parallel")
    parser.add_argument("--summary-only", action="store_true", help="Just print summary of existing results")

    args = parser.parse_args()

    if args.summary_only:
        results = collect_results(args.output)
        print_summary(results)
        return

    matrix = SMOKE_MATRIX if args.smoke else FULL_MATRIX
    total = len(matrix)

    logger.info("Experiment matrix: %d configurations on instances [%s]", total, args.slice)
    logger.info("Model: %s, Max steps: %d, Cost limit: $%.2f", args.model, args.max_steps, args.cost_limit)

    run_results = []

    if args.parallel:
        # Run all configs in parallel
        import concurrent.futures
        logger.info("Running %d configs in PARALLEL", total)

        def _run(config):
            return run_config(
                policy=config["policy"],
                budget=config["budget"],
                slice_spec=args.slice,
                model=args.model,
                output_base=args.output,
                max_steps=args.max_steps,
                cost_limit=args.cost_limit,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=total) as pool:
            futures = {pool.submit(_run, c): c for c in matrix}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                run_results.append(result)
                logger.info("Completed: %s (%.0fs)", result["run_id"], result["elapsed_seconds"])
    else:
        # Sequential
        for i, config in enumerate(matrix, 1):
            logger.info("[%d/%d] Starting %s budget=%d", i, total, config["policy"], config["budget"])
            result = run_config(
                policy=config["policy"],
                budget=config["budget"],
                slice_spec=args.slice,
                model=args.model,
                output_base=args.output,
                max_steps=args.max_steps,
            cost_limit=args.cost_limit,
        )
        run_results.append(result)

    # Save run manifest
    manifest_path = Path(args.output) / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(run_results, indent=2))

    # Collect and print summary
    results = collect_results(args.output)
    print_summary(results)

    # Save CSV
    csv_path = Path(args.output) / "results.csv"
    if results:
        import csv
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        logger.info("Results saved to %s", csv_path)


if __name__ == "__main__":
    main()
