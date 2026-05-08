from __future__ import annotations

import time

import grpc
import pytest

from kvstore.generated import kvstore_pb2
from tests._cluster import build_cluster


def test_quorum_w2_r2_round_trip() -> None:
	cluster = build_cluster(n=3, mode="quorum", w=2, r=2)
	try:
		a, b, c = cluster.nodes
		resp = a.stub().Put(kvstore_pb2.PutRequest(key="k", value=b"val"), timeout=1.5)
		assert resp.ok
		# Read from any node should now return the value.
		for node in (a, b, c):
			deadline = time.time() + 2.0
			while time.time() < deadline:
				r = node.stub().Get(kvstore_pb2.GetRequest(key="k"), timeout=1.5)
				if r.found and r.value == b"val":
					break
				time.sleep(0.02)
			else:
				pytest.fail(f"{node.node_id} never served quorum read")
	finally:
		cluster.stop()


def test_quorum_w2_tolerates_one_disabled_follower() -> None:
	cluster = build_cluster(n=3, mode="quorum", w=2, r=2)
	try:
		coord, bad, good = cluster.nodes
		bad.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		resp = coord.stub().Put(kvstore_pb2.PutRequest(key="q", value=b"v"), timeout=1.5)
		assert resp.ok
		deadline = time.time() + 2.0
		while time.time() < deadline:
			r = good.stub().Get(kvstore_pb2.GetRequest(key="q"), timeout=1.5)
			if r.found and r.value == b"v":
				break
			time.sleep(0.02)
		else:
			pytest.fail("healthy replica never saw the write")
	finally:
		cluster.stop()


def test_quorum_w3_fails_when_one_disabled() -> None:
	cluster = build_cluster(n=3, mode="quorum", w=3, r=1)
	try:
		coord, bad, _ = cluster.nodes
		bad.stub().SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=False), timeout=1.0)
		with pytest.raises(grpc.RpcError) as exc:
			coord.stub().Put(
				kvstore_pb2.PutRequest(key="should_fail", value=b"x"),
				timeout=2.0,
			)
		assert exc.value.code() == grpc.StatusCode.FAILED_PRECONDITION
	finally:
		cluster.stop()


def test_quorum_delete_tombstone_visible_to_readers() -> None:
	cluster = build_cluster(n=3, mode="quorum", w=2, r=2)
	try:
		a, b, c = cluster.nodes
		a.stub().Put(kvstore_pb2.PutRequest(key="d", value=b"x"), timeout=1.5)
		a.stub().Delete(kvstore_pb2.DeleteRequest(key="d"), timeout=1.5)
		deadline = time.time() + 2.0
		while time.time() < deadline:
			r = c.stub().Get(kvstore_pb2.GetRequest(key="d"), timeout=1.5)
			if not r.found:
				break
			time.sleep(0.02)
		else:
			pytest.fail("delete never visible via quorum read")
	finally:
		cluster.stop()


def test_quorum_vector_clock_mode_round_trip() -> None:
	cluster = build_cluster(n=3, mode="quorum", versioning="vector", w=2, r=2)
	try:
		a, _, _ = cluster.nodes
		resp = a.stub().Put(kvstore_pb2.PutRequest(key="vc", value=b"v"), timeout=1.5)
		assert resp.ok
		assert len(resp.version.vector) >= 1
	finally:
		cluster.stop()
