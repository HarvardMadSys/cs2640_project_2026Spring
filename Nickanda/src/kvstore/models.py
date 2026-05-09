from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Version:
	logical_time: int
	node_id: str
	# Canonical vector-clock representation: tuple of (node_id, time) sorted
	# by node_id. Empty tuple means "no vector-clock info", in which case
	# comparison falls back to (logical_time, node_id).
	vector: tuple[tuple[str, int], ...] = field(default_factory=tuple)


@dataclass
class ValueRecord:
	value: bytes | None
	version: Version
	is_tombstone: bool = False
