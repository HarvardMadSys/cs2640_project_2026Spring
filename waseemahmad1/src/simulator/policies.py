"""Eviction policies for the KV-cache simulator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from typing import Deque, Optional, Set


class EvictionPolicy(ABC):
    """Interface for cache eviction policies."""

    @abstractmethod
    def on_access(self, key: str, time: int) -> None:
        """Handle block access."""

    @abstractmethod
    def on_insert(self, key: str, time: int) -> None:
        """Handle block insertion."""

    @abstractmethod
    def on_evict(self, key: str) -> None:
        """Handle block eviction."""

    @abstractmethod
    def choose_victim(self) -> Optional[str]:
        """Choose which key to evict next."""


class LRUPolicy(EvictionPolicy):
    """Least-recently-used eviction policy."""

    def __init__(self) -> None:
        self._order: "OrderedDict[str, int]" = OrderedDict()

    def on_access(self, key: str, time: int) -> None:
        if key in self._order:
            self._order.move_to_end(key)
            self._order[key] = time

    def on_insert(self, key: str, time: int) -> None:
        if key in self._order:
            self._order.move_to_end(key)
        self._order[key] = time

    def on_evict(self, key: str) -> None:
        self._order.pop(key, None)

    def choose_victim(self) -> Optional[str]:
        if not self._order:
            return None
        oldest_key = next(iter(self._order))
        return oldest_key


class FIFOPolicy(EvictionPolicy):
    """First-in-first-out eviction policy."""

    def __init__(self) -> None:
        self._queue: Deque[str] = deque()
        self._present: Set[str] = set()

    def on_access(self, key: str, time: int) -> None:
        del key, time

    def on_insert(self, key: str, time: int) -> None:
        _ = time
        if key in self._present:
            return
        self._queue.append(key)
        self._present.add(key)

    def on_evict(self, key: str) -> None:
        if key in self._present:
            self._present.remove(key)
        while self._queue and self._queue[0] not in self._present:
            self._queue.popleft()

    def choose_victim(self) -> Optional[str]:
        while self._queue and self._queue[0] not in self._present:
            self._queue.popleft()
        if not self._queue:
            return None
        return self._queue[0]
