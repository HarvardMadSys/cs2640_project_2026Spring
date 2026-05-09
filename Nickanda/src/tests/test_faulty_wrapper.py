from __future__ import annotations

import time

from kvstore.models import Version
from kvstore.storage.faulty_wrapper import (
	CorrelatedFaultGroup,
	FaultSpec,
	FaultyStorageWrapper,
)
from kvstore.storage.memory_backend import InMemoryBackend


def _measure(fn) -> float:
	t0 = time.perf_counter()
	fn()
	return (time.perf_counter() - t0) * 1000.0


def test_steady_delay_applied_per_op() -> None:
	w = FaultyStorageWrapper(InMemoryBackend(), FaultSpec(steady_delay_ms=25.0))
	v = Version(logical_time=1, node_id="n1")
	ms = _measure(lambda: w.put("k", b"v", v))
	assert ms >= 20.0


def test_fail_slow_burst_increases_latency_on_trigger() -> None:
	spec = FaultSpec(
		steady_delay_ms=0.0,
		fail_slow_period_ops=3,
		fail_slow_burst_ms=40.0,
		fail_slow_burst_len=1,
	)
	w = FaultyStorageWrapper(InMemoryBackend(), spec)
	v = Version(logical_time=1, node_id="n1")
	fast_ms = [_measure(lambda i=i: w.put(f"k{i}", b"v", v)) for i in range(2)]
	burst_ms = _measure(lambda: w.put("kburst", b"v", v))
	assert burst_ms > max(fast_ms) + 20.0


def test_correlated_group_adds_shared_delay() -> None:
	group = CorrelatedFaultGroup(active=False, correlated_delay_ms=30.0)
	w = FaultyStorageWrapper(InMemoryBackend(), FaultSpec(), correlated_group=group)
	v = Version(logical_time=1, node_id="n1")

	quiet_ms = _measure(lambda: w.put("a", b"x", v))
	group.set(active=True)
	noisy_ms = _measure(lambda: w.put("b", b"y", v))
	group.set(active=False)
	assert noisy_ms > quiet_ms + 20.0


def test_probabilistic_stall_at_100_pct_always_stalls() -> None:
	spec = FaultSpec(stall_probability=1.0, stall_ms=25.0, seed=42)
	w = FaultyStorageWrapper(InMemoryBackend(), spec)
	v = Version(logical_time=1, node_id="n1")
	ms = _measure(lambda: w.put("stall_key", b"v", v))
	assert ms >= 20.0
