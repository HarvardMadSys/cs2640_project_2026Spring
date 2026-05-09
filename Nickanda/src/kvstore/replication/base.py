from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from kvstore.models import Version


@dataclass
class CoordinatorResult:
	"""Outcome of a coordinator-side write (Put/Delete)."""

	ok: bool
	version: Version | None
	error: str = ""


@dataclass
class ReadReplicaResult:
	peer: str
	found: bool
	value: bytes | None
	version: Version | None
	is_tombstone: bool
	error: str | None


@dataclass
class CoordinatorReadResult:
	"""Outcome of a coordinator-side read (Get)."""

	found: bool
	value: bytes | None
	version: Version | None
	is_tombstone: bool
	replica_results: list[ReadReplicaResult]
	error: str = ""


class Replicator(ABC):
	"""Coordinator-side replication policy.

	Implementations decide what to do when a client-facing Put/Get/Delete
	arrives at this node. They have full authority to apply the change
	locally and to dispatch internal ``ReplicateWrite``/``FetchKey`` RPCs to
	peer nodes.
	"""

	@abstractmethod
	def coordinate_put(self, key: str, value: bytes) -> CoordinatorResult: ...

	@abstractmethod
	def coordinate_delete(self, key: str) -> CoordinatorResult: ...

	@abstractmethod
	def coordinate_get(self, key: str) -> CoordinatorReadResult: ...

	@abstractmethod
	def mode_name(self) -> str: ...
