"""Concurrent request workload generators for shared global cache experiments."""

from __future__ import annotations

import random
from typing import Dict, List, Mapping, Sequence, Tuple

AccessEvent = Tuple[str, str, int, bool]


def is_shared_prefix_key(key: str) -> bool:
    """Returns True when a key belongs to a shared prefix block namespace."""
    return key.startswith("shared_prefix_block_")


def recompute_cost_for_key(key: str) -> int:
    """Heuristic recomputation cost by block type."""
    if key.startswith("shared_prefix_block_"):
        return 3
    if key.startswith("long_anchor_"):
        return 4
    if key.startswith("long_block_"):
        return 2
    return 1


def generate_concurrent_request_traces(
    num_requests: int = 8,
    request_length: int = 500,
    shared_prefix_blocks: int = 24,
    unique_blocks_per_request: int = 256,
    shared_prefix_reuse_prob: float = 0.20,
    recency_reuse_prob: float = 0.45,
    seed: int = 0,
) -> Dict[str, List[str]]:
    """Generates per-request traces with shared-prefix and local temporal reuse."""
    if num_requests <= 0 or request_length <= 0:
        return {}

    rng = random.Random(seed)
    shared = [f"shared_prefix_block_{i}" for i in range(shared_prefix_blocks)]
    traces: Dict[str, List[str]] = {}

    for req_idx in range(num_requests):
        request_id = f"req_{req_idx}"
        unique = [f"req_{req_idx}_block_{i}" for i in range(unique_blocks_per_request)]

        trace: List[str] = []
        trace.extend(shared[: min(shared_prefix_blocks, request_length)])

        while len(trace) < request_length:
            p = rng.random()
            if len(trace) > shared_prefix_blocks and p < recency_reuse_prob:
                window = trace[max(0, len(trace) - 16) :]
                key = rng.choice(window)
            elif p < recency_reuse_prob + shared_prefix_reuse_prob and shared:
                key = rng.choice(shared)
            else:
                key = rng.choice(unique)
            trace.append(key)

        traces[request_id] = trace

    return traces


def interleave_request_traces(
    traces: Mapping[str, Sequence[str]],
    mode: str = "round_robin",
    seed: int = 0,
) -> List[Tuple[str, str]]:
    """Interleaves per-request traces into a single concurrent access stream."""
    mode = mode.lower()
    if mode not in {"round_robin", "random"}:
        raise ValueError("mode must be 'round_robin' or 'random'")

    request_ids = list(traces.keys())
    if not request_ids:
        return []

    if mode == "round_robin":
        max_len = max(len(t) for t in traces.values())
        merged: List[Tuple[str, str]] = []
        for idx in range(max_len):
            for request_id in request_ids:
                trace = traces[request_id]
                if idx < len(trace):
                    merged.append((request_id, trace[idx]))
        return merged

    rng = random.Random(seed)
    positions = {request_id: 0 for request_id in request_ids}
    active = set(request_ids)
    merged = []

    while active:
        request_id = rng.choice(sorted(active))
        pos = positions[request_id]
        trace = traces[request_id]
        merged.append((request_id, trace[pos]))
        positions[request_id] += 1
        if positions[request_id] >= len(trace):
            active.remove(request_id)

    return merged


def traces_to_events(accesses: Sequence[Tuple[str, str]]) -> List[AccessEvent]:
    """Converts interleaved access pairs to typed access events."""
    events: List[AccessEvent] = []
    for request_id, key in accesses:
        events.append(
            (
                request_id,
                key,
                recompute_cost_for_key(key),
                is_shared_prefix_key(key),
            )
        )
    return events


def generate_shared_global_events(
    num_requests: int = 8,
    request_length: int = 500,
    shared_prefix_blocks: int = 24,
    unique_blocks_per_request: int = 256,
    shared_prefix_reuse_prob: float = 0.20,
    recency_reuse_prob: float = 0.45,
    interleave_mode: str = "round_robin",
    seed: int = 0,
) -> List[AccessEvent]:
    """Generates one concurrent access stream for shared global cache experiments."""
    traces = generate_concurrent_request_traces(
        num_requests=num_requests,
        request_length=request_length,
        shared_prefix_blocks=shared_prefix_blocks,
        unique_blocks_per_request=unique_blocks_per_request,
        shared_prefix_reuse_prob=shared_prefix_reuse_prob,
        recency_reuse_prob=recency_reuse_prob,
        seed=seed,
    )
    accesses = interleave_request_traces(traces, mode=interleave_mode, seed=seed + 999)
    return traces_to_events(accesses)


def generate_shifted_global_events(
    seed: int = 0,
    shift_level: str = "moderate",
) -> Tuple[List[AccessEvent], int]:
    """Generates two-phase workload with a distribution shift.

    `shift_level` controls how severe the phase-2 degradation is:
    - moderate: meaningful but not extreme shift
    - hard: stronger long-context/weak-locality pressure

    Returns:
      events: concatenated events for both phases
      split_index: index where phase-2 begins
    """
    shift_level = shift_level.lower()
    if shift_level not in {"moderate", "hard"}:
        raise ValueError("shift_level must be 'moderate' or 'hard'")

    phase1 = generate_shared_global_events(
        num_requests=8,
        request_length=300,
        shared_prefix_blocks=32,
        unique_blocks_per_request=120,
        shared_prefix_reuse_prob=0.30,
        recency_reuse_prob=0.55,
        interleave_mode="round_robin",
        seed=seed,
    )

    if shift_level == "moderate":
        phase2 = generate_shared_global_events(
            num_requests=8,
            request_length=300,
            shared_prefix_blocks=8,
            unique_blocks_per_request=1200,
            shared_prefix_reuse_prob=0.05,
            recency_reuse_prob=0.20,
            interleave_mode="round_robin",
            seed=seed + 10_000,
        )
    else:
        phase2 = generate_shared_global_events(
            num_requests=8,
            request_length=300,
            shared_prefix_blocks=6,
            unique_blocks_per_request=2000,
            shared_prefix_reuse_prob=0.03,
            recency_reuse_prob=0.12,
            interleave_mode="round_robin",
            seed=seed + 20_000,
        )

    return phase1 + phase2, len(phase1)


def summarize_events(events: Sequence[AccessEvent]) -> Dict[str, float]:
    """Computes simple summary stats for event streams."""
    if not events:
        return {
            "total_accesses": 0,
            "unique_blocks": 0,
            "unique_requests": 0,
            "reuse_ratio": 0.0,
            "shared_prefix_fraction": 0.0,
            "avg_recompute_cost": 0.0,
        }

    keys = [event[1] for event in events]
    total_accesses = len(keys)
    unique_blocks = len(set(keys))
    unique_requests = len(set(event[0] for event in events))
    shared_prefix_fraction = sum(1 for event in events if event[3]) / total_accesses
    avg_recompute_cost = sum(event[2] for event in events) / total_accesses
    reuse_ratio = (total_accesses - unique_blocks) / total_accesses

    return {
        "total_accesses": total_accesses,
        "unique_blocks": unique_blocks,
        "unique_requests": unique_requests,
        "reuse_ratio": reuse_ratio,
        "shared_prefix_fraction": shared_prefix_fraction,
        "avg_recompute_cost": avg_recompute_cost,
    }
