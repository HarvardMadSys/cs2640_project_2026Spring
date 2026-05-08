from __future__ import annotations

from threading import Lock


class FaultState:
	def __init__(self) -> None:
		self._enabled = True
		self._lock = Lock()

	def is_enabled(self) -> bool:
		with self._lock:
			return self._enabled

	def set_enabled(self, enabled: bool) -> None:
		with self._lock:
			self._enabled = enabled

