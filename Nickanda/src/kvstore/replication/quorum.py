from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from kvstore.generated import kvstore_pb2
from kvstore.metrics import MetricsCollector
from kvstore.models import Version
from kvstore.replication.base import (
	CoordinatorReadResult,
	CoordinatorResult,
	ReadReplicaResult,
	Replicator,
)
from kvstore.rpc_client import make_stub
from kvstore.storage.base import StorageBackend
from kvstore.versioning.base import Versioner
from kvstore.versioning.compare import compare_versions


def _version_msg(v: Version) -> kvstore_pb2.Version:
	return kvstore_pb2.Version(
		logical_time=v.logical_time,
		node_id=v.node_id,
		vector=[kvstore_pb2.VectorEntry(node_id=nid, time=t) for nid, t in v.vector],
	)


def _version_from_msg(m: kvstore_pb2.Version) -> Version:
	return Version(
		logical_time=int(m.logical_time),
		node_id=str(m.node_id),
		vector=tuple(sorted((e.node_id, int(e.time)) for e in m.vector)),
	)


class QuorumReplicator(Replicator):
	"""Quorum-based replication with (w, r).

	All nodes are peers. A write returns success when at least ``w`` replicas
	(including the coordinator itself) have applied it; a read queries up to
	``r`` replicas in parallel (including the coordinator), picks the
	newest-by-version, and triggers asynchronous read-repair for any
	replica that returned an older / missing value.
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
		w: int,
		r: int,
		write_timeout_sec: float = 0.8,
		read_timeout_sec: float = 0.6,
		thread_pool: ThreadPoolExecutor | None = None,
	) -> None:
		self._node_id = node_id
		self._self_address = self_address
		self._peers = [p for p in peers if p != self_address]
		self._backend = backend
		self._versioner = versioner
		self._metrics = metrics
		self._w = max(1, int(w))
		self._r = max(1, int(r))
		self._write_timeout = write_timeout_sec
		self._read_timeout = read_timeout_sec
		n = 1 + len(self._peers)
		if self._w > n:
			raise ValueError(f"w={self._w} exceeds cluster size n={n}")
		if self._r > n:
			raise ValueError(f"r={self._r} exceeds cluster size n={n}")
		self._pool = thread_pool or ThreadPoolExecutor(max_workers=max(4, n * 2))

	def mode_name(self) -> str:
		return f"quorum-w{self._w}r{self._r}"

	def _write_quorum(self, key: str, value: bytes, is_delete: bool) -> CoordinatorResult:
		version = self._versioner.tick()
		if is_delete:
			self._backend.delete(key, version)
		else:
			self._backend.put(key, value, version)
		acks = 1
		if acks >= self._w:
			self._fire_and_forget_replicate(key, value, is_delete, version)
			return CoordinatorResult(ok=True, version=version)

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

		futures = {self._pool.submit(_dispatch, p): p for p in self._peers}
		try:
			for fut in as_completed(futures, timeout=self._write_timeout):
				if fut.result():
					acks += 1
				if acks >= self._w:
					return CoordinatorResult(ok=True, version=version)
		except Exception:
			pass
		if acks >= self._w:
			return CoordinatorResult(ok=True, version=version)
		return CoordinatorResult(
			ok=False,
			version=version,
			error=f"quorum not reached: got {acks}/{self._w}",
		)

	def _fire_and_forget_replicate(
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

		def _go(peer: str) -> None:
			try:
				stub = make_stub(peer)
				stub.ReplicateWrite(req, timeout=self._write_timeout)
			except Exception:
				pass

		for p in self._peers:
			self._pool.submit(_go, p)

	def coordinate_put(self, key: str, value: bytes) -> CoordinatorResult:
		return self._write_quorum(key, value, is_delete=False)

	def coordinate_delete(self, key: str) -> CoordinatorResult:
		return self._write_quorum(key, b"", is_delete=True)

	def coordinate_get(self, key: str) -> CoordinatorReadResult:
		replicas_needed = min(self._r, 1 + len(self._peers))
		local_rec = self._backend.get(key)
		local_result = ReadReplicaResult(
			peer=self._self_address,
			found=local_rec is not None and not local_rec.is_tombstone,
			value=(local_rec.value if local_rec and not local_rec.is_tombstone else None),
			version=(local_rec.version if local_rec is not None else None),
			is_tombstone=bool(local_rec and local_rec.is_tombstone),
			error=None,
		)
		results: list[ReadReplicaResult] = [local_result]
		remaining = replicas_needed - 1

		futures: list = []
		if self._peers:
			req = kvstore_pb2.FetchKeyRequest(key=key)

			def _dispatch(peer: str) -> ReadReplicaResult:
				try:
					stub = make_stub(peer)
					resp = stub.FetchKey(req, timeout=self._read_timeout)
					version = _version_from_msg(resp.version) if resp.found or resp.is_tombstone else None
					return ReadReplicaResult(
						peer=peer,
						found=bool(resp.found),
						value=bytes(resp.value) if resp.found else None,
						version=version,
						is_tombstone=bool(resp.is_tombstone),
						error=None,
					)
				except Exception as e:
					return ReadReplicaResult(
						peer=peer,
						found=False,
						value=None,
						version=None,
						is_tombstone=False,
						error=str(e),
					)

			futures = [self._pool.submit(_dispatch, p) for p in self._peers]

		# Collect enough responses to satisfy the read quorum.
		done: set = set()
		if remaining > 0 and futures:
			collected = 0
			try:
				for fut in as_completed(futures, timeout=self._read_timeout):
					r = fut.result()
					results.append(r)
					done.add(fut)
					if r.error is None:
						collected += 1
					if collected >= remaining:
						break
			except Exception:
				pass

		winner = self._pick_newest(results)

		# Drain any remaining peer responses (briefly) so read-repair can
		# target stale replicas that hadn't yet responded in time for the
		# quorum. If everything timed out we just skip the repair.
		pending = [f for f in futures if f not in done]
		if pending:
			try:
				for fut in as_completed(pending, timeout=self._read_timeout):
					results.append(fut.result())
			except Exception:
				pass

		self._maybe_read_repair(key, winner, results)

		if winner is None:
			return CoordinatorReadResult(
				found=False,
				value=None,
				version=None,
				is_tombstone=False,
				replica_results=results,
			)
		return CoordinatorReadResult(
			found=winner.found,
			value=winner.value,
			version=winner.version,
			is_tombstone=winner.is_tombstone,
			replica_results=results,
		)

	def _pick_newest(self, results: list[ReadReplicaResult]) -> ReadReplicaResult | None:
		best: ReadReplicaResult | None = None
		for r in results:
			if r.error is not None:
				continue
			if r.version is None:
				continue
			if best is None or compare_versions(r.version, best.version) > 0:  # type: ignore[arg-type]
				best = r
		return best

	def _maybe_read_repair(
		self,
		key: str,
		winner: ReadReplicaResult | None,
		results: list[ReadReplicaResult],
	) -> None:
		if winner is None or winner.version is None:
			return
		winner_version = winner.version
		value_bytes = winner.value if winner.value is not None else b""
		for r in results:
			if r.peer == winner.peer:
				continue
			if r.error is not None:
				continue
			if r.version is None or compare_versions(r.version, winner_version) < 0:
				self._send_repair_write(
					peer=r.peer,
					key=key,
					value=value_bytes,
					is_delete=winner.is_tombstone,
					version=winner_version,
				)

	def _send_repair_write(
		self,
		*,
		peer: str,
		key: str,
		value: bytes,
		is_delete: bool,
		version: Version,
	) -> None:
		req = kvstore_pb2.ReplicateWriteRequest(
			key=key,
			value=value,
			is_delete=is_delete,
			version=_version_msg(version),
			source_node_id=self._node_id,
			is_repair=True,
		)

		def _go() -> None:
			try:
				stub = make_stub(peer)
				stub.ReplicateWrite(req, timeout=self._read_timeout)
				self._metrics.record_read_repair(byte_count=len(value))
			except Exception:
				pass

		self._pool.submit(_go)
