"""Cliff detection on existing trajectories.

Given a directory of saved trajectory JSON files, for each trajectory:
  1. Walk the per-step messages.
  2. Detect compaction events (step k where len(messages_k) < len(messages_{k-1})
     OR a message at index < len(messages_{k-1}) was rewritten).
  3. For each step, compute the prefix-cache-hit ceiling vs the prior step
     using prefix_match.cache_hit_ceiling.
  4. Compute cliff_k around each compaction = uncached(k+1) / uncached(k-1).

Output: one JSON record per trajectory with the per-step series + cliff
events. Plotting lives in analysis.py.

Trajectory file formats supported:
  - Native: {"steps": [{"messages_in": [...], "usage": {...}}, ...]}
  - mini-swe-agent style: list of {"role", "content"} messages with no
    explicit per-step boundary; we derive steps as runs of
    user/tool → assistant.
  - SWE-bench JSON from results/v2_experiment_*.json: {"policy_results":
    [{"policy", "trajectory": {...}}]}.

Add a new format by writing a function that yields (task_id, model, policy,
list_of_per_step_message_lists, optional_per_step_usage).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

from .prefix_match import cache_hit_ceiling, canonical_render, hash_prefix_blocks


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_native(path: Path) -> Iterator[dict]:
    """Our own Trajectory.to_dict() format."""
    data = json.loads(path.read_text())
    if "steps" not in data:
        return
    yield {
        "task_id": data.get("task_id", path.stem),
        "model": data.get("model", "unknown"),
        "policy": data.get("policy", "unknown"),
        "step_messages": [s["messages_in"] for s in data["steps"]],
        "step_usage": [s.get("usage") for s in data["steps"]],
        "source_file": str(path),
    }


def _load_v2_experiment(path: Path) -> Iterator[dict]:
    """results/v2_experiment_*.json format. One file = many policies."""
    data = json.loads(path.read_text())
    if "policy_results" not in data:
        return
    for entry in data["policy_results"]:
        traj = entry.get("trajectory") or {}
        steps = traj.get("steps") or traj.get("messages_per_step")
        if not steps:
            continue
        yield {
            "task_id": entry.get("task_id", path.stem),
            "model": data.get("model", "unknown"),
            "policy": entry.get("policy", "unknown"),
            "step_messages": steps if isinstance(steps[0], list) else [steps],
            "step_usage": entry.get("step_usage"),
            "source_file": str(path),
        }


def _load_swebench_traj(path: Path) -> Iterator[dict]:
    """mini-swe-agent style trajectory: a flat list of messages with assistant
    interleaved. We reconstruct per-step prompts as 'all messages up to and
    including the i-th assistant message'."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    msgs = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(msgs, list):
        return

    # Find assistant message indices — those mark step boundaries
    asst_idx = [i for i, m in enumerate(msgs) if isinstance(m, dict) and m.get("role") == "assistant"]
    if not asst_idx:
        return

    step_messages = []
    for i, end in enumerate(asst_idx):
        step_messages.append(msgs[: end])  # what was sent at this step
    yield {
        "task_id": data.get("instance_id", path.stem) if isinstance(data, dict) else path.stem,
        "model": (data.get("model") if isinstance(data, dict) else None) or "unknown",
        "policy": (data.get("policy") if isinstance(data, dict) else None) or "unknown",
        "step_messages": step_messages,
        "step_usage": None,
        "source_file": str(path),
    }


LOADERS = [_load_native, _load_v2_experiment, _load_swebench_traj]


def load_trajectories(results_dir: Path) -> Iterator[dict]:
    """Walk a directory and yield trajectory dicts from any recognized format."""
    for path in sorted(results_dir.rglob("*.json")):
        for loader in LOADERS:
            try:
                for traj in loader(path):
                    yield traj
            except Exception as e:
                # Loader didn't apply; try the next one
                continue


# ---------------------------------------------------------------------------
# Compaction detection
# ---------------------------------------------------------------------------

def _msg_fingerprint(m: dict) -> str:
    """Stable identity of a message for change detection."""
    return canonical_render([m])[:200]


