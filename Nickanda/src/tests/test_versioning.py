from __future__ import annotations

from kvstore.models import Version
from kvstore.versioning.compare import (
	VectorOrdering,
	compare_vector_clocks,
	compare_versions,
)
from kvstore.versioning.lamport import LamportClock, LamportVersioner
from kvstore.versioning.vector_clock import VectorClockVersioner


def test_lamport_clock_ticks_monotonically() -> None:
	c = LamportClock("n1")
	v1 = c.tick()
	v2 = c.tick()
	assert v1.logical_time == 1
	assert v2.logical_time == 2
	assert v1.node_id == "n1" == v2.node_id


def test_lamport_observe_advances_clock() -> None:
	c = LamportClock("n1")
	c.observe(Version(logical_time=10, node_id="n2"))
	v = c.tick()
	assert v.logical_time == 11


def test_compare_versions_lamport_total_order() -> None:
	a = Version(logical_time=5, node_id="n1")
	b = Version(logical_time=5, node_id="n2")
	c = Version(logical_time=6, node_id="n1")
	assert compare_versions(a, b) < 0  # node_id tiebreak
	assert compare_versions(b, a) > 0
	assert compare_versions(a, c) < 0
	assert compare_versions(c, a) > 0
	assert compare_versions(a, a) == 0


def test_vector_clock_concurrent_vs_ordered() -> None:
	v_a = (("n1", 2), ("n2", 1))
	v_b = (("n1", 1), ("n2", 2))
	v_c = (("n1", 2), ("n2", 2))
	assert compare_vector_clocks(v_a, v_b) == VectorOrdering.CONCURRENT
	assert compare_vector_clocks(v_c, v_a) == VectorOrdering.AFTER
	assert compare_vector_clocks(v_a, v_c) == VectorOrdering.BEFORE
	assert compare_vector_clocks(v_c, v_c) == VectorOrdering.EQUAL


def test_vector_clock_versioner_tick_increments_own_entry() -> None:
	v = VectorClockVersioner("n1")
	ver1 = v.tick()
	ver2 = v.tick()
	assert ver1.logical_time == 1
	assert ver2.logical_time == 2
	assert dict(ver2.vector)["n1"] == 2


def test_vector_clock_versioner_observe_merges_components() -> None:
	v = VectorClockVersioner("n1")
	v.observe(Version(logical_time=3, node_id="n2", vector=(("n1", 0), ("n2", 3))))
	ver = v.tick()
	assert dict(ver.vector)["n1"] == 1
	assert dict(ver.vector)["n2"] == 3


def test_compare_versions_uses_vector_when_both_have_it() -> None:
	a = Version(logical_time=1, node_id="n1", vector=(("n1", 2), ("n2", 1)))
	b = Version(logical_time=1, node_id="n2", vector=(("n1", 2), ("n2", 2)))
	assert compare_versions(a, b) < 0
	assert compare_versions(b, a) > 0


def test_compare_versions_concurrent_falls_back_to_lamport_tiebreak() -> None:
	a = Version(logical_time=7, node_id="n1", vector=(("n1", 2), ("n2", 1)))
	b = Version(logical_time=7, node_id="n2", vector=(("n1", 1), ("n2", 2)))
	# Concurrent vectors => LWW by (logical_time, node_id).
	assert compare_versions(a, b) < 0  # n1 < n2 in node_id tiebreak
	assert compare_versions(b, a) > 0


def test_lamport_versioner_name() -> None:
	assert LamportVersioner("n1").name() == "lamport"


def test_vector_versioner_name() -> None:
	assert VectorClockVersioner("n1").name() == "vector_clock"
