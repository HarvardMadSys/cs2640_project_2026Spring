from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from kvstore.models import ValueRecord, Version


@dataclass(frozen=True)
class DigestRow:
	key: str
	version: Version
	is_tombstone: bool


class StorageBackend(ABC):
	"""Durable or in-memory key-value storage interface.

	The contract for ``put`` / ``delete`` is last-writer-wins: if the incoming
	``version`` is not strictly newer than the currently-stored version, the
	operation is a no-op and returns ``False``. This keeps the layer above
	agnostic to whether a write came from a direct client or from replication.
	"""

	@abstractmethod
	def get(self, key: str) -> ValueRecord | None: ...

	@abstractmethod
	def put(self, key: str, value: bytes, version: Version) -> bool: ...

	@abstractmethod
	def delete(self, key: str, version: Version) -> bool: ...

	@abstractmethod
	def scan(self) -> list[DigestRow]:
		"""Return one DigestRow per key currently stored (including tombstones)."""

	@abstractmethod
	def max_logical_time(self) -> int:
		"""Maximum logical_time observed on any stored version; 0 if empty."""

	def close(self) -> None:
		"""Release any backend resources (files, connections)."""
