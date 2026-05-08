from __future__ import annotations

from abc import ABC, abstractmethod

from kvstore.models import Version


class Versioner(ABC):
	"""Abstraction over how we produce and consume version metadata.

	Implementations are thread-safe. ``tick()`` returns the next version for a
	local write. ``observe()`` merges an incoming remote version so that our
	next tick is strictly greater.
	"""

	@property
	@abstractmethod
	def node_id(self) -> str: ...

	@abstractmethod
	def tick(self) -> Version:
		"""Advance local clock and return a fresh Version for a new write."""

	@abstractmethod
	def observe(self, version: Version) -> None:
		"""Merge an incoming version into local state."""

	def name(self) -> str:
		return self.__class__.__name__
