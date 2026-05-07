"""Metadata placement policies used by the benchmark runner."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Lock
from typing import Protocol


class PinBackend(Protocol):
    def set_pin(self, path: str, rank: int) -> None:
        ...


@dataclass(frozen=True)
class PolicyConfig:
    pin_ranks: tuple[int, ...] = ()
    hot_window: int = 128
    hot_threshold: int = 64
    hot_min_interval: int = 32
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class PolicyEvent:
    sequence: int
    policy: str
    action: str
    path: str
    rank: int | None
    reason: str

    def to_row(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "policy": self.policy,
            "action": self.action,
            "path": self.path,
            "rank": self.rank,
            "reason": self.reason,
        }


class MetadataPolicy:
    name = "none"

    def on_dirs_created(self, backend: PinBackend, dirs: list[str]) -> None:
        return None

    def before_operation(self, backend: PinBackend, operation: str, path: str) -> None:
        return None

    def events(self) -> list[PolicyEvent]:
        return []


class NoopPolicy(MetadataPolicy):
    name = "none"


class StaticSubtreePinningPolicy(MetadataPolicy):
    """Round-robin static export-pin baseline.

    This is the explicit static subtree-pinning baseline from the proposal. It
    pins benchmark-created subtrees immediately after directory creation and
    before benchmark operations begin.
    """

    name = "static"

    def __init__(self, config: PolicyConfig) -> None:
        self.pin_ranks = config.pin_ranks
        self._events: list[PolicyEvent] = []

    def on_dirs_created(self, backend: PinBackend, dirs: list[str]) -> None:
        if not self.pin_ranks:
            return
        for index, directory in enumerate(dirs):
            rank = self.pin_ranks[index % len(self.pin_ranks)]
            try:
                backend.set_pin(directory, rank)
            except Exception as exc:
                self._record("pin_failed", directory, rank, str(exc))
            else:
                self._record("pin", directory, rank, "round_robin_static_subtree_pin")

    def events(self) -> list[PolicyEvent]:
        return list(self._events)

    def _record(self, action: str, path: str, rank: int | None, reason: str) -> None:
        self._events.append(
            PolicyEvent(
                sequence=len(self._events) + 1,
                policy=self.name,
                action=action,
                path=path,
                rank=rank,
                reason=reason,
            )
        )


class PredictiveHotDirectoryPolicy(MetadataPolicy):
    """Simple sliding-window hot-directory predictor.

    The policy observes metadata operations by parent directory. Once a directory
    crosses `hot_threshold` operations inside the last `hot_window` observed
    operations, it export-pins that directory to the next configured rank. This
    is intentionally simple so we can iterate on policy files without changing
    the runner.
    """

    name = "predictive"

    def __init__(self, config: PolicyConfig) -> None:
        self.pin_ranks = config.pin_ranks
        self.hot_window = max(1, config.hot_window)
        self.hot_threshold = max(1, config.hot_threshold)
        self.hot_min_interval = max(1, config.hot_min_interval)
        self._recent_dirs: deque[str] = deque()
        self._counts: Counter[str] = Counter()
        self._pinned: dict[str, int] = {}
        self._last_pin_sequence: dict[str, int] = {}
        self._sequence = 0
        self._next_rank_index = 0
        self._events: list[PolicyEvent] = []
        self._lock = Lock()

    def before_operation(self, backend: PinBackend, operation: str, path: str) -> None:
        if not self.pin_ranks:
            return
        directory = parent_dir(path)
        with self._lock:
            self._sequence += 1
            self._recent_dirs.append(directory)
            self._counts[directory] += 1
            if len(self._recent_dirs) > self.hot_window:
                expired = self._recent_dirs.popleft()
                self._counts[expired] -= 1
                if self._counts[expired] <= 0:
                    del self._counts[expired]

            should_pin = (
                self._counts[directory] >= self.hot_threshold
                and directory not in self._pinned
                and self._sequence - self._last_pin_sequence.get(directory, 0)
                >= self.hot_min_interval
            )
            if not should_pin:
                return
            rank = self.pin_ranks[self._next_rank_index % len(self.pin_ranks)]
            self._next_rank_index += 1
            self._pinned[directory] = rank
            self._last_pin_sequence[directory] = self._sequence

        try:
            backend.set_pin(directory, rank)
        except Exception as exc:
            with self._lock:
                self._events.append(
                    PolicyEvent(
                        sequence=self._sequence,
                        policy=self.name,
                        action="pin_failed",
                        path=directory,
                        rank=rank,
                        reason=str(exc),
                    )
                )
            return
        with self._lock:
            self._events.append(
                PolicyEvent(
                    sequence=self._sequence,
                    policy=self.name,
                    action="pin",
                    path=directory,
                    rank=rank,
                    reason=(
                        f"hot_directory count={self._counts.get(directory, 0)} "
                        f"window={self.hot_window} threshold={self.hot_threshold} "
                        f"trigger_op={operation}"
                    ),
                )
            )

    def events(self) -> list[PolicyEvent]:
        with self._lock:
            return list(self._events)


def parent_dir(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "/" if parent == "." else parent


def build_policy(name: str, config: PolicyConfig) -> MetadataPolicy:
    if name == "none":
        return NoopPolicy()
    if name == "static":
        return StaticSubtreePinningPolicy(config)
    if name == "predictive":
        return PredictiveHotDirectoryPolicy(config)
    raise ValueError(f"unknown policy: {name}")
