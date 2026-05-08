from __future__ import annotations

from enum import Enum

from kvstore.models import Version


class VectorOrdering(Enum):
	BEFORE = -1
	EQUAL = 0
	AFTER = 1
	CONCURRENT = 2


def _vec_map(v: tuple[tuple[str, int], ...]) -> dict[str, int]:
	return {nid: t for nid, t in v}


def compare_vector_clocks(
	a: tuple[tuple[str, int], ...],
	b: tuple[tuple[str, int], ...],
) -> VectorOrdering:
	"""Partial order on vector clocks.

	Returns AFTER if a dominates b, BEFORE if b dominates a, EQUAL if they
	are identical, CONCURRENT otherwise.
	"""
	am = _vec_map(a)
	bm = _vec_map(b)
	keys = set(am) | set(bm)
	a_greater = False
	b_greater = False
	for k in keys:
		av = am.get(k, 0)
		bv = bm.get(k, 0)
		if av > bv:
			a_greater = True
		elif av < bv:
			b_greater = True
	if a_greater and b_greater:
		return VectorOrdering.CONCURRENT
	if a_greater:
		return VectorOrdering.AFTER
	if b_greater:
		return VectorOrdering.BEFORE
	return VectorOrdering.EQUAL


def compare_versions(a: Version, b: Version) -> int:
	"""Total order over Versions suitable for last-writer-wins storage.

	If both versions carry a non-empty vector clock, we use the vector-clock
	partial order. Ties and concurrent updates fall back to (logical_time,
	node_id) as the LWW tiebreak, so this function is always total.
	"""
	if a.vector and b.vector:
		ordering = compare_vector_clocks(a.vector, b.vector)
		if ordering == VectorOrdering.AFTER:
			return 1
		if ordering == VectorOrdering.BEFORE:
			return -1
	if a.logical_time < b.logical_time:
		return -1
	if a.logical_time > b.logical_time:
		return 1
	if a.node_id < b.node_id:
		return -1
	if a.node_id > b.node_id:
		return 1
	return 0
