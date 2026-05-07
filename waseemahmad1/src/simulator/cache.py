"""Core KV-cache simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from simulator.policies import EvictionPolicy


@dataclass
class CacheEntry:
    """Represents one token block in cache."""

    key: str
    size: int
    creation_time: int
    last_access_time: int
    access_count: int


class KVCacheSimulator:
    """Simulates a fixed-size KV cache with a pluggable eviction policy."""

    def __init__(
        self,
        capacity: int,
        policy: EvictionPolicy,
        miss_recompute_cost: int = 1,
        block_size: int = 1,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if miss_recompute_cost < 0:
            raise ValueError("miss_recompute_cost must be non-negative")
        if block_size <= 0:
            raise ValueError("block_size must be positive")

        self.capacity = capacity
        self.policy = policy
        self.miss_recompute_cost = miss_recompute_cost
        self.block_size = block_size

        self.entries: Dict[str, CacheEntry] = {}
        self.time = 0

        self.total_accesses = 0
        self.hits = 0
        self.misses = 0
        self.recomputation_cost = 0

    def access(self, key: str) -> bool:
        """Accesses a block key and inserts on miss.

        Returns True for hit, False for miss.
        """
        self.time += 1
        self.total_accesses += 1

        if key in self.entries:
            entry = self.entries[key]
            entry.last_access_time = self.time
            entry.access_count += 1
            self.hits += 1
            self.policy.on_access(key, self.time)
            return True

        self.misses += 1
        self.recomputation_cost += self.miss_recompute_cost
        self._insert(key)
        return False

    def _insert(self, key: str) -> None:
        if len(self.entries) >= self.capacity:
            victim = self.policy.choose_victim()
            if victim is None:
                raise RuntimeError("policy returned no victim while cache is full")
            self.entries.pop(victim, None)
            self.policy.on_evict(victim)

        entry = CacheEntry(
            key=key,
            size=self.block_size,
            creation_time=self.time,
            last_access_time=self.time,
            access_count=1,
        )
        self.entries[key] = entry
        self.policy.on_insert(key, self.time)

    def reset(self) -> None:
        """Resets cache contents and counters."""
        self.entries.clear()
        self.time = 0
        self.total_accesses = 0
        self.hits = 0
        self.misses = 0
        self.recomputation_cost = 0

    def get_summary(self) -> dict:
        """Returns a summary of simulator metrics."""
        hit_rate = self.hits / self.total_accesses if self.total_accesses else 0.0
        miss_rate = self.misses / self.total_accesses if self.total_accesses else 0.0
        return {
            "capacity": self.capacity,
            "total_accesses": self.total_accesses,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "miss_rate": miss_rate,
            "recomputation_cost": self.recomputation_cost,
        }
