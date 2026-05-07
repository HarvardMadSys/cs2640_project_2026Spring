"""Static baseline that pins only top-level benchmark subtrees.

This is a conservative variant of explicit static subtree pinning. Pinning every
directory caused heavy fragmentation in the first CloudLab run, so this policy
pins only the root's direct children by default.
"""

from pathlib import PurePosixPath

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyEvent


class StaticTopPolicy:
    name = "static_top"

    def __init__(self, config):
        self.pin_ranks = config.pin_ranks
        self.max_depth = int(config.options.get("max_depth", "1"))
        self.include_root = config.options.get("include_root", "false").lower() == "true"
        self._events = []

    def on_dirs_created(self, backend, dirs):
        if not self.pin_ranks or not dirs:
            return
        root = dirs[0].rstrip("/")
        root_depth = depth(root)
        pin_index = 0
        for directory in dirs:
            rel_depth = depth(directory.rstrip("/")) - root_depth
            if rel_depth < 0:
                continue
            if rel_depth == 0 and not self.include_root:
                continue
            if rel_depth > self.max_depth:
                continue
            rank = self.pin_ranks[pin_index % len(self.pin_ranks)]
            pin_index += 1
            try:
                backend.set_pin(directory, rank)
            except Exception as exc:
                self._record("pin_failed", directory, rank, str(exc))
            else:
                self._record("pin", directory, rank, f"top_level_static rel_depth={rel_depth}")

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


def depth(path):
    return len(PurePosixPath(path).parts)


def create_policy(config):
    return StaticTopPolicy(config)
