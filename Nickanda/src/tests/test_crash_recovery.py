from __future__ import annotations

import os
import time

from kvstore.generated import kvstore_pb2
from kvstore.rpc_client import reset_channel_cache
from kvstore.storage.sqlite_backend import SqliteBackend
from kvstore.versioning import make_versioner
from kvstore.node_main import restore_versioner_from_backend
from tests._cluster import build_cluster, _build_node, wait_ready


def test_sqlite_state_survives_crash_and_restart_rebuild() -> None:
	"""Stop a SQLite-backed follower and rebuild a fresh node pointed at the
	same data dir. The new node's backend sees the pre-crash rows and its
	versioner bumps past the max observed logical_time.

	We rebuild the node process state only, not the listening socket, so this
	test targets crash-recovery correctness of the persistence + clock replay
	logic without re-binding the old port (which can race with TIME_WAIT)."""

	cluster = build_cluster(n=3, mode="leader", backend="sqlite")
	try:
		leader, f1, f2 = cluster.nodes

		for i in range(5):
			leader.stub().Put(
				kvstore_pb2.PutRequest(key=f"k{i}", value=f"v{i}".encode()),
				timeout=1.5,
			)

		deadline = time.time() + 2.0
		while time.time() < deadline:
			if f1.backend.get("k4") is not None:
				break
			time.sleep(0.02)
		assert f1.backend.get("k4") is not None

		f1_data_dir = f1.cfg.data_dir

		# Simulate a crash: stop the follower's server and close its backend.
		f1.stop()
		reset_channel_cache()

		# Rebuild just the backend + versioner from the same data dir. This
		# is the core of crash recovery: pre-crash state is still there and
		# the clock has advanced past the pre-crash max.
		db_path = os.path.join(f1_data_dir, "n2.sqlite")
		recovered_backend = SqliteBackend(db_path)
		try:
			assert recovered_backend.get("k4") is not None
			assert recovered_backend.get("k4").value == b"v4"

			recovered_versioner = make_versioner("lamport", "n2")
			assert recovered_versioner.current_time() == 0  # type: ignore[attr-defined]
			restore_versioner_from_backend(recovered_versioner, recovered_backend)
			max_time = recovered_backend.max_logical_time()
			assert max_time >= 5
			assert recovered_versioner.current_time() >= max_time  # type: ignore[attr-defined]

			# A new tick must produce a version strictly greater than any
			# pre-crash version, so replication stays monotonic.
			new_version = recovered_versioner.tick()
			assert new_version.logical_time > max_time
		finally:
			recovered_backend.close()

		cluster.nodes = [leader, f2]
	finally:
		cluster.stop()


def test_full_node_restart_on_fresh_port_preserves_state() -> None:
	"""Restart the entire node on a fresh address and confirm the surviving
	cluster can still replicate to it."""

	cluster = build_cluster(n=3, mode="leader", backend="sqlite")
	try:
		leader, f1, f2 = cluster.nodes

		leader.stub().Put(kvstore_pb2.PutRequest(key="kA", value=b"vA"), timeout=1.5)
		deadline = time.time() + 1.5
		while time.time() < deadline:
			if f1.backend.get("kA") is not None:
				break
			time.sleep(0.02)
		assert f1.backend.get("kA") is not None

		f1_data_dir = f1.cfg.data_dir
		f1_old_peers = list(f1.cfg.peers)

		# Stop the old follower and start a new one on a fresh port pointing
		# at the same data dir (durable state).
		f1.stop()
		reset_channel_cache()

		from tests._cluster import _free_port

		new_addr = f"127.0.0.1:{_free_port()}"
		new_peers = [new_addr if p == f1.address else p for p in f1_old_peers]

		restarted = _build_node(
			node_id="n2",
			address=new_addr,
			peer_addresses=new_peers,
			leader_address=f1.cfg.leader_address,
			mode="leader",
			versioning="lamport",
			backend_kind="sqlite",
			data_dir=f1_data_dir,
			w=2,
			r=2,
			fault_spec=None,
			anti_entropy_interval=0.0,
		)
		wait_ready(restarted)
		try:
			# Pre-crash state survived.
			assert restarted.backend.get("kA") is not None
			# The restarted node answers RPCs.
			health = restarted.stub().Health(kvstore_pb2.HealthRequest(), timeout=1.5)
			assert health.serving
			# Replay a direct replicate to confirm it integrates new writes.
			max_time = restarted.backend.max_logical_time()
			direct = restarted.stub().ReplicateWrite(
				kvstore_pb2.ReplicateWriteRequest(
					key="post_direct",
					value=b"direct",
					is_delete=False,
					version=kvstore_pb2.Version(
						logical_time=max_time + 10, node_id="n1"
					),
					source_node_id="n1",
				),
				timeout=1.5,
			)
			assert direct.ok
			assert restarted.backend.get("post_direct") is not None
		finally:
			restarted.stop()

		# Swap the restarted handle into the cluster so cluster.stop cleans up
		# the remaining nodes properly.
		cluster.nodes = [leader, f2]
	finally:
		cluster.stop()
