"""Synthetic workload generators for KV-cache simulation."""

from __future__ import annotations

import random
from typing import Dict, List, Mapping, Sequence

DEFAULT_WORKLOAD_SEEDS: Dict[str, int] = {
    "short_prompt": 42,
    "long_context": 123,
    "multiturn": 7,
}

_SEED_OFFSETS: Dict[str, int] = {
    "short_prompt": 11,
    "long_context": 23,
    "multiturn": 37,
}


def generate_short_prompt_workload(
    num_requests: int = 2000,
    working_set_size: int = 96,
    locality_window: int = 12,
    reuse_probability: float = 0.85,
    seed: int = 42,
) -> List[str]:
    """Generates a short-prompt-like trace with strong recency locality."""
    if num_requests <= 0:
        return []

    rng = random.Random(seed)
    keys = [f"short_block_{i}" for i in range(working_set_size)]
    trace: List[str] = []

    for _ in range(num_requests):
        if trace and rng.random() < reuse_probability:
            start = max(0, len(trace) - locality_window)
            key = rng.choice(trace[start:])
        else:
            key = rng.choice(keys)
        trace.append(key)

    return trace


def generate_long_context_workload(
    num_requests: int = 4000,
    working_set_size: int = 1024,
    long_range_interval: int = 25,
    long_range_pool_size: int = 64,
    seed: int = 123,
) -> List[str]:
    """Generates a long-context trace with broad working set and periodic reuse."""
    if num_requests <= 0:
        return []

    rng = random.Random(seed)
    trace: List[str] = []
    long_range_pool = [f"long_anchor_{i}" for i in range(long_range_pool_size)]

    for i in range(num_requests):
        if i > 0 and i % long_range_interval == 0:
            key = rng.choice(long_range_pool)
        elif i > 0 and rng.random() < 0.2:
            key = rng.choice(long_range_pool)
        else:
            block_id = i % working_set_size
            key = f"long_block_{block_id}"
        trace.append(key)

    return trace


def generate_multiturn_workload(
    num_turns: int = 40,
    turn_length: int = 60,
    system_blocks: int = 12,
    per_turn_new_blocks: int = 35,
    seed: int = 7,
) -> List[str]:
    """Generates a multi-turn conversation trace with shared and turn-local reuse."""
    if num_turns <= 0 or turn_length <= 0:
        return []

    rng = random.Random(seed)
    shared = [f"system_block_{i}" for i in range(system_blocks)]
    trace: List[str] = []

    for turn_idx in range(num_turns):
        turn_keys = [f"turn_{turn_idx}_block_{i}" for i in range(per_turn_new_blocks)]

        # Every turn touches system blocks first (prefix reuse).
        trace.extend(shared)

        recent: List[str] = []
        remaining = max(0, turn_length - system_blocks)
        for _ in range(remaining):
            p = rng.random()
            if recent and p < 0.55:
                key = rng.choice(recent[-8:])
            elif p < 0.80:
                key = rng.choice(turn_keys)
            else:
                key = rng.choice(shared)
            trace.append(key)
            recent.append(key)

    return trace


def generate_default_workloads(seed: int | None = None) -> Dict[str, List[str]]:
    """Returns default named workloads with explicit deterministic seeds.

    Notes:
    - Calling `generate_default_workloads(seed=None)` matches calling each
      individual workload generator with its default parameters.
    - Providing a `seed` generates separate deterministic seeds per workload.
    """
    seeds = make_default_workload_seeds(seed)
    return {
        "short_prompt": generate_short_prompt_workload(seed=seeds["short_prompt"]),
        "long_context": generate_long_context_workload(seed=seeds["long_context"]),
        "multiturn": generate_multiturn_workload(seed=seeds["multiturn"]),
    }


def make_default_workload_seeds(seed: int | None = None) -> Dict[str, int]:
    """Builds deterministic per-workload seeds from one base seed."""
    if seed is None:
        return dict(DEFAULT_WORKLOAD_SEEDS)
    return {name: seed + offset for name, offset in _SEED_OFFSETS.items()}


def summarize_trace(trace: Sequence[str]) -> Dict[str, float]:
    """Computes lightweight summary statistics for one trace."""
    total_accesses = len(trace)
    unique_blocks = len(set(trace))
    reuse_ratio = ((total_accesses - unique_blocks) / total_accesses) if total_accesses else 0.0
    avg_access_per_block = (total_accesses / unique_blocks) if unique_blocks else 0.0
    return {
        "total_accesses": total_accesses,
        "unique_blocks": unique_blocks,
        "reuse_ratio": reuse_ratio,
        "avg_accesses_per_unique_block": avg_access_per_block,
    }


def summarize_workloads(
    workloads: Mapping[str, Sequence[str]],
) -> List[Dict[str, float | str]]:
    """Returns summary rows for multiple workloads."""
    rows: List[Dict[str, float | str]] = []
    for name, trace in workloads.items():
        row = {"workload": name}
        row.update(summarize_trace(trace))
        rows.append(row)
    return rows
