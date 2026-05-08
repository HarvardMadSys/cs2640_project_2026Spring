from __future__ import annotations

import argparse
import signal
from concurrent import futures

import grpc

from kvstore.config import NodeConfig
from kvstore.fault import FaultState
from kvstore.generated import kvstore_pb2_grpc
from kvstore.metrics import MetricsCollector
from kvstore.replication.anti_entropy import AntiEntropyService
from kvstore.replication.base import Replicator
from kvstore.replication.leader import LeaderReplicator
from kvstore.replication.quorum import QuorumReplicator
from kvstore.service import KVStoreService
from kvstore.storage import make_backend
from kvstore.storage.base import StorageBackend
from kvstore.storage.faulty_wrapper import FaultSpec, FaultyStorageWrapper
from kvstore.versioning import make_versioner
from kvstore.versioning.base import Versioner


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run a KVStore node")
	parser.add_argument("--node-id", required=True)
	parser.add_argument("--bind", required=True, help="host:port")
	parser.add_argument("--leader", required=True, help="leader host:port")
	parser.add_argument("--peers", default="", help="comma-separated host:port list")
	parser.add_argument(
		"--mode",
		default="leader",
		choices=["leader", "quorum"],
		help="replication mode",
	)
	parser.add_argument(
		"--versioning",
		default="lamport",
		choices=["lamport", "vector"],
		help="versioning scheme",
	)
	parser.add_argument(
		"--backend",
		default="memory",
		choices=["memory", "sqlite"],
		help="storage backend",
	)
	parser.add_argument("--data-dir", default="", help="data directory for sqlite backend")
	parser.add_argument("--w", type=int, default=2, help="write quorum size")
	parser.add_argument("--r", type=int, default=2, help="read quorum size")
	parser.add_argument("--storage-delay-ms", type=float, default=0.0)
	parser.add_argument("--storage-stall-prob", type=float, default=0.0)
	parser.add_argument("--storage-stall-ms", type=float, default=0.0)
	parser.add_argument("--storage-fail-slow-period", type=int, default=0)
	parser.add_argument("--storage-fail-slow-burst-ms", type=float, default=0.0)
	parser.add_argument("--storage-fail-slow-burst-len", type=int, default=0)
	parser.add_argument("--anti-entropy-interval", type=float, default=0.0)
	return parser.parse_args()


def build_backend(cfg: NodeConfig) -> StorageBackend:
	raw = make_backend(cfg.backend, data_dir=cfg.data_dir, node_id=cfg.node_id)
	spec = FaultSpec(
		steady_delay_ms=cfg.storage_delay_ms,
		fail_slow_period_ops=cfg.storage_fail_slow_period,
		fail_slow_burst_ms=cfg.storage_fail_slow_burst_ms,
		fail_slow_burst_len=cfg.storage_fail_slow_burst_len,
		stall_probability=cfg.storage_stall_prob,
		stall_ms=cfg.storage_stall_ms,
		seed=cfg.storage_seed,
	)
	any_fault = (
		spec.steady_delay_ms > 0
		or (spec.fail_slow_period_ops > 0 and spec.fail_slow_burst_len > 0)
		or (spec.stall_probability > 0 and spec.stall_ms > 0)
	)
	if any_fault:
		return FaultyStorageWrapper(inner=raw, spec=spec)
	return raw


def build_replicator(
	cfg: NodeConfig,
	backend: StorageBackend,
	versioner: Versioner,
	metrics: MetricsCollector,
) -> Replicator:
	if cfg.mode == "leader":
		return LeaderReplicator(
			node_id=cfg.node_id,
			self_address=cfg.bind_address,
			leader_address=cfg.leader_address,
			peers=cfg.peers,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
		)
	if cfg.mode == "quorum":
		return QuorumReplicator(
			node_id=cfg.node_id,
			self_address=cfg.bind_address,
			peers=cfg.peers,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
			w=cfg.w,
			r=cfg.r,
		)
	raise ValueError(f"unknown mode: {cfg.mode}")


def restore_versioner_from_backend(versioner: Versioner, backend: StorageBackend) -> None:
	"""After a crash/restart, bump our clock past the max observed time."""

	max_t = backend.max_logical_time()
	if max_t > 0:
		from kvstore.models import Version

		versioner.observe(Version(logical_time=max_t, node_id=versioner.node_id))


def serve(cfg: NodeConfig) -> None:
	server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))

	backend = build_backend(cfg)
	versioner = make_versioner(cfg.versioning, cfg.node_id)
	restore_versioner_from_backend(versioner, backend)
	metrics = MetricsCollector()
	fault_state = FaultState()
	replicator = build_replicator(cfg, backend, versioner, metrics)

	svc = KVStoreService(
		cfg=cfg,
		backend=backend,
		versioner=versioner,
		replicator=replicator,
		metrics=metrics,
		fault_state=fault_state,
	)
	kvstore_pb2_grpc.add_KVStoreServicer_to_server(svc, server)
	server.add_insecure_port(cfg.bind_address)
	server.start()

	ae_service: AntiEntropyService | None = None
	if cfg.anti_entropy_interval_sec > 0:
		ae_service = AntiEntropyService(
			node_id=cfg.node_id,
			self_address=cfg.bind_address,
			peers=cfg.peers,
			backend=backend,
			versioner=versioner,
			metrics=metrics,
			interval_sec=cfg.anti_entropy_interval_sec,
		)
		ae_service.start()

	print(
		f"node={cfg.node_id} bind={cfg.bind_address} mode={cfg.mode} "
		f"versioning={cfg.versioning} backend={cfg.backend} "
		f"w={cfg.w} r={cfg.r}"
	)

	def _graceful(_sig, _frame):  # noqa: ANN001
		if ae_service is not None:
			ae_service.stop()
		server.stop(grace=1.0)

	signal.signal(signal.SIGTERM, _graceful)
	signal.signal(signal.SIGINT, _graceful)
	try:
		server.wait_for_termination()
	finally:
		if ae_service is not None:
			ae_service.stop()
		backend.close()


def main() -> None:
	args = parse_args()
	peers = [p.strip() for p in args.peers.split(",") if p.strip()]
	cfg = NodeConfig(
		node_id=args.node_id,
		bind_address=args.bind,
		peers=peers,
		leader_address=args.leader,
		mode=args.mode,
		versioning=args.versioning,
		backend=args.backend,
		data_dir=args.data_dir or None,
		w=args.w,
		r=args.r,
		storage_delay_ms=args.storage_delay_ms,
		storage_stall_prob=args.storage_stall_prob,
		storage_stall_ms=args.storage_stall_ms,
		storage_fail_slow_period=args.storage_fail_slow_period,
		storage_fail_slow_burst_ms=args.storage_fail_slow_burst_ms,
		storage_fail_slow_burst_len=args.storage_fail_slow_burst_len,
		anti_entropy_interval_sec=args.anti_entropy_interval,
	)
	serve(cfg)


if __name__ == "__main__":
	main()
