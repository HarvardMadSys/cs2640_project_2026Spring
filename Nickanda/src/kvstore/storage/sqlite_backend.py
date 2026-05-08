from __future__ import annotations

import json
import sqlite3
from threading import RLock

from kvstore.models import ValueRecord, Version
from kvstore.storage.base import DigestRow, StorageBackend
from kvstore.versioning.compare import compare_versions


def _encode_vector(vec: tuple[tuple[str, int], ...]) -> str:
	if not vec:
		return ""
	return json.dumps(list(vec), separators=(",", ":"))


def _decode_vector(raw: str | None) -> tuple[tuple[str, int], ...]:
	if not raw:
		return ()
	parsed = json.loads(raw)
	return tuple((str(nid), int(t)) for nid, t in parsed)


class SqliteBackend(StorageBackend):
	"""Durable SQLite-backed storage using the stdlib sqlite3 module.

	Uses WAL mode for reasonable write concurrency. Each logical row carries
	the full Version (logical_time, origin node, optional vector).
	"""

	def __init__(self, path: str) -> None:
		self._path = path
		self._lock = RLock()
		self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
		self._conn.execute("PRAGMA journal_mode=WAL")
		self._conn.execute("PRAGMA synchronous=NORMAL")
		self._conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS kv (
				key TEXT PRIMARY KEY,
				value BLOB,
				logical_time INTEGER NOT NULL,
				origin_node TEXT NOT NULL,
				vector TEXT,
				tombstone INTEGER NOT NULL DEFAULT 0
			)
			"""
		)

	def _row_to_record(self, row: tuple) -> ValueRecord:
		value, logical_time, origin_node, vector_raw, tombstone = row
		version = Version(
			logical_time=int(logical_time),
			node_id=str(origin_node),
			vector=_decode_vector(vector_raw),
		)
		return ValueRecord(
			value=None if tombstone else (bytes(value) if value is not None else b""),
			version=version,
			is_tombstone=bool(tombstone),
		)

	def get(self, key: str) -> ValueRecord | None:
		with self._lock:
			cur = self._conn.execute(
				"SELECT value, logical_time, origin_node, vector, tombstone FROM kv WHERE key=?",
				(key,),
			)
			row = cur.fetchone()
			if row is None:
				return None
			return self._row_to_record(row)

	def _upsert(self, key: str, value: bytes | None, version: Version, tombstone: bool) -> bool:
		with self._lock:
			cur = self._conn.execute(
				"SELECT logical_time, origin_node, vector FROM kv WHERE key=?",
				(key,),
			)
			existing = cur.fetchone()
			if existing is not None:
				current_version = Version(
					logical_time=int(existing[0]),
					node_id=str(existing[1]),
					vector=_decode_vector(existing[2]),
				)
				if compare_versions(version, current_version) <= 0:
					return False
			self._conn.execute(
				"""
				INSERT INTO kv (key, value, logical_time, origin_node, vector, tombstone)
				VALUES (?, ?, ?, ?, ?, ?)
				ON CONFLICT(key) DO UPDATE SET
					value=excluded.value,
					logical_time=excluded.logical_time,
					origin_node=excluded.origin_node,
					vector=excluded.vector,
					tombstone=excluded.tombstone
				""",
				(
					key,
					sqlite3.Binary(value) if value is not None else None,
					int(version.logical_time),
					str(version.node_id),
					_encode_vector(version.vector),
					1 if tombstone else 0,
				),
			)
			return True

	def put(self, key: str, value: bytes, version: Version) -> bool:
		return self._upsert(key, value, version, tombstone=False)

	def delete(self, key: str, version: Version) -> bool:
		return self._upsert(key, None, version, tombstone=True)

	def scan(self) -> list[DigestRow]:
		with self._lock:
			cur = self._conn.execute(
				"SELECT key, logical_time, origin_node, vector, tombstone FROM kv"
			)
			rows = cur.fetchall()
		out: list[DigestRow] = []
		for key, logical_time, origin_node, vector_raw, tombstone in rows:
			out.append(
				DigestRow(
					key=str(key),
					version=Version(
						logical_time=int(logical_time),
						node_id=str(origin_node),
						vector=_decode_vector(vector_raw),
					),
					is_tombstone=bool(tombstone),
				)
			)
		return out

	def max_logical_time(self) -> int:
		with self._lock:
			cur = self._conn.execute("SELECT MAX(logical_time) FROM kv")
			row = cur.fetchone()
			if row is None or row[0] is None:
				return 0
			return int(row[0])

	def close(self) -> None:
		with self._lock:
			try:
				self._conn.close()
			except sqlite3.Error:
				pass
