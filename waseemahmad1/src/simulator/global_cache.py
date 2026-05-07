"""Shared global KV-cache simulator with optional adaptive cost-aware eviction."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, Iterable, List, Optional

from simulator.global_workload import AccessEvent
from simulator.metrics import cache_hit_rate, miss_rate


@dataclass
class GlobalCacheEntry:
    """Represents one entry in the shared global cache."""

    key: str
    size: int
    creation_time: int
    last_access_time: int
    access_count: int
    recompute_cost: int
    shared_prefix: bool
    protected: bool
    last_request_id: str


@dataclass
class GlobalSimulationResult:
    """Structured summary for one shared-cache run."""

    workload: str
    policy: str
    capacity: int
    total_accesses: int
    hits: int
    misses: int
    hit_rate: float
    miss_rate: float
    recomputation_cost: int
    unique_requests: int

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


class SharedGlobalKVCacheSimulator:
    """Simulates a shared global cache across concurrent requests."""

    def __init__(
        self,
        capacity: int,
        policy_name: str,
        block_size: int = 1,
        miss_window_size: int = 256,
        adaptive_protected_fraction: float = 0.20,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        if not (0.0 <= adaptive_protected_fraction < 1.0):
            raise ValueError("adaptive_protected_fraction must be in [0.0, 1.0)")

        policy_name = policy_name.lower()
        if policy_name not in {"lru", "fifo", "adaptive"}:
            raise ValueError("policy_name must be one of: lru, fifo, adaptive")

        self.capacity = capacity
        self.policy_name = policy_name
        self.block_size = block_size
        self.adaptive_protected_fraction = adaptive_protected_fraction

        self.entries: Dict[str, GlobalCacheEntry] = {}
        self.time = 0

        self.total_accesses = 0
        self.hits = 0
        self.misses = 0
        self.recomputation_cost = 0
        self._requests_seen: set[str] = set()
        self._key_seen_counts: Dict[str, int] = {}

        self._miss_history: Deque[int] = deque(maxlen=miss_window_size)
        self._pressure_state = "low"
        self.skipped_admissions = 0

    def access(
        self,
        request_id: str,
        key: str,
        recompute_cost: int = 1,
        shared_prefix: bool = False,
    ) -> bool:
        """Accesses a key in the shared cache. Returns True on hit."""
        self.time += 1
        self.total_accesses += 1
        self._requests_seen.add(request_id)
        prior_seen_count = self._key_seen_counts.get(key, 0)
        self._key_seen_counts[key] = prior_seen_count + 1

        if key in self.entries:
            entry = self.entries[key]
            entry.last_access_time = self.time
            entry.access_count += 1
            entry.last_request_id = request_id
            entry.recompute_cost = max(entry.recompute_cost, recompute_cost)
            entry.shared_prefix = entry.shared_prefix or shared_prefix
            entry.protected = entry.protected or shared_prefix or recompute_cost >= 3
            self.hits += 1
            self._miss_history.append(0)
            self._update_pressure_state()
            return True

        self.misses += 1
        self.recomputation_cost += recompute_cost
        self._miss_history.append(1)
        self._update_pressure_state()
        if self.policy_name == "adaptive":
            if not self._should_admit_adaptive(
                recompute_cost=recompute_cost,
                shared_prefix=shared_prefix,
                prior_seen_count=prior_seen_count,
            ):
                self.skipped_admissions += 1
                return False

        self._insert(request_id, key, recompute_cost, shared_prefix)
        return False

    def _insert(
        self,
        request_id: str,
        key: str,
        recompute_cost: int,
        shared_prefix: bool,
    ) -> None:
        if len(self.entries) >= self.capacity:
            victim = self._choose_victim()
            if victim is None:
                raise RuntimeError("no victim found while cache is full")
            self.entries.pop(victim, None)

        protected = (
            shared_prefix
            or recompute_cost >= 3
            or (
                self.policy_name == "adaptive"
                and self._pressure_state == "high"
                and recompute_cost >= 2
            )
        )
        self.entries[key] = GlobalCacheEntry(
            key=key,
            size=self.block_size,
            creation_time=self.time,
            last_access_time=self.time,
            access_count=1,
            recompute_cost=recompute_cost,
            shared_prefix=shared_prefix,
            protected=protected,
            last_request_id=request_id,
        )

    def _recent_miss_rate(self) -> float:
        count = len(self._miss_history)
        if count == 0:
            return 0.0
        return sum(self._miss_history) / count

    def _update_pressure_state(self) -> None:
        """Updates adaptive pressure mode using simple hysteresis thresholds."""
        miss_rate = self._recent_miss_rate()
        state = self._pressure_state

        if state == "low":
            if miss_rate >= 0.70:
                self._pressure_state = "high"
            elif miss_rate >= 0.50:
                self._pressure_state = "medium"
            return

        if state == "medium":
            if miss_rate >= 0.72:
                self._pressure_state = "high"
            elif miss_rate <= 0.35:
                self._pressure_state = "low"
            return

        # high
        if miss_rate <= 0.55:
            self._pressure_state = "medium"

    def _adaptive_entry_value(
        self,
        recompute_cost: int,
        shared_prefix: bool,
        prior_seen_count: int,
    ) -> float:
        return (
            float(recompute_cost)
            + (2.0 if shared_prefix else 0.0)
            + (1.0 if prior_seen_count > 0 else 0.0)
        )

    def _retention_value(self, entry: GlobalCacheEntry) -> float:
        """Approximates how valuable a resident entry is to keep."""
        age = self.time - entry.last_access_time
        return (
            float(entry.recompute_cost)
            + (1.0 if entry.shared_prefix else 0.0)
            + (0.5 * min(entry.access_count, 4))
            - (0.04 * age)
        )

    def _should_admit_adaptive(
        self,
        recompute_cost: int,
        shared_prefix: bool,
        prior_seen_count: int,
    ) -> bool:
        """Admission control for adaptive mode to reduce cache pollution."""
        if len(self.entries) < self.capacity:
            return True

        value = self._adaptive_entry_value(
            recompute_cost=recompute_cost,
            shared_prefix=shared_prefix,
            prior_seen_count=prior_seen_count,
        )

        if self._pressure_state == "low":
            return True
        resident_values = [self._retention_value(entry) for entry in self.entries.values()]
        min_resident = min(resident_values) if resident_values else 0.0
        if self._pressure_state == "medium":
            return value >= (min_resident - 0.4)
        return value >= (min_resident + 0.1)

    def _choose_victim(self) -> Optional[str]:
        if not self.entries:
            return None

        items = list(self.entries.items())

        if self.policy_name == "lru":
            victim_key, _ = min(
                items,
                key=lambda item: (item[1].last_access_time, item[1].creation_time, item[0]),
            )
            return victim_key

        if self.policy_name == "fifo":
            victim_key, _ = min(
                items,
                key=lambda item: (item[1].creation_time, item[1].last_access_time, item[0]),
            )
            return victim_key

        # adaptive cost-aware policy
        return self._choose_adaptive_victim(items)

    def _choose_adaptive_victim(self, items: List[tuple[str, GlobalCacheEntry]]) -> str:
        # Increase cost-awareness and protected retention when misses are high.
        if self._pressure_state == "high":
            cost_weight = 1.0
            shared_bonus = 0.6
            protected_bonus = 0.25
            frequency_weight = 0.7
            age_weight = 1.9
            protected_target = max(0.05, self.adaptive_protected_fraction - 0.10)
        elif self._pressure_state == "medium":
            cost_weight = 1.3
            shared_bonus = 1.0
            protected_bonus = 0.6
            frequency_weight = 0.8
            age_weight = 1.4
            protected_target = max(0.10, self.adaptive_protected_fraction - 0.05)
        else:
            cost_weight = 1.4
            shared_bonus = 1.0
            protected_bonus = 0.6
            frequency_weight = 0.8
            age_weight = 1.0
            protected_target = self.adaptive_protected_fraction

        protected_count = sum(1 for _, entry in items if entry.protected)
        protected_limit = max(0, int(round(self.capacity * protected_target)))

        if protected_count <= protected_limit:
            candidates = [(key, entry) for key, entry in items if not entry.protected]
            if not candidates:
                candidates = items
        else:
            candidates = items

        def eviction_score(entry: GlobalCacheEntry) -> float:
            age = self.time - entry.last_access_time
            recently_used = age <= 128
            score = (
                (age_weight * age)
                - (frequency_weight * entry.access_count)
                - (cost_weight * entry.recompute_cost)
                - (shared_bonus if entry.shared_prefix and recently_used else 0.0)
                - (protected_bonus if entry.protected and recently_used else 0.0)
            )
            return score

        victim_key, _ = max(
            candidates,
            key=lambda item: (
                eviction_score(item[1]),
                -item[1].creation_time,
                item[0],
            ),
        )
        return victim_key

    def summary(self, workload_name: str = "shared_global") -> Dict[str, float]:
        """Returns run summary metrics."""
        result = GlobalSimulationResult(
            workload=workload_name,
            policy=self.policy_name,
            capacity=self.capacity,
            total_accesses=self.total_accesses,
            hits=self.hits,
            misses=self.misses,
            hit_rate=cache_hit_rate(self.hits, self.total_accesses),
            miss_rate=miss_rate(self.misses, self.total_accesses),
            recomputation_cost=self.recomputation_cost,
            unique_requests=len(self._requests_seen),
        )
        validate_global_result(result)
        return result.to_dict()


def validate_global_result(result: GlobalSimulationResult, miss_cost: int | None = None) -> None:
    """Checks accounting invariants for shared-cache results."""
    assert result.hits + result.misses == result.total_accesses
    assert 0.0 <= result.hit_rate <= 1.0
    assert 0.0 <= result.miss_rate <= 1.0
    if result.total_accesses > 0:
        assert abs((result.hit_rate + result.miss_rate) - 1.0) < 1e-9
    if miss_cost is not None:
        assert result.recomputation_cost == result.misses * miss_cost


def run_shared_events(
    events: Iterable[AccessEvent],
    policy_name: str,
    capacity: int,
    workload_name: str = "shared_global",
) -> GlobalSimulationResult:
    """Runs a stream of concurrent access events on the shared cache."""
    simulator = SharedGlobalKVCacheSimulator(capacity=capacity, policy_name=policy_name)
    request_ids: set[str] = set()

    for request_id, key, recompute_cost, shared_prefix in events:
        request_ids.add(request_id)
        simulator.access(
            request_id=request_id,
            key=key,
            recompute_cost=recompute_cost,
            shared_prefix=shared_prefix,
        )

    result = GlobalSimulationResult(
        workload=workload_name,
        policy=policy_name.lower(),
        capacity=capacity,
        total_accesses=simulator.total_accesses,
        hits=simulator.hits,
        misses=simulator.misses,
        hit_rate=cache_hit_rate(simulator.hits, simulator.total_accesses),
        miss_rate=miss_rate(simulator.misses, simulator.total_accesses),
        recomputation_cost=simulator.recomputation_cost,
        unique_requests=len(request_ids),
    )
    validate_global_result(result)
    return result
