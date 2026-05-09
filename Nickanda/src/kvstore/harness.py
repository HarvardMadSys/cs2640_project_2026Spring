"""Helpers to spin up small in-process clusters of KVStore nodes.

This module is imported both by the pytest suite and by the experiment
runner so the two paths share the same semantics.
"""

from __future__ import annotations

import contextlib
import socket
import tempfile
import time
from concurrent import futures
from dataclasses import dataclass, field
from pathlib import Path

import grpc

from kvstore.config import NodeConfig
from kvstore.fault import FaultState
from kvstore.generated import kvstore_pb2, kvstore_pb2_grpc
from kvstore.metrics import MetricsCollector
from kvstore.node_main import restore_versioner_from_backend
from kvstore.replication.anti_entropy import AntiEntropyService
from kvstore.replication.leader import LeaderReplicator
from kvstore.replication.quorum import QuorumReplicator
from kvstore.rpc_client import make_stub, reset_channel_cache
from kvstore.service import KVStoreService
from kvstore.storage import make_backend
from kvstore.storage.base import StorageBackend
from kvstore.storage.faulty_wrapper import (
	CorrelatedFaultGroup,
	FaultSpec,
	FaultyStorageWrapper,
)
from kvstore.versioning import make_versioner
from kvstore.versioning.base import Versioner


def free_port() -> int:
	with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
		s.bind(("127.0.0.1", 0))
		return s.getsockname()[1]


@dataclass
class NodeHandle:
	node_id: str
	address: str
	server: grpc.Server
	backend: StorageBackend
	versioner: Versioner
	metrics: MetricsCollector
	fault_state: FaultState
	anti_entropy: AntiEntropyService | None = None
	cfg: NodeConfig = field(default=None)  # type: ignore[assignment]

	def stub(self):
		return make_stub(self.address)

	def stop(self) -> None:
		if self.anti_entropy is not None:
			self.anti_entropy.stop()
		self.server.stop(grace=0.2).wait(timeout=2.0)
		self.backend.close()


@dataclass
class Cluster:
	nodes: list[NodeHandle]
	tmpdir: tempfile.TemporaryDirectory | None = None

	def by_id(self, node_id: str) -> NodeHandle:
		for n in self.nodes:
			if n.node_id == node_id:
				return n
		raise KeyError(node_id)

	def stop(self) -> None:
		for n in self.nodes:
			try:
				n.stop()
			except Exception:
				pass
		if self.tmpdir is not None:
			self.tmpdir.cleanup()
		reset_channel_cache()


def build_node(
	*,
	node_id: str,
	address: str,
	peer_addresses: list[str],
	leader_address: str,
	mode: str,
	versioning: str,
	backend_kind: str,
	data_dir: str | None,
	w: int,
	r: int,
	fault_spec: FaultSpec | None,
	anti_entropy_interval: float,
	correlated_group: CorrelatedFaultGroup | None = None,
) -> NodeHandle:
	raw_backend = make_backend(backend_kind, data_dir=data_dir, node_id=node_id)
	backend: StorageBackend
	if fault_spec is not None or correlated_group is not None:
		backend = FaultyStorageWrapper(
			inner=raw_backend,
			spec=fault_spec or FaultSpec(),
			correlated_group=correlated_group,
		)
	else:
		backend = raw_backend
	versioner = make_versioner(versioning, node_id)
	restore_versioner_from_backend(versioner, backend)
	metrics = MetricsCollector()
	fault_state = FaultState()

	if mode == "leader":
		replicator = LeaderReplicator(
			node_id=node_id,
			self_address=address,
			leader_address=leader_address,
			peers=peer_addresses,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
		)
	elif mode == "quorum":
		replicator = QuorumReplicator(
			node_id=node_id,
			self_address=address,
			peers=peer_addresses,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
			w=w,
			r=r,
		)
	else:
		raise ValueError(f"unknown mode: {mode}")

	cfg = NodeConfig(
		node_id=node_id,
		bind_address=address,
		peers=peer_addresses,
		leader_address=leader_address,
		mode=mode,
		versioning=versioning,
		backend=backend_kind,
		data_dir=data_dir,
		w=w,
		r=r,
		anti_entropy_interval_sec=anti_entropy_interval,
	)

	svc = KVStoreService(
		cfg=cfg,
		backend=backend,
		versioner=versioner,
		replicator=replicator,
		metrics=metrics,
		fault_state=fault_state,
	)
	server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
	kvstore_pb2_grpc.add_KVStoreServicer_to_server(svc, server)
	bound = server.add_insecure_port(address)
	if bound == 0:
		raise RuntimeError(f"failed to bind {address}")
	server.start()

	ae: AntiEntropyService | None = None
	if anti_entropy_interval > 0:
		ae = AntiEntropyService(
			node_id=node_id,
			self_address=address,
			peers=peer_addresses,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
			interval_sec=anti_entropy_interval,
		)
		ae.start()

	return NodeHandle(
		node_id=node_id,
		address=address,
		server=server,
		backend=backend,
		versioner=versioner,
		metrics=metrics,
		fault_state=fault_state,
		anti_entropy=ae,
		cfg=cfg,
	)


def build_cluster(
	*,
	n: int = 3,
	mode: str = "leader",
	versioning: str = "lamport",
	backend: str = "memory",
	data_dir: str | None = None,
	w: int = 2,
	r: int = 2,
	fault_specs: dict[str, FaultSpec] | None = None,
	anti_entropy_interval: float = 0.0,
	correlated_group: CorrelatedFaultGroup | None = None,
	correlated_members: list[str] | None = None,
) -> Cluster:
	fault_specs = fault_specs or {}
	reset_channel_cache()
	tmpdir: tempfile.TemporaryDirectory | None = None
	if backend == "sqlite" and data_dir is None:
		tmpdir = tempfile.TemporaryDirectory(prefix="kvstore-exp-")
		data_dir = tmpdir.name
	elif backend == "sqlite":
		Path(data_dir).mkdir(parents=True, exist_ok=True)

	ports = [free_port() for _ in range(n)]
	node_ids = [f"n{i + 1}" for i in range(n)]
	addresses = [f"127.0.0.1:{p}" for p in ports]
	leader_address = addresses[0]

	members = set(correlated_members or [])
	handles: list[NodeHandle] = []
	for i, (nid, addr) in enumerate(zip(node_ids, addresses)):
		h = build_node(
			node_id=nid,
			address=addr,
			peer_addresses=addresses,
			leader_address=leader_address,
			mode=mode,
			versioning=versioning,
			backend_kind=backend,
			data_dir=data_dir,
			w=w,
			r=r,
			fault_spec=fault_specs.get(nid),
			anti_entropy_interval=anti_entropy_interval,
			correlated_group=(correlated_group if nid in members else None),
		)
		handles.append(h)

	wait_ready(handles)
	return Cluster(nodes=handles, tmpdir=tmpdir)


def wait_ready(nodes: list[NodeHandle] | NodeHandle, timeout_s: float = 3.0) -> None:
	if isinstance(nodes, NodeHandle):
		nodes = [nodes]
	for n in nodes:
		deadline = time.time() + timeout_s
		while time.time() < deadline:
			try:
				resp = n.stub().Health(kvstore_pb2.HealthRequest(), timeout=0.5)
				if resp.serving:
					break
			except Exception:
				time.sleep(0.05)
		else:
			raise TimeoutError(f"node {n.node_id} never became ready")
