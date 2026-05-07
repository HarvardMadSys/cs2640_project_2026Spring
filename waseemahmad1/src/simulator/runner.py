"""Workload runner utilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable

from simulator.cache import KVCacheSimulator
from simulator.metrics import cache_hit_rate, miss_rate
from simulator.policies import EvictionPolicy, FIFOPolicy, LRUPolicy


@dataclass
class SimulationResult:
    """Structured result for one simulation run."""

    workload: str
    policy: str
    capacity: int
    total_accesses: int
    hits: int
    misses: int
    hit_rate: float
    miss_rate: float
    recomputation_cost: int

    def to_dict(self) -> Dict[str, float]:
        """Converts result to dictionary."""
        return asdict(self)


def validate_result(result: SimulationResult, miss_cost: int = 1) -> None:
    """Performs lightweight sanity checks on simulation output."""
    assert result.hits + result.misses == result.total_accesses
    assert 0.0 <= result.hit_rate <= 1.0
    assert 0.0 <= result.miss_rate <= 1.0
    if result.total_accesses > 0:
        assert abs((result.hit_rate + result.miss_rate) - 1.0) < 1e-9
    assert result.recomputation_cost == result.misses * miss_cost


def make_policy(policy_name: str) -> EvictionPolicy:
    """Creates policy instance by name."""
    policy_name = policy_name.lower()
    if policy_name == "lru":
        return LRUPolicy()
    if policy_name == "fifo":
        return FIFOPolicy()
    raise ValueError(f"Unknown policy: {policy_name}")


def run_trace(
    trace: Iterable[str],
    policy_name: str,
    capacity: int,
    workload_name: str = "unknown",
    miss_cost: int = 1,
) -> SimulationResult:
    """Runs one access trace and returns simulation stats."""
    policy = make_policy(policy_name)
    sim = KVCacheSimulator(
        capacity=capacity,
        policy=policy,
        miss_recompute_cost=miss_cost,
    )

    for key in trace:
        sim.access(key)

    result = SimulationResult(
        workload=workload_name,
        policy=policy_name.lower(),
        capacity=capacity,
        total_accesses=sim.total_accesses,
        hits=sim.hits,
        misses=sim.misses,
        hit_rate=cache_hit_rate(sim.hits, sim.total_accesses),
        miss_rate=miss_rate(sim.misses, sim.total_accesses),
        recomputation_cost=sim.recomputation_cost,
    )
    validate_result(result, miss_cost=miss_cost)
    return result
