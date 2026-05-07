"""Pre-pin the workload root and declared hot subtrees to one rank.

This tests a specific diagnosis from the CloudLab runs: pinning a hot child
away from its parent can introduce cross-rank forwarding. Co-locating the root
and hot child is a conservative fixed approach for known-skew workloads.
"""

from pathlib import PurePosixPath

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyEvent


class PrepinColocatedHotsetPolicy:
    name = "prepin_colocated_hotset"

    def __init__(self, config):
        self.pin_ranks = config.pin_ranks
        self.hot_count = int(config.options.get("hot_count", "1"))
        self._events = []

    def on_dirs_created(self, backend, dirs):
        if not self.pin_ranks or not dirs:
            return
        rank = self.pin_ranks[0]
        root = dirs[0].rstrip("/")
        self._pin(backend, root, rank, "colocate_root")
        if self.hot_count <= 0:
            return
        root_depth = len(PurePosixPath(root).parts)
        top_dirs = [
            directory
            for directory in dirs[1:]
            if len(PurePosixPath(directory.rstrip("/")).parts) - root_depth == 1
        ]
        for index, directory in enumerate(top_dirs[: self.hot_count]):
            self._pin(backend, directory, rank, f"colocate_declared_hotset index={index}")

    def before_operation(self, backend, operation, path):
        return None

    def events(self):
        return list(self._events)

    def _pin(self, backend, path, rank, reason):
        try:
            backend.set_pin(path, rank)
        except Exception as exc:
            self._record("pin_failed", path, rank, str(exc))
        else:
            self._record("pin", path, rank, reason)

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
    return PrepinColocatedHotsetPolicy(config)
