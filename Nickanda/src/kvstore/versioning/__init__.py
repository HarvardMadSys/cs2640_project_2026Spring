from __future__ import annotations

from kvstore.versioning.base import Versioner
from kvstore.versioning.compare import (
	VectorOrdering,
	compare_vector_clocks,
	compare_versions,
)
from kvstore.versioning.lamport import LamportClock, LamportVersioner
from kvstore.versioning.vector_clock import VectorClockVersioner

__all__ = [
	"Versioner",
	"LamportClock",
	"LamportVersioner",
	"VectorClockVersioner",
	"VectorOrdering",
	"compare_vector_clocks",
	"compare_versions",
]


def make_versioner(name: str, node_id: str) -> Versioner:
	"""Factory used by node_main to pick a versioner at startup."""
	lowered = name.lower()
	if lowered in ("lamport", "lamport_ts"):
		return LamportVersioner(node_id)
	if lowered in ("vector", "vector_clock", "vc"):
		return VectorClockVersioner(node_id)
	raise ValueError(f"unknown versioning scheme: {name!r}")
