"""Pre-pin a small declared hot set.

This is a simple production-style baseline: if an operator or higher-level
scheduler already knows the first few top-level subtrees will be hot, pin just
those subtrees before the measured workload starts.
"""

from pathlib import PurePosixPath

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyEvent


class PrepinHotsetPolicy:
    name = "prepin_hotset"

    def __init__(self, config):
        self.pin_ranks = config.pin_ranks
        self.hot_count = int(config.options.get("hot_count", "1"))
        self._events = []

    def on_dirs_created(self, backend, dirs):
        if not self.pin_ranks or not dirs or self.hot_count <= 0:
            return
        root = dirs[0].rstrip("/")
        root_depth = len(PurePosixPath(root).parts)
        top_dirs = [
            directory
            for directory in dirs[1:]
            if len(PurePosixPath(directory.rstrip("/")).parts) - root_depth == 1
        ]
        for index, directory in enumerate(top_dirs[: self.hot_count]):
            rank = self.pin_ranks[index % len(self.pin_ranks)]
            try:
                backend.set_pin(directory, rank)
            except Exception as exc:
                self._record("pin_failed", directory, rank, str(exc))
            else:
                self._record("pin", directory, rank, f"declared_hotset index={index}")

    def before_operation(self, backend, operation, path):
        return None

    def events(self):
        return list(self._events)

    def _record(self, action, path, rank, reason):
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


def create_policy(config):
    return PrepinHotsetPolicy(config)
