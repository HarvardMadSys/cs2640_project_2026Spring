from __future__ import annotations

from threading import Lock

import grpc

from kvstore.generated import kvstore_pb2_grpc


_CHANNEL_CACHE: dict[str, grpc.Channel] = {}
_CHANNEL_LOCK = Lock()


def make_channel(address: str) -> grpc.Channel:
	"""Return a cached insecure channel for ``address``.

	gRPC channels are thread-safe and reusable, so caching avoids paying the
	connection-setup cost on every RPC in benchmarks and anti-entropy loops.
	"""

	with _CHANNEL_LOCK:
		ch = _CHANNEL_CACHE.get(address)
		if ch is None:
			ch = grpc.insecure_channel(address)
			_CHANNEL_CACHE[address] = ch
		return ch


def make_stub(address: str) -> kvstore_pb2_grpc.KVStoreStub:
	return kvstore_pb2_grpc.KVStoreStub(make_channel(address))


def reset_channel_cache() -> None:
	"""Close and forget all cached channels. Used by tests to avoid bleed."""

	with _CHANNEL_LOCK:
		for ch in _CHANNEL_CACHE.values():
			try:
				ch.close()
			except Exception:
				pass
		_CHANNEL_CACHE.clear()
