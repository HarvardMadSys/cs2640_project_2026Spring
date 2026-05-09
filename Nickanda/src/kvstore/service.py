from __future__ import annotations

import grpc

from kvstore.config import NodeConfig
from kvstore.fault import FaultState
from kvstore.generated import kvstore_pb2, kvstore_pb2_grpc
from kvstore.metrics import MetricsCollector
from kvstore.models import Version
from kvstore.replication.base import Replicator
from kvstore.storage.base import StorageBackend
from kvstore.versioning.base import Versioner


def _version_to_msg(v: Version) -> kvstore_pb2.Version:
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


class KVStoreService(kvstore_pb2_grpc.KVStoreServicer):
	"""gRPC service backed by a pluggable Replicator + StorageBackend."""

	def __init__(
		self,
		cfg: NodeConfig,
		backend: StorageBackend,
		versioner: Versioner,
		replicator: Replicator,
		metrics: MetricsCollector,
		fault_state: FaultState,
	) -> None:
		self._cfg = cfg
		self._backend = backend
		self._versioner = versioner
		self._replicator = replicator
		self._metrics = metrics
		self._fault_state = fault_state

	def _ensure_enabled(self, context: grpc.ServicerContext) -> None:
		if not self._fault_state.is_enabled():
			context.abort(grpc.StatusCode.UNAVAILABLE, "node disabled by fault injector")

	def Put(
		self, request: kvstore_pb2.PutRequest, context: grpc.ServicerContext
	) -> kvstore_pb2.PutResponse:
		self._ensure_enabled(context)
		with self._metrics.measure("put"):
			result = self._replicator.coordinate_put(request.key, bytes(request.value))
			if not result.ok:
				self._metrics.record_error()
				context.abort(grpc.StatusCode.FAILED_PRECONDITION, result.error)
			return kvstore_pb2.PutResponse(
				ok=True,
				version=_version_to_msg(result.version),  # type: ignore[arg-type]
			)

	def Get(
		self, request: kvstore_pb2.GetRequest, context: grpc.ServicerContext
	) -> kvstore_pb2.GetResponse:
		self._ensure_enabled(context)
		with self._metrics.measure("get"):
			result = self._replicator.coordinate_get(request.key)
			if not result.found or result.value is None:
				return kvstore_pb2.GetResponse(found=False)
			return kvstore_pb2.GetResponse(
				found=True,
				value=result.value,
				version=_version_to_msg(result.version),  # type: ignore[arg-type]
			)

	def Delete(
		self, request: kvstore_pb2.DeleteRequest, context: grpc.ServicerContext
	) -> kvstore_pb2.DeleteResponse:
		self._ensure_enabled(context)
		with self._metrics.measure("delete"):
			result = self._replicator.coordinate_delete(request.key)
			if not result.ok:
				self._metrics.record_error()
				context.abort(grpc.StatusCode.FAILED_PRECONDITION, result.error)
			return kvstore_pb2.DeleteResponse(
				ok=True,
				version=_version_to_msg(result.version),  # type: ignore[arg-type]
			)

	def ReplicateWrite(
		self,
		request: kvstore_pb2.ReplicateWriteRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.ReplicateWriteResponse:
		self._ensure_enabled(context)
		with self._metrics.measure("replicate"):
			version = _version_from_msg(request.version)
			self._versioner.observe(version)
			if request.is_delete:
				applied = self._backend.delete(request.key, version)
			else:
				applied = self._backend.put(request.key, bytes(request.value), version)
			if applied and request.is_repair:
				self._metrics.record_repair(byte_count=len(request.value))
			return kvstore_pb2.ReplicateWriteResponse(ok=True)

	def DigestScan(
		self,
		request: kvstore_pb2.DigestScanRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.DigestScanResponse:
		self._ensure_enabled(context)
		entries = []
		floor = int(request.since_logical_time)
		for row in self._backend.scan():
			if row.version.logical_time < floor:
				continue
			entries.append(
				kvstore_pb2.DigestEntry(
					key=row.key,
					version=_version_to_msg(row.version),
					is_tombstone=row.is_tombstone,
				)
			)
		return kvstore_pb2.DigestScanResponse(entries=entries)

	def FetchKey(
		self,
		request: kvstore_pb2.FetchKeyRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.FetchKeyResponse:
		self._ensure_enabled(context)
		rec = self._backend.get(request.key)
		if rec is None:
			return kvstore_pb2.FetchKeyResponse(found=False, is_tombstone=False)
		if rec.is_tombstone:
			return kvstore_pb2.FetchKeyResponse(
				found=False,
				is_tombstone=True,
				version=_version_to_msg(rec.version),
			)
		return kvstore_pb2.FetchKeyResponse(
			found=True,
			value=rec.value or b"",
			version=_version_to_msg(rec.version),
			is_tombstone=False,
		)

	def Health(
		self,
		request: kvstore_pb2.HealthRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.HealthResponse:
		return kvstore_pb2.HealthResponse(
			serving=self._fault_state.is_enabled(),
			node_id=self._cfg.node_id,
			mode=self._cfg.mode,
			is_leader=self._cfg.is_leader,
		)

	def Metrics(
		self,
		request: kvstore_pb2.MetricsRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.MetricsResponse:
		snap = self._metrics.snapshot()
		counters = self._metrics.counters()
		return kvstore_pb2.MetricsResponse(
			put_count=snap["put"].count,
			get_count=snap["get"].count,
			delete_count=snap["delete"].count,
			replicate_count=snap["replicate"].count,
			put_p50_ms=snap["put"].p50_ms,
			put_p95_ms=snap["put"].p95_ms,
			put_p99_ms=snap["put"].p99_ms,
			get_p50_ms=snap["get"].p50_ms,
			get_p95_ms=snap["get"].p95_ms,
			get_p99_ms=snap["get"].p99_ms,
			delete_p50_ms=snap["delete"].p50_ms,
			delete_p95_ms=snap["delete"].p95_ms,
			delete_p99_ms=snap["delete"].p99_ms,
			replicate_p50_ms=snap["replicate"].p50_ms,
			replicate_p95_ms=snap["replicate"].p95_ms,
			replicate_p99_ms=snap["replicate"].p99_ms,
			repair_ops=counters.repair_ops,
			repair_bytes=counters.repair_bytes,
			read_repair_ops=counters.read_repair_ops,
			anti_entropy_rounds=counters.anti_entropy_rounds,
			error_count=counters.errors,
		)

	def SetNodeState(
		self,
		request: kvstore_pb2.SetNodeStateRequest,
		context: grpc.ServicerContext,
	) -> kvstore_pb2.SetNodeStateResponse:
		self._fault_state.set_enabled(request.enabled)
		return kvstore_pb2.SetNodeStateResponse(ok=True)