def detect_compactions(step_messages: List[List[dict]]) -> List[int]:
    """Step indices where compaction *appears* to have fired.

    Heuristic: at step k, either (a) fewer messages than step k-1, or (b)
    a message at index i < len(prev) has different content than at step k-1.
    """
    events = []
    for k in range(1, len(step_messages)):
        prev = step_messages[k - 1]
        curr = step_messages[k]
        if len(curr) < len(prev):
            events.append(k)
            continue
        # Same length or longer → check if any prior position was rewritten
        for i in range(min(len(prev), len(curr))):
            if _msg_fingerprint(prev[i]) != _msg_fingerprint(curr[i]):
                events.append(k)
                break
    return events


# ---------------------------------------------------------------------------
# Per-trajectory cliff series
# ---------------------------------------------------------------------------

@dataclass
class StepCliffPoint:
    step: int
    n_messages: int
    curr_blocks: int
    shared_blocks: int
    hit_ratio_blocks: float
    uncached_block_estimate: int           # curr - shared


@dataclass
class CliffEvent:
    step: int                               # the step *at which* compaction was detected
    cliff_ratio_blocks: float               # uncached(k) / max(uncached(k-1), 1)
    uncached_before: int
    uncached_after: int


@dataclass
class TrajectoryCliffReport:
    task_id: str
    model: str
    policy: str
    source_file: str
    n_steps: int
    series: List[StepCliffPoint]
    compactions: List[CliffEvent]


def analyze_trajectory(traj: dict, *, block_chars: int = 1024) -> TrajectoryCliffReport:
    step_msgs = traj["step_messages"]
    series = []
    prev = None
    for k, msgs in enumerate(step_msgs):
        if prev is None:
            rendered = canonical_render(msgs)
            blocks = hash_prefix_blocks(rendered, block_chars)
            series.append(StepCliffPoint(
                step=k,
                n_messages=len(msgs),
                curr_blocks=len(blocks),
                shared_blocks=0,
                hit_ratio_blocks=0.0,
                uncached_block_estimate=len(blocks),
            ))
        else:
            r = cache_hit_ceiling(prev, msgs, block_chars=block_chars)
            series.append(StepCliffPoint(
                step=k,
                n_messages=len(msgs),
                curr_blocks=r["curr_blocks"],
                shared_blocks=r["shared_blocks"],
                hit_ratio_blocks=r["hit_ratio_blocks"],
                uncached_block_estimate=r["curr_blocks"] - r["shared_blocks"],
            ))
        prev = msgs

    # Compaction events → cliff ratios
    compactions = []
    comp_steps = set(detect_compactions(step_msgs))
    for k in sorted(comp_steps):
        before = series[k - 1].uncached_block_estimate if k > 0 else 1
        after = series[k].uncached_block_estimate
        compactions.append(CliffEvent(
            step=k,
            cliff_ratio_blocks=after / max(before, 1),
            uncached_before=before,
            uncached_after=after,
        ))

    return TrajectoryCliffReport(
        task_id=traj["task_id"],
        model=traj["model"],
        policy=traj["policy"],
        source_file=traj["source_file"],
        n_steps=len(step_msgs),
        series=series,
        compactions=compactions,
    )


def report_to_dict(r: TrajectoryCliffReport) -> dict:
    d = asdict(r)
    return d


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def aggregate_cliffs(reports: Iterable[TrajectoryCliffReport]) -> dict:
    """Summary statistics across many trajectories."""
    import statistics

    all_cliffs = []
    by_policy = {}
    n_trajs = 0
    n_with_compaction = 0

    for r in reports:
        n_trajs += 1
        if r.compactions:
            n_with_compaction += 1
        for c in r.compactions:
            all_cliffs.append(c.cliff_ratio_blocks)
            by_policy.setdefault(r.policy, []).append(c.cliff_ratio_blocks)

    if not all_cliffs:
        return {
            "n_trajectories": n_trajs,
            "n_with_compaction": 0,
            "n_cliff_events": 0,
            "median_cliff": None,
            "p90_cliff": None,
            "by_policy": {},
        }

    return {
        "n_trajectories": n_trajs,
        "n_with_compaction": n_with_compaction,
        "n_cliff_events": len(all_cliffs),
        "median_cliff": statistics.median(all_cliffs),
        "mean_cliff": statistics.mean(all_cliffs),
        "p90_cliff": sorted(all_cliffs)[int(0.9 * (len(all_cliffs) - 1))],
        "max_cliff": max(all_cliffs),
        "by_policy": {
            p: {
                "n_events": len(cs),
                "median_cliff": statistics.median(cs),
                "p90_cliff": sorted(cs)[int(0.9 * (len(cs) - 1))],
            }
            for p, cs in by_policy.items()
        },
    }
