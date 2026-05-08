from __future__ import annotations

from threading import Lock

from kvstore.models import Version
from kvstore.versioning.base import Versioner


def _canonicalize(vec: dict[str, int]) -> tuple[tuple[str, int], ...]:
	return tuple(sorted((k, v) for k, v in vec.items() if v > 0))


class VectorClockVersioner(Versioner):
	"""Vector-clock versioner.

	Each node maintains a per-node counter map. ``tick()`` increments this
	node's counter and emits a Version carrying the full vector; the
	``logical_time`` field is set to this node's own counter to double as a
	Lamport LWW tiebreak when two vectors are concurrent.

	``observe()`` merges an incoming vector (per-component max), and also
	advances our own component if the incoming vector's value for this node
	is higher than what we remember.
	"""

	def __init__(self, node_id: str) -> None:
		self._node_id = node_id
		self._vec: dict[str, int] = {node_id: 0}
		self._lock = Lock()

	@property
	def node_id(self) -> str:
		return self._node_id

	def tick(self) -> Version:
		with self._lock:
			self._vec[self._node_id] = self._vec.get(self._node_id, 0) + 1
			own_time = self._vec[self._node_id]
			canonical = _canonicalize(self._vec)
		return Version(
			logical_time=own_time,
			node_id=self._node_id,
			vector=canonical,
		)

	def observe(self, version: Version) -> None:
		with self._lock:
			if version.vector:
				for nid, t in version.vector:
					if t > self._vec.get(nid, 0):
						self._vec[nid] = t
			else:
				nid = version.node_id or ""
				if version.logical_time > self._vec.get(nid, 0):
					self._vec[nid] = version.logical_time

	def snapshot(self) -> tuple[tuple[str, int], ...]:
		with self._lock:
			return _canonicalize(self._vec)

	def current_time(self) -> int:
		with self._lock:
			return self._vec.get(self._node_id, 0)

	def name(self) -> str:
		return "vector_clock"
