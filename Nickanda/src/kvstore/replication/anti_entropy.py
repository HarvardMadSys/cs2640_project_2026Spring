from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kvstore.generated import kvstore_pb2
from kvstore.metrics import MetricsCollector
from kvstore.models import Version
from kvstore.rpc_client import make_stub
from kvstore.storage.base import StorageBackend
from kvstore.versioning.base import Versioner
from kvstore.versioning.compare import compare_versions


def _version_from_msg(m: kvstore_pb2.Version) -> Version:
	return Version(
		logical_time=int(m.logical_time),
		node_id=str(m.node_id),
		vector=tuple(sorted((e.node_id, int(e.time)) for e in m.vector)),
	)


def _version_msg(v: Version) -> kvstore_pb2.Version:
	return kvstore_pb2.Version(
		logical_time=v.logical_time,
		node_id=v.node_id,
		vector=[kvstore_pb2.VectorEntry(node_id=nid, time=t) for nid, t in v.vector],
	)


class AntiEntropyService:
	"""Background digest-sync thread.

	Every ``interval_sec`` the node picks a random peer, requests a digest
	scan, and pulls any key the peer has at a newer version. If the peer has
	an older version of a key we have, we push that key with a repair write.
	Both directions increment the shared ``MetricsCollector`` repair counters.
	"""

	def __init__(
		self,
		*,
		node_id: str,
		self_address: str,
		peers: list[str],
		backend: StorageBackend,
		versioner: Versioner,
		metrics: MetricsCollector,
		interval_sec: float = 1.0,
		rpc_timeout_sec: float = 0.6,
	) -> None:
		self._node_id = node_id
		self._self_address = self_address
		self._peers = [p for p in peers if p != self_address]
		self._backend = backend
		self._versioner = versioner
		self._metrics = metrics
		self._interval = max(0.05, float(interval_sec))
		self._rpc_timeout = rpc_timeout_sec
		self._thread: threading.Thread | None = None
		self._stop = threading.Event()
		self._pool = ThreadPoolExecutor(max_workers=max(2, len(self._peers)))

	def start(self) -> None:
		if self._thread is not None:
			return
		self._stop.clear()
		self._thread = threading.Thread(
			target=self._run,
			name=f"anti-entropy-{self._node_id}",
			daemon=True,
		)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()
		if self._thread is not None:
			self._thread.join(timeout=2.0)
			self._thread = None

	def _run(self) -> None:
		# Stagger rounds so peers don't all fire at exactly the same time.
		time.sleep(self._interval * 0.5)
		while not self._stop.is_set():
			try:
				self.run_once()
			except Exception:
				pass
			self._stop.wait(self._interval)

	def run_once(self) -> None:
		if not self._peers:
			return
		self._metrics.record_anti_entropy_round()
		futures = [self._pool.submit(self._sync_with_peer, p) for p in self._peers]
		for f in futures:
			try:
				f.result(timeout=self._rpc_timeout * 4)
			except Exception:
				continue

	def _sync_with_peer(self, peer: str) -> None:
		try:
			stub = make_stub(peer)
			resp = stub.DigestScan(
				kvstore_pb2.DigestScanRequest(since_logical_time=0),
				timeout=self._rpc_timeout,
			)
		except Exception:
			return

		# Index peer's state by key.
		peer_entries: dict[str, tuple[Version, bool]] = {}
		for e in resp.entries:
			peer_entries[e.key] = (_version_from_msg(e.version), bool(e.is_tombstone))

		local_rows = {r.key: r for r in self._backend.scan()}

		# Pull from peer: keys where peer version > local version.
		for key, (peer_version, is_tomb) in peer_entries.items():
			local = local_rows.get(key)
			need_pull = False
			if local is None:
				need_pull = True
			elif compare_versions(peer_version, local.version) > 0:
				need_pull = True
			if need_pull:
				self._pull_key(stub, peer, key, peer_version, is_tomb)

		# Push to peer: keys where local version > peer version.
		for key, row in local_rows.items():
			peer_info = peer_entries.get(key)
			need_push = False
			if peer_info is None:
				need_push = True
			else:
				peer_version, _ = peer_info
				if compare_versions(row.version, peer_version) > 0:
					need_push = True
			if need_push:
				self._push_key(stub, key, row.version, row.is_tombstone)

	def _pull_key(
		self,
		stub,
		peer: str,
		key: str,
		expected_version: Version,
		is_tomb: bool,
	) -> None:
		try:
			resp = stub.FetchKey(
				kvstore_pb2.FetchKeyRequest(key=key),
				timeout=self._rpc_timeout,
			)
		except Exception:
			return
		if not (resp.found or resp.is_tombstone):
			return
		version = _version_from_msg(resp.version)
		value = bytes(resp.value) if resp.found and not resp.is_tombstone else None
		if resp.is_tombstone:
			applied = self._backend.delete(key, version)
		else:
			applied = self._backend.put(key, value or b"", version)
		if applied:
			self._versioner.observe(version)
			self._metrics.record_repair(byte_count=len(value or b""))

	def _push_key(self, stub, key: str, version: Version, is_tomb: bool) -> None:
		record = self._backend.get(key)
		if record is None:
			return
		value = b"" if record.is_tombstone else (record.value or b"")
		try:
			stub.ReplicateWrite(
				kvstore_pb2.ReplicateWriteRequest(
					key=key,
					value=value,
					is_delete=record.is_tombstone,
					version=_version_msg(record.version),
					source_node_id=self._node_id,
					is_repair=True,
				),
				timeout=self._rpc_timeout,
			)
			self._metrics.record_repair(byte_count=len(value))
		except Exception:
			return
