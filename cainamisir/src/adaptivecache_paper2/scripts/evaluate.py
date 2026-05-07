#!/usr/bin/env python3
"""Evaluate SWE-bench predictions using sb-cli or local harness.

Usage:
    # Evaluate with sb-cli (cloud, recommended)
    python scripts/evaluate.py results/run_001/preds.json --method sb-cli

    # Evaluate locally (requires Docker + 120GB disk)
    python scripts/evaluate.py results/run_001/preds.json --method local

    # Just summarize cache traces (no evaluation)
    python scripts/evaluate.py results/run_001 --traces-only
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def summarize_cache_traces(output_dir: Path) -> None:
    """Print cache trace summary from experiment results."""
    from harness.swe_runner import collect_cache_traces

    traces = collect_cache_traces(output_dir)
    if not traces:
        print("No cache traces found.")
        return

    print(f"=== Cache Trace Summary ({len(traces)} instances) ===\n")

    total_tokens_all = []
    pinned_tokens_all = []
    hit_rates = []

    for t in traces:
        stats = t.get("cache_final_stats", {})
        if stats:
            total_tokens_all.append(stats.get("total_tokens", 0))
            pinned_tokens_all.append(stats.get("pinned_tokens", 0))
            hit_rates.append(stats.get("cache_hit_estimate", 0))

    if total_tokens_all:
        avg_total = sum(total_tokens_all) / len(total_tokens_all)
        avg_pinned = sum(pinned_tokens_all) / len(pinned_tokens_all)
        avg_hit = sum(hit_rates) / len(hit_rates)
        print(f"  Avg total tokens:  {avg_total:,.0f}")
        print(f"  Avg pinned tokens: {avg_pinned:,.0f}")
        print(f"  Avg cache hit est: {avg_hit:.1%}")
        print()

    # Per-instance breakdown
    for t in traces[:10]:
        iid = t["instance_id"]
        trace = t.get("cache_trace", [])
        stats = t.get("cache_final_stats", {})
        steps = len(trace)
        hit = stats.get("cache_hit_estimate", 0)
        print(f"  {iid}: {steps} steps, {hit:.1%} cache hit est")

    if len(traces) > 10:
        print(f"  ... and {len(traces) - 10} more")


def evaluate_sb_cli(preds_path: Path, dataset: str = "lite") -> None:
    """Submit predictions to sb-cli for cloud evaluation."""
    print(f"Submitting {preds_path} to sb-cli...")
    cmd = ["sb-cli", "evaluate", str(preds_path), "--dataset", dataset]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("sb-cli not found. Install with: pip install sb-cli")
        print("Then authenticate with: sb-cli login")
        sys.exit(1)


def evaluate_local(preds_path: Path, dataset: str = "lite") -> None:
    """Run local swebench evaluation (requires Docker)."""
    dataset_mapping = {
        "lite": "princeton-nlp/SWE-bench_Lite",
        "verified": "princeton-nlp/SWE-bench_Verified",
    }
    dataset_name = dataset_mapping.get(dataset, dataset)

    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--predictions_path", str(preds_path),
        "--max_workers", "4",
        "--run_id", preds_path.parent.name,
    ]
    print(f"Running local evaluation: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Evaluate SWE-bench predictions")
    parser.add_argument("path", help="Path to preds.json or output directory")
    parser.add_argument("--method", choices=["sb-cli", "local"], default="sb-cli")
    parser.add_argument("--dataset", default="lite", help="SWE-bench subset")
    parser.add_argument("--traces-only", action="store_true", help="Only summarize cache traces")

    args = parser.parse_args()
    path = Path(args.path)

    if args.traces_only or path.is_dir():
        output_dir = path if path.is_dir() else path.parent
        summarize_cache_traces(output_dir)
        if args.traces_only:
            return

    preds_path = path if path.suffix == ".json" else path / "preds.json"
    if not preds_path.exists():
        print(f"Predictions file not found: {preds_path}")
        sys.exit(1)

    if args.method == "sb-cli":
        evaluate_sb_cli(preds_path, args.dataset)
    else:
        evaluate_local(preds_path, args.dataset)


if __name__ == "__main__":
    main()
