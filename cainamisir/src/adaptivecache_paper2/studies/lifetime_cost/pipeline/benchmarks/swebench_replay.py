"""SWE-bench replay adapter.

This is the *cheapest* benchmark — it doesn't run any agent. It replays
existing trajectory logs from results/ through the cliff/cost analysis.
Useful as a sanity check and for the cliff plot in the paper.

For actually *running* SWE-bench with our policies, use the existing
modal_app/run_experiments.py harness with our policies plugged in (a
small wrapper, not implemented in this study folder).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .base import Benchmark, Task, ToolEnv


class SWEBenchReplay(Benchmark):
    """Wraps results/*.json trajectories as Tasks for offline replay."""

    name = "swebench_replay"

    def __init__(self, results_dir: str = "results"):
        self.results_dir = Path(results_dir)

    def tasks(self) -> Iterable[Task]:
        # Replay-only: emit empty tasks pointing at trajectory files.
        # The runner detects this and switches to replay mode.
        for path in sorted(self.results_dir.rglob("*.json")):
            yield Task(
                id=path.stem,
                messages_init=[],
                tool_env=ToolEnv([]),
                metadata={"replay_path": str(path), "mode": "replay"},
                max_steps=0,
            )
