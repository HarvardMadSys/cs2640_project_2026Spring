"""Conservative predictive hot-directory policy.

Compared with `predictive`, this variant only reacts to create-heavy signals and
uses a higher default threshold. It is meant to avoid subtree migration during
read/stat/delete phases, where the first CloudLab run showed reactive pinning
could dominate the measured workload.
"""

from collections import Counter, deque
from pathlib import PurePosixPath
from threading import Lock

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyEvent


CREATE_OPS = {"create", "skewed_create", "initial_create", "mixed_create"}


class PredictiveSafePolicy:
    name = "predictive_safe"

    def __init__(self, config):
        self.pin_ranks = config.pin_ranks
        self.window = int(config.options.get("window", max(config.hot_window, 256)))
        self.threshold = int(config.options.get("threshold", max(config.hot_threshold, 128)))
        self.min_sequence = int(config.options.get("min_sequence", self.threshold))
        self.recent = deque()
        self.counts = Counter()
        self.pinned = {}
        self.sequence = 0
        self.next_rank = 0
        self._events = []
        self._lock = Lock()

    def on_dirs_created(self, backend, dirs):
        return None

    def before_operation(self, backend, operation, path):
        if not self.pin_ranks or operation not in CREATE_OPS:
            return
        directory = parent_dir(path)
        with self._lock:
            self.sequence += 1
            self.recent.append(directory)
            self.counts[directory] += 1
            if len(self.recent) > self.window:
                expired = self.recent.popleft()
                self.counts[expired] -= 1
                if self.counts[expired] <= 0:
                    del self.counts[expired]
            if directory in self.pinned:
                return
            if self.sequence < self.min_sequence:
                return
            if self.counts[directory] < self.threshold:
                return
            rank = self.pin_ranks[self.next_rank % len(self.pin_ranks)]
            self.next_rank += 1
            self.pinned[directory] = rank
        try:
            backend.set_pin(directory, rank)
        except Exception as exc:
            self._record("pin_failed", directory, rank, str(exc))
        else:
            self._record(
                "pin",
                directory,
                rank,
                f"safe_create_hot count={self.counts.get(directory, 0)} window={self.window}",
            )

    def events(self):
        with self._lock:
            return list(self._events)

    def _record(self, action, path, rank, reason):
        with self._lock:
            self._events.append(
                PolicyEvent(
                    sequence=self.sequence,
                    policy=self.name,
                    action=action,
                    path=path,
                    rank=rank,
                    reason=reason,
                )
            )


def parent_dir(path):
    parent = str(PurePosixPath(path).parent)
    return "/" if parent == "." else parent


def create_policy(config):
    return PredictiveSafePolicy(config)
