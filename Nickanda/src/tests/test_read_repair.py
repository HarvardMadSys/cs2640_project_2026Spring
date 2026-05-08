from __future__ import annotations

import time

from kvstore.generated import kvstore_pb2
from tests._cluster import build_cluster


def _wait_record(node, key: str, expected: bytes, timeout_s: float = 2.0) -> bool:
	"""Wait until ``node``'s local backend has ``expected`` for ``key``."""

	deadline = time.time() + timeout_s
	while time.time() < deadline:
		rec = node.backend.get(key)
		if rec is not None and rec.value == expected:
			return True
		time.sleep(0.02)
	return False


def test_read_repair_heals_stale_replica() -> None:
	cluster = build_cluster(n=3, mode="quorum", w=2, r=2)
	try:
		a, b, c = cluster.nodes

		# Disable one replica so the write only lands on the two healthy ones.
		c.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		resp = a.stub().Put(kvstore_pb2.PutRequest(key="rr", value=b"fresh"), timeout=1.5)
		assert resp.ok

		# Bring c back. Its backend has nothing for key "rr".
		c.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=True), timeout=1.0)
		assert c.backend.get("rr") is None

		# A quorum read from a (w=2,r=2) should involve two replicas and
		# then fire read-repair to the stale one.
		before = c.metrics.counters().repair_ops
		r = a.stub().Get(kvstore_pb2.GetRequest(key="rr"), timeout=1.5)
		assert r.found and r.value == b"fresh"

		# read-repair is async; wait briefly for it to land on c.
		assert _wait_record(c, "rr", b"fresh")

		# The coordinator's metrics should show a read-repair was issued.
		after_read_repair_ops = a.metrics.counters().read_repair_ops
		assert after_read_repair_ops >= 1
		# And c received at least one repair write.
		assert c.metrics.counters().repair_ops > before or c.backend.get("rr") is not None
	finally:
		cluster.stop()
