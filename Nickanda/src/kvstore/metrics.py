from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock


def _percentile(values: list[float], p: float) -> float:
	if not values:
		return 0.0
	ordered = sorted(values)
	idx = int(round((p / 100.0) * (len(ordered) - 1)))
	return ordered[max(0, min(idx, len(ordered) - 1))]


@dataclass
class OpStats:
	count: int
	p50_ms: float
	p95_ms: float
	p99_ms: float
	p999_ms: float


@dataclass
class CounterSnapshot:
	repair_ops: int
	repair_bytes: int
	read_repair_ops: int
	anti_entropy_rounds: int
	errors: int


class MetricsCollector:
	"""Thread-safe metrics.

	Tracks per-op latency samples (for p50/p95/p99/p999 percentiles) and a
	set of integer counters for repair overhead and errors.
	"""

	OP_NAMES = ("put", "get", "delete", "replicate")

	def __init__(self) -> None:
		self._lock = Lock()
		self._latencies_ms: dict[str, list[float]] = {op: [] for op in self.OP_NAMES}
		self._repair_ops = 0
		self._repair_bytes = 0
		self._read_repair_ops = 0
		self._anti_entropy_rounds = 0
		self._errors = 0

	@contextmanager
	def measure(self, op_name: str):
		start = time.perf_counter()
		try:
			yield
		finally:
			elapsed_ms = (time.perf_counter() - start) * 1000.0
			with self._lock:
				self._latencies_ms.setdefault(op_name, []).append(elapsed_ms)

	def record_error(self) -> None:
		with self._lock:
			self._errors += 1

	def record_repair(self, byte_count: int = 0) -> None:
		with self._lock:
			self._repair_ops += 1
			self._repair_bytes += max(0, int(byte_count))

	def record_read_repair(self, byte_count: int = 0) -> None:
		with self._lock:
			self._read_repair_ops += 1
			self._repair_ops += 1
			self._repair_bytes += max(0, int(byte_count))

	def record_anti_entropy_round(self) -> None:
		with self._lock:
			self._anti_entropy_rounds += 1

	def snapshot(self) -> dict[str, OpStats]:
		with self._lock:
			result: dict[str, OpStats] = {}
			for op, vals in self._latencies_ms.items():
				result[op] = OpStats(
					count=len(vals),
					p50_ms=_percentile(vals, 50),
					p95_ms=_percentile(vals, 95),
					p99_ms=_percentile(vals, 99),
					p999_ms=_percentile(vals, 99.9),
				)
			return result

	def counters(self) -> CounterSnapshot:
		with self._lock:
			return CounterSnapshot(
				repair_ops=self._repair_ops,
				repair_bytes=self._repair_bytes,
				read_repair_ops=self._read_repair_ops,
				anti_entropy_rounds=self._anti_entropy_rounds,
				errors=self._errors,
			)
