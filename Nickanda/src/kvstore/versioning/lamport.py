from __future__ import annotations

from threading import Lock

from kvstore.models import Version
from kvstore.versioning.base import Versioner


class LamportClock:
	"""Classic Lamport clock. Kept as a public class for backward-compat."""

	def __init__(self, node_id: str) -> None:
		self._node_id = node_id
		self._time = 0
		self._lock = Lock()

	@property
	def node_id(self) -> str:
		return self._node_id

	def tick(self) -> Version:
		with self._lock:
			self._time += 1
			return Version(logical_time=self._time, node_id=self._node_id)

	def observe(self, version: Version) -> None:
		with self._lock:
			self._time = max(self._time, version.logical_time)

	def current_time(self) -> int:
		with self._lock:
			return self._time


class LamportVersioner(Versioner):
	"""Versioner wrapper over LamportClock."""

	def __init__(self, node_id: str, initial_time: int = 0) -> None:
		self._clock = LamportClock(node_id)
		if initial_time > 0:
			self._clock.observe(Version(logical_time=initial_time, node_id=node_id))

	@property
	def node_id(self) -> str:
		return self._clock.node_id

	def tick(self) -> Version:
		return self._clock.tick()

	def observe(self, version: Version) -> None:
		self._clock.observe(version)

	def current_time(self) -> int:
		return self._clock.current_time()

	def name(self) -> str:
		return "lamport"
