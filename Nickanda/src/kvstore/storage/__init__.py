from __future__ import annotations

from kvstore.storage.base import DigestRow, StorageBackend
from kvstore.storage.faulty_wrapper import CorrelatedFaultGroup, FaultyStorageWrapper
from kvstore.storage.memory_backend import InMemoryBackend
from kvstore.storage.sqlite_backend import SqliteBackend

__all__ = [
	"StorageBackend",
	"DigestRow",
	"InMemoryBackend",
	"SqliteBackend",
	"FaultyStorageWrapper",
	"CorrelatedFaultGroup",
]


def make_backend(name: str, data_dir: str | None = None, node_id: str = "") -> StorageBackend:
	"""Factory used by node_main to pick a backend at startup."""
	lowered = name.lower()
	if lowered in ("memory", "mem", "in_memory"):
		return InMemoryBackend()
	if lowered in ("sqlite", "sqlite3", "disk"):
		if not data_dir:
			raise ValueError("sqlite backend requires --data-dir")
		import os

		os.makedirs(data_dir, exist_ok=True)
		path = os.path.join(data_dir, f"{node_id or 'node'}.sqlite")
		return SqliteBackend(path)
	raise ValueError(f"unknown storage backend: {name!r}")
