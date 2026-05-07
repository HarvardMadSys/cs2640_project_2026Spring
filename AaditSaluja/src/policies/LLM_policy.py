"""Heuristic policy inspired by LLM-generated placement reasoning.

No model is called at runtime. The idea is to encode the kind of rule an LLM
suggested for this project: distinguish broad fanout workloads from concentrated
hot-directory workloads, then avoid over-pinning until the signal is strong.
"""

from collections import Counter, deque
from pathlib import PurePosixPath
from threading import Lock

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyEvent


class LLMPolicy:
    name = "LLM_policy"

    def __init__(self, config):
        self.pin_ranks = config.pin_ranks
        self.window = int(config.options.get("window", config.hot_window))
        self.hot_threshold = int(config.options.get("hot_threshold", config.hot_threshold))
        self.fanout_threshold = int(config.options.get("fanout_threshold", 8))
        self.cooldown = int(config.options.get("cooldown", config.hot_min_interval))
        self.recent = deque()
        self.counts = Counter()
        self.children_by_parent = {}
        self.pinned = {}
        self.sequence = 0
        self.next_rank = 0
        self._events = []
        self._lock = Lock()

    def on_dirs_created(self, backend, dirs):
        del backend
        for directory in dirs:
            parent = parent_dir(directory)
            self.children_by_parent.setdefault(parent, set()).add(directory)

    def before_operation(self, backend, operation, path):
        if not self.pin_ranks:
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
            fanout = len(self.children_by_parent.get(directory, ()))
            concentrated_hot = self.counts[directory] >= self.hot_threshold
            broad_fanout = fanout >= self.fanout_threshold
            cooldown_ok = self.sequence - self.pinned.get(directory, (-1, -self.cooldown))[1] >= self.cooldown
            if directory in self.pinned or not cooldown_ok:
                return
            if concentrated_hot:
                reason = "concentrated_hot_directory"
            elif broad_fanout and self.counts[directory] >= max(2, self.hot_threshold // 2):
                reason = "broad_fanout_parent_with_repeated_access"
            else:
                return
            rank = self.pin_ranks[self.next_rank % len(self.pin_ranks)]
            self.next_rank += 1
            self.pinned[directory] = (rank, self.sequence)
        try:
            backend.set_pin(directory, rank)
        except Exception as exc:
            action = "pin_failed"
            event_reason = str(exc)
        else:
            action = "pin"
            event_reason = (
                f"{reason} count={self.counts.get(directory, 0)} "
                f"fanout={fanout} window={self.window}"
            )
        with self._lock:
            self._events.append(
                PolicyEvent(
                    sequence=self.sequence,
                    policy=self.name,
                    action=action,
                    path=directory,
                    rank=rank,
                    reason=event_reason,
                )
            )

    def events(self):
        with self._lock:
            return list(self._events)


def parent_dir(path):
    parent = str(PurePosixPath(path).parent)
    return "/" if parent == "." else parent


def create_policy(config):
    return LLMPolicy(config)
