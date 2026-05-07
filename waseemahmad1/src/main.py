"""CLI entry point for quick KV-cache simulation runs."""

from __future__ import annotations

import argparse

from simulator.metrics import format_summary
from simulator.runner import run_trace
from simulator.workload import generate_default_workloads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KV-cache simulation.")
    parser.add_argument(
        "--workload",
        choices=["short_prompt", "long_context", "multiturn"],
        default="short_prompt",
        help="Workload trace to run.",
    )
    parser.add_argument(
        "--policy",
        choices=["lru", "fifo"],
        default="lru",
        help="Eviction policy.",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=128,
        help="Cache capacity in number of blocks.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional base seed for deterministic default workloads.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workloads = generate_default_workloads(seed=args.seed)
    trace = workloads[args.workload]
    result = run_trace(
        trace=trace,
        policy_name=args.policy,
        capacity=args.capacity,
        workload_name=args.workload,
    )
    print(format_summary(result.to_dict()))


if __name__ == "__main__":
    main()
