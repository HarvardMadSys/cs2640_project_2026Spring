from __future__ import annotations

import os
import tempfile

import pytest

from kvstore.models import Version
from kvstore.storage.memory_backend import InMemoryBackend
from kvstore.storage.sqlite_backend import SqliteBackend


def _backends(tmp_path_factory) -> list:
	mem = InMemoryBackend()
	sqlite_dir = tmp_path_factory.mktemp("sqlite")
	sqlite = SqliteBackend(str(sqlite_dir / "n1.sqlite"))
	return [("memory", mem), ("sqlite", sqlite)]


@pytest.fixture(scope="module")
def all_backends(tmp_path_factory):
	items = _backends(tmp_path_factory)
	yield items
	for _, b in items:
		b.close()


def test_put_get_roundtrip_for_each_backend(all_backends) -> None:
	for name, b in all_backends:
		v = Version(logical_time=1, node_id="n1")
		assert b.put("k", b"hello", v), name
		rec = b.get("k")
		assert rec is not None, name
		assert rec.value == b"hello", name
		assert not rec.is_tombstone, name


def test_older_version_is_rejected(all_backends) -> None:
	for name, b in all_backends:
		newer = Version(logical_time=10, node_id="n2")
		older = Version(logical_time=9, node_id="n1")
		assert b.put("k2", b"new", newer), name
		assert not b.put("k2", b"old", older), name
		rec = b.get("k2")
		assert rec.value == b"new", name


def test_delete_tombstone_hides_value(all_backends) -> None:
	for name, b in all_backends:
		v1 = Version(logical_time=1, node_id="n1")
		v2 = Version(logical_time=2, node_id="n1")
		assert b.put("k3", b"value", v1), name
		assert b.delete("k3", v2), name
		rec = b.get("k3")
		assert rec is not None, name
		assert rec.is_tombstone, name
		assert rec.value is None, name


def test_scan_and_max_logical_time(all_backends) -> None:
	for name, b in all_backends:
		base = Version(logical_time=100, node_id="n1")
		b.put("scan_a", b"x", base)
		b.put("scan_b", b"y", Version(logical_time=101, node_id="n2"))
		rows = {r.key: r for r in b.scan()}
		assert "scan_a" in rows and "scan_b" in rows, name
		assert b.max_logical_time() >= 101, name


def test_sqlite_durable_across_reopen() -> None:
	with tempfile.TemporaryDirectory() as td:
		path = os.path.join(td, "n1.sqlite")
		s1 = SqliteBackend(path)
		s1.put("durable_key", b"persisted", Version(logical_time=42, node_id="n1"))
		s1.delete("tomb_key", Version(logical_time=43, node_id="n1"))
		s1.close()

		s2 = SqliteBackend(path)
		rec = s2.get("durable_key")
		assert rec is not None and rec.value == b"persisted"
		tomb = s2.get("tomb_key")
		assert tomb is not None and tomb.is_tombstone
		assert s2.max_logical_time() == 43
		s2.close()


def test_sqlite_preserves_vector_clock_round_trip() -> None:
	with tempfile.TemporaryDirectory() as td:
		path = os.path.join(td, "vc.sqlite")
		s = SqliteBackend(path)
		ver = Version(logical_time=7, node_id="n2", vector=(("n1", 3), ("n2", 7)))
		assert s.put("vc_key", b"vv", ver)
		rec = s.get("vc_key")
		assert rec is not None
		assert rec.version.vector == (("n1", 3), ("n2", 7))
		s.close()
