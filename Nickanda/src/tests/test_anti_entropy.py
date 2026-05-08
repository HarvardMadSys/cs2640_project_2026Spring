from __future__ import annotations

import time

from kvstore.generated import kvstore_pb2
from tests._cluster import build_cluster


def test_anti_entropy_heals_partitioned_follower_leader_mode() -> None:
	cluster = build_cluster(
		n=3,
		mode="leader",
		anti_entropy_interval=0.1,
	)
	try:
		leader, follower_a, follower_b = cluster.nodes

		# Disable follower_b so the write only reaches leader and follower_a.
		follower_b.stub().SetNodeState(
			kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0
		)
		resp = leader.stub().Put(
			kvstore_pb2.PutRequest(key="ae_key", value=b"payload"), timeout=1.5
		)
		assert resp.ok
		assert follower_b.backend.get("ae_key") is None

		# Bring it back; anti-entropy should pull the key from its peers.
		follower_b.stub().SetNodeState(
			kvstore_pb2.SetNodeStateRequest(enabled=True), timeout=1.0
		)
		deadline = time.time() + 5.0
		while time.time() < deadline:
			rec = follower_b.backend.get("ae_key")
			if rec is not None and rec.value == b"payload":
				break
			time.sleep(0.1)
		else:
			raise AssertionError("anti-entropy never healed the partitioned follower")

		counters = follower_b.metrics.counters()
		assert counters.anti_entropy_rounds >= 1
	finally:
		cluster.stop()


def test_anti_entropy_bidirectional_convergence_quorum_mode() -> None:
	cluster = build_cluster(
		n=3,
		mode="quorum",
		w=2,
		r=2,
		anti_entropy_interval=0.1,
	)
	try:
		a, b, c = cluster.nodes

		# Write on a with c disabled, so a+b have kA.
		c.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		a.stub().Put(kvstore_pb2.PutRequest(key="kA", value=b"A"), timeout=1.5)

		# Re-enable c, disable a, write on b so b+c have kB.
		c.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=True), timeout=1.0)
		a.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		b.stub().Put(kvstore_pb2.PutRequest(key="kB", value=b"B"), timeout=1.5)
		a.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=True), timeout=1.0)

		# Wait for convergence on both keys across all three nodes.
		deadline = time.time() + 6.0
		while time.time() < deadline:
			have_a_kA = a.backend.get("kA") is not None
			have_a_kB = a.backend.get("kB") is not None
			have_b_kA = b.backend.get("kA") is not None
			have_b_kB = b.backend.get("kB") is not None
			have_c_kA = c.backend.get("kA") is not None
			have_c_kB = c.backend.get("kB") is not None
			if all([have_a_kA, have_a_kB, have_b_kA, have_b_kB, have_c_kA, have_c_kB]):
				break
			time.sleep(0.1)
		else:
			raise AssertionError("nodes did not converge via anti-entropy")
	finally:
		cluster.stop()
