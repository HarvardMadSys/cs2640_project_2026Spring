from __future__ import annotations

import time

import grpc
import pytest

from kvstore.generated import kvstore_pb2
from tests._cluster import build_cluster


def _wait_follower_value(follower, key: str, expected: bytes, timeout_s: float = 2.0) -> bool:
	deadline = time.time() + timeout_s
	while time.time() < deadline:
		resp = follower.stub().Get(kvstore_pb2.GetRequest(key=key), timeout=1.0)
		if resp.found and resp.value == expected:
			return True
		time.sleep(0.02)
	return False


def test_leader_put_replicates_to_followers() -> None:
	cluster = build_cluster(n=3, mode="leader")
	try:
		leader, f1, f2 = cluster.nodes
		resp = leader.stub().Put(kvstore_pb2.PutRequest(key="k", value=b"hi"), timeout=1.0)
		assert resp.ok
		assert _wait_follower_value(f1, "k", b"hi")
		assert _wait_follower_value(f2, "k", b"hi")
	finally:
		cluster.stop()


def test_follower_rejects_direct_writes() -> None:
	cluster = build_cluster(n=3, mode="leader")
	try:
		_, follower, _ = cluster.nodes
		with pytest.raises(grpc.RpcError) as exc:
			follower.stub().Put(kvstore_pb2.PutRequest(key="x", value=b"nope"), timeout=1.0)
		assert exc.value.code() == grpc.StatusCode.FAILED_PRECONDITION
	finally:
		cluster.stop()


def test_delete_tombstone_propagates() -> None:
	cluster = build_cluster(n=3, mode="leader")
	try:
		leader, f1, _ = cluster.nodes
		leader.stub().Put(kvstore_pb2.PutRequest(key="d", value=b"v"), timeout=1.0)
		assert _wait_follower_value(f1, "d", b"v")
		leader.stub().Delete(kvstore_pb2.DeleteRequest(key="d"), timeout=1.0)
		deadline = time.time() + 2.0
		while time.time() < deadline:
			resp = f1.stub().Get(kvstore_pb2.GetRequest(key="d"), timeout=1.0)
			if not resp.found:
				break
			time.sleep(0.02)
		else:
			pytest.fail("follower never observed delete")
	finally:
		cluster.stop()


def test_disabled_follower_returns_unavailable() -> None:
	cluster = build_cluster(n=3, mode="leader")
	try:
		_, follower, _ = cluster.nodes
		follower.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		with pytest.raises(grpc.RpcError) as exc:
			follower.stub().Get(kvstore_pb2.GetRequest(key="anything"), timeout=1.0)
		assert exc.value.code() == grpc.StatusCode.UNAVAILABLE
		follower.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=True), timeout=1.0)
		resp = follower.stub().Get(kvstore_pb2.GetRequest(key="anything"), timeout=1.0)
		assert resp.found is False
	finally:
		cluster.stop()


def test_leader_succeeds_even_when_one_follower_disabled() -> None:
	cluster = build_cluster(n=3, mode="leader")
	try:
		leader, bad, good = cluster.nodes
		bad.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		resp = leader.stub().Put(
			kvstore_pb2.PutRequest(key="best_effort", value=b"v"),
			timeout=1.5,
		)
		assert resp.ok
		assert _wait_follower_value(good, "best_effort", b"v")
	finally:
		cluster.stop()
