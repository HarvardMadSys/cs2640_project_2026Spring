from __future__ import annotations

from kvstore.models import Version
from kvstore.storage.memory_backend import InMemoryBackend


def test_put_then_get_round_trip() -> None:
	store = InMemoryBackend()
	v = Version(logical_time=1, node_id="n1")
	assert store.put("k", b"hello", v)

	rec = store.get("k")
	assert rec is not None
	assert rec.value == b"hello"
	assert rec.version == v
	assert not rec.is_tombstone


def test_newer_version_wins() -> None:
	store = InMemoryBackend()
	older = Version(logical_time=2, node_id="n1")
	newer = Version(logical_time=3, node_id="n2")

	assert store.put("k", b"old", older)
	assert store.put("k", b"new", newer)

	rec = store.get("k")
	assert rec is not None
	assert rec.value == b"new"
	assert rec.version == newer


def test_older_version_is_rejected() -> None:
	store = InMemoryBackend()
	newer = Version(logical_time=5, node_id="n2")
	older = Version(logical_time=4, node_id="n1")

	assert store.put("k", b"new", newer)
	assert not store.put("k", b"old", older)

	rec = store.get("k")
	assert rec is not None
	assert rec.value == b"new"
	assert rec.version == newer


def test_delete_tombstone_hides_value() -> None:
	store = InMemoryBackend()
	v1 = Version(logical_time=1, node_id="n1")
	v2 = Version(logical_time=2, node_id="n1")

	assert store.put("k", b"value", v1)
	assert store.delete("k", v2)

	rec = store.get("k")
	assert rec is not None
	assert rec.is_tombstone
	assert rec.value is None
