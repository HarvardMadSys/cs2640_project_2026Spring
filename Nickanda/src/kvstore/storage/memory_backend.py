from __future__ import annotations

from threading import RLock

from kvstore.models import ValueRecord, Version
from kvstore.storage.base import DigestRow, StorageBackend
from kvstore.versioning.compare import compare_versions


class InMemoryBackend(StorageBackend):
	"""Dict-backed store with a lock. Useful for tests and baseline runs."""

	def __init__(self) -> None:
		self._data: dict[str, ValueRecord] = {}
		self._lock = RLock()

	def get(self, key: str) -> ValueRecord | None:
		with self._lock:
			return self._data.get(key)

	def put(self, key: str, value: bytes, version: Version) -> bool:
		return self._upsert(key, ValueRecord(value=value, version=version, is_tombstone=False))

	def delete(self, key: str, version: Version) -> bool:
		return self._upsert(key, ValueRecord(value=None, version=version, is_tombstone=True))

	def _upsert(self, key: str, record: ValueRecord) -> bool:
		with self._lock:
			current = self._data.get(key)
			if current is None or compare_versions(record.version, current.version) > 0:
				self._data[key] = record
				return True
			return False

	def scan(self) -> list[DigestRow]:
		with self._lock:
			return [
				DigestRow(key=k, version=r.version, is_tombstone=r.is_tombstone)
				for k, r in self._data.items()
			]

	def max_logical_time(self) -> int:
		with self._lock:
			if not self._data:
				return 0
			return max(r.version.logical_time for r in self._data.values())
