from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from kvstore.generated import kvstore_pb2
from kvstore.metrics import MetricsCollector
from kvstore.models import Version
from kvstore.replication.base import (
	CoordinatorReadResult,
	CoordinatorResult,
	Replicator,
)
from kvstore.rpc_client import make_stub
from kvstore.storage.base import StorageBackend
from kvstore.versioning.base import Versioner


def _version_msg(v: Version) -> kvstore_pb2.Version:
	return kvstore_pb2.Version(
		logical_time=v.logical_time,
		node_id=v.node_id,
		vector=[kvstore_pb2.VectorEntry(node_id=nid, time=t) for nid, t in v.vector],
	)


class LeaderReplicator(Replicator):
	"""Writes go to the leader; leader fans out to followers best-effort.

	The leader's local apply is synchronous. Peer fanout is dispatched in
	parallel and waited for with a short timeout, so a slow follower only
	extends write latency up to ``write_timeout_sec``. Reads are served
	locally from the leader.
	"""

	def __init__(
		self,
		*,
		node_id: str,
		self_address: str,
		leader_address: str,
		peers: list[str],
		backend: StorageBackend,
		versioner: Versioner,
		metrics: MetricsCollector,
		write_timeout_sec: float = 0.8,
		thread_pool: ThreadPoolExecutor | None = None,
	) -> None:
		self._node_id = node_id
		self._self_address = self_address
		self._leader_address = leader_address
		self._peers = [p for p in peers if p != self_address]
		self._backend = backend
		self._versioner = versioner
		self._metrics = metrics
		self._write_timeout = write_timeout_sec
		self._pool = thread_pool or ThreadPoolExecutor(max_workers=max(4, len(self._peers) * 2))

	@property
	def is_leader(self) -> bool:
		return self._self_address == self._leader_address

	def mode_name(self) -> str:
		return "leader"

	def coordinate_put(self, key: str, value: bytes) -> CoordinatorResult:
		if not self.is_leader:
			return CoordinatorResult(ok=False, version=None, error="writes must go to leader")
		version = self._versioner.tick()
		self._backend.put(key, value, version)
		self._fanout_replicate(key, value, is_delete=False, version=version)
		return CoordinatorResult(ok=True, version=version)

	def coordinate_delete(self, key: str) -> CoordinatorResult:
		if not self.is_leader:
			return CoordinatorResult(ok=False, version=None, error="writes must go to leader")
		version = self._versioner.tick()
		self._backend.delete(key, version)
		self._fanout_replicate(key, b"", is_delete=True, version=version)
		return CoordinatorResult(ok=True, version=version)

	def coordinate_get(self, key: str) -> CoordinatorReadResult:
		rec = self._backend.get(key)
		if rec is None:
			return CoordinatorReadResult(
				found=False,
				value=None,
				version=None,
				is_tombstone=False,
				replica_results=[],
			)
		if rec.is_tombstone:
			return CoordinatorReadResult(
				found=False,
				value=None,
				version=rec.version,
				is_tombstone=True,
				replica_results=[],
			)
		return CoordinatorReadResult(
			found=True,
			value=rec.value,
			version=rec.version,
			is_tombstone=False,
			replica_results=[],
		)

	def _fanout_replicate(
		self, key: str, value: bytes, is_delete: bool, version: Version
	) -> None:
		if not self._peers:
			return
		req = kvstore_pb2.ReplicateWriteRequest(
			key=key,
			value=value,
			is_delete=is_delete,
			version=_version_msg(version),
			source_node_id=self._node_id,
			is_repair=False,
		)

		def _dispatch(peer: str) -> bool:
			try:
				stub = make_stub(peer)
				resp = stub.ReplicateWrite(req, timeout=self._write_timeout)
				return bool(resp.ok)
			except Exception:
				return False

		futures = [self._pool.submit(_dispatch, p) for p in self._peers]
		for f in futures:
			try:
				f.result(timeout=self._write_timeout)
			except Exception:
				continue
