"""Per-instance JSONL trace logging for experiments.

Logs step-by-step context state, cache hits, block scores,
and zone assignments for offline analysis.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


class TraceLogger:
    """Append-only JSONL logger for experiment traces."""

    def __init__(self, output_dir: str, instance_id: str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / f"{instance_id}.jsonl"
        self._file = open(self.path, "a")

    def log_step(
        self,
        instance_id: str,
        step: int,
        cache_stats: dict,
        block_states: list[dict] | None = None,
        extra: dict | None = None,
    ) -> None:
        """Log a single step's state."""
        record = {
            "instance_id": instance_id,
            "step": step,
            **cache_stats,
        }
        if block_states is not None:
            record["blocks"] = block_states
        if extra is not None:
            record.update(extra)

        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def log_summary(self, trace) -> None:
        """Log an AgentTrace summary."""
        record = {
            "type": "summary",
            "task": trace.task,
            "num_steps": len(trace.steps),
            "success": trace.success,
            "total_input_tokens": trace.total_input_tokens,
            "total_output_tokens": trace.total_output_tokens,
        }
        if trace.steps:
            record["final_context_tokens"] = trace.steps[-1].context_tokens
            record["final_pinned_tokens"] = trace.steps[-1].pinned_tokens
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
