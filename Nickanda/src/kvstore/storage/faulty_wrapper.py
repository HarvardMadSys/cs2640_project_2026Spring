from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from threading import Lock

from kvstore.models import ValueRecord, Version
from kvstore.storage.base import DigestRow, StorageBackend


@dataclass
class CorrelatedFaultGroup:
	"""Shared state used by multiple FaultyStorageWrappers to degrade together.

	When ``active`` is True, every wrapper that holds a reference applies its
	configured ``correlated_delay_ms`` on top of its own per-op delays. The
	experiment harness can flip this to simulate a datacenter event (e.g.
	shared power/thermal issue) hitting several nodes simultaneously.
	"""

	active: bool = False
	correlated_delay_ms: float = 0.0
	_lock: Lock = field(default_factory=Lock)

	def set(self, active: bool, delay_ms: float | None = None) -> None:
		with self._lock:
			self.active = active
			if delay_ms is not None:
				self.correlated_delay_ms = delay_ms

	def read(self) -> tuple[bool, float]:
		with self._lock:
			return self.active, self.correlated_delay_ms


@dataclass
class FaultSpec:
	"""Configuration for FaultyStorageWrapper fault injection.

	- steady_delay_ms: every op sleeps this long (straggler).
	- fail_slow_period_ops: trigger a latency burst every N ops.
	- fail_slow_burst_ms: burst amplitude.
	- fail_slow_burst_len: number of consecutive ops affected by one burst.
	- stall_probability: probability any single op gets a full I/O stall.
	- stall_ms: duration of a stall event.
	- seed: optional RNG seed for reproducibility.
	"""

	steady_delay_ms: float = 0.0
	fail_slow_period_ops: int = 0
	fail_slow_burst_ms: float = 0.0
	fail_slow_burst_len: int = 0
	stall_probability: float = 0.0
	stall_ms: float = 0.0
	seed: int | None = None


class FaultyStorageWrapper(StorageBackend):
	"""Wrap any StorageBackend and inject realistic storage-layer faults.

	The wrapper uses its own counters; it does not assume anything about the
	underlying backend. It also cooperates with an optional
	``CorrelatedFaultGroup`` so multiple nodes can degrade together.
	"""

	def __init__(
		self,
		inner: StorageBackend,
		spec: FaultSpec | None = None,
		correlated_group: CorrelatedFaultGroup | None = None,
	) -> None:
		self._inner = inner
		self._spec = spec or FaultSpec()
		self._group = correlated_group
		self._lock = Lock()
		self._op_counter = 0
		self._burst_remaining = 0
		self._rng = random.Random(self._spec.seed)

	def set_spec(self, spec: FaultSpec) -> None:
		with self._lock:
			self._spec = spec
			if spec.seed is not None:
				self._rng = random.Random(spec.seed)

	def _inject_delay(self) -> None:
		with self._lock:
			self._op_counter += 1
			spec = self._spec
			op_idx = self._op_counter
			if (
				spec.fail_slow_period_ops > 0
				and spec.fail_slow_burst_len > 0
				and op_idx % spec.fail_slow_period_ops == 0
			):
				self._burst_remaining = spec.fail_slow_burst_len
			in_burst = self._burst_remaining > 0
			if in_burst:
				self._burst_remaining -= 1
			burst_ms = spec.fail_slow_burst_ms if in_burst else 0.0
			steady_ms = spec.steady_delay_ms
			stall_ms = 0.0
			if spec.stall_probability > 0 and self._rng.random() < spec.stall_probability:
				stall_ms = spec.stall_ms
		correlated_ms = 0.0
		if self._group is not None:
			active, c_ms = self._group.read()
			if active:
				correlated_ms = c_ms
		total = steady_ms + burst_ms + stall_ms + correlated_ms
		if total > 0:
			time.sleep(total / 1000.0)

	def get(self, key: str) -> ValueRecord | None:
		self._inject_delay()
		return self._inner.get(key)

	def put(self, key: str, value: bytes, version: Version) -> bool:
		self._inject_delay()
		return self._inner.put(key, value, version)

	def delete(self, key: str, version: Version) -> bool:
		self._inject_delay()
		return self._inner.delete(key, version)

	def scan(self) -> list[DigestRow]:
		return self._inner.scan()

	def max_logical_time(self) -> int:
		return self._inner.max_logical_time()

	def close(self) -> None:
		self._inner.close()
