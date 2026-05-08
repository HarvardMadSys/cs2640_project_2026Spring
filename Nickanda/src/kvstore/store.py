"""Backward-compat shim.

The original milestone-1 MVP had a single ``InMemoryStore`` class here. It
now lives in ``kvstore.storage.memory_backend`` behind the ``StorageBackend``
ABC. This module re-exports the class under the old name with an
``operation_delay_ms`` kwarg for existing callers.
"""

from __future__ import annotations

from kvstore.storage.faulty_wrapper import FaultSpec, FaultyStorageWrapper
from kvstore.storage.memory_backend import InMemoryBackend


def InMemoryStore(operation_delay_ms: float = 0.0):  # noqa: N802 - keep legacy name
	"""Legacy factory returning either a plain InMemoryBackend or a wrapper."""

	backend = InMemoryBackend()
	if operation_delay_ms and operation_delay_ms > 0:
		return FaultyStorageWrapper(
			inner=backend,
			spec=FaultSpec(steady_delay_ms=float(operation_delay_ms)),
		)
	return backend


__all__ = ["InMemoryStore"]
