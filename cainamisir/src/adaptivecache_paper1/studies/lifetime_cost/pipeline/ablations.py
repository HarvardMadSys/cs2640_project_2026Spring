"""Hyperparameter ablations.

Sweeps:
  - keep_first_turns K  (prefix_preserving, boundary_aware)
  - keep_recent_turns M (same)
  - trigger_ratio       (when does compaction fire as a fraction of budget)
  - budget_tokens       (the budget itself)
  - per_msg_threshold   (microcompact)

Each sweep runs the matrix harness with one parameter varied. Results are
written into a sub-directory named for the swept parameter so analysis
can pick them up cleanly.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

from .harness import run_matrix


def sweep(
    base_config: Dict[str, Any],
    *,
    target_policy: str,
    param: str,
    values: List[Any],
    out_subdir: str = "ablations",
) -> List[Path]:
    """Run base_config N times, varying one param of target_policy.

    Each run writes to: out_dir/<out_subdir>/<param>=<value>/...
    """
    base_out = Path(base_config.get("out_dir", "studies/lifetime_cost/out"))
    written: List[Path] = []

    for v in values:
        cfg = copy.deepcopy(base_config)
        cfg["out_dir"] = str(base_out / out_subdir / f"{param}={v}")
        for pol in cfg["policies"]:
            if pol["name"] == target_policy:
                pol.setdefault("kwargs", {})[param] = v
        # Allow sweeping budget itself
        if param == "budget_tokens":
            cfg["budget_tokens"] = v
        if param == "hard_budget_tokens":
            cfg["hard_budget_tokens"] = v
        written.extend(run_matrix(cfg))
    return written


def planned_sweeps() -> List[Dict[str, Any]]:
    """The set of sweeps we report in the paper. Used by scripts/run_ablations.py."""
    return [
        {"target_policy": "prefix_preserving", "param": "keep_first_turns",
         "values": [2, 4, 6, 8, 12]},
        {"target_policy": "prefix_preserving", "param": "keep_recent_turns",
         "values": [2, 4, 6, 8]},
        {"target_policy": "prefix_preserving", "param": "trigger_ratio",
         "values": [0.50, 0.70, 0.85, 0.95]},
        {"target_policy": "microcompact", "param": "per_msg_threshold_tokens",
         "values": [200, 500, 800, 2000]},
        {"target_policy": "boundary_aware", "param": "boundary_grace_steps",
         "values": [0, 1, 2, 4]},
        # budget sweep applies to all policies
        {"target_policy": "prefix_preserving", "param": "budget_tokens",
         "values": [8_000, 16_000, 32_000, 64_000]},
    ]
