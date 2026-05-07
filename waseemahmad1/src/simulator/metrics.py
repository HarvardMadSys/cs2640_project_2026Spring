"""Metrics helpers for KV-cache simulation results."""

from __future__ import annotations

from typing import Dict


def cache_hit_rate(hits: int, total_accesses: int) -> float:
    """Computes cache hit rate."""
    if total_accesses <= 0:
        return 0.0
    return hits / total_accesses


def miss_rate(misses: int, total_accesses: int) -> float:
    """Computes cache miss rate."""
    if total_accesses <= 0:
        return 0.0
    return misses / total_accesses


def recomputation_cost(misses: int, miss_cost: int = 1) -> int:
    """Computes total recomputation cost."""
    return misses * miss_cost


def format_summary(summary: Dict[str, float]) -> str:
    """Formats a human-readable summary string."""
    return (
        f"accesses={summary['total_accesses']}, "
        f"hits={summary['hits']}, misses={summary['misses']}, "
        f"hit_rate={summary['hit_rate']:.4f}, miss_rate={summary['miss_rate']:.4f}, "
        f"recompute={summary['recomputation_cost']}"
    )
