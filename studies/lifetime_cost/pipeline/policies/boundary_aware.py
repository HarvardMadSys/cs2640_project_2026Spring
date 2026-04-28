"""Boundary-aware compaction.

Defers compaction until a detected sub-task boundary, then runs
prefix-preserving compaction. The intuition: compacting mid-task discards
context the very next step is about to need, costing both quality and
cache. Compacting at a natural boundary (task switch, planning message,
tool returning to a file we haven't touched recently) costs less because
the upcoming turns won't reuse the discarded context anyway.

Boundary signals (cheap, model-agnostic):
  - Assistant emits a 'plan' / 'next' / 'now I will' marker (regex)
  - Tool sequence transitions: e.g., a different file is now under edit
  - A decisive verb in the assistant message ('finally', 'in summary')

These are heuristics. The ablation toggles them on/off.
"""

from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy
from .prefix_preserving import PrefixPreserving


_PLAN_MARKERS = re.compile(
    r"\b(now i will|next, i will|let me|i'll start|moving on|in summary|finally|step \d+:)\b",
    re.IGNORECASE,
)


class BoundaryAware(CompactionPolicy):
    """Wraps PrefixPreserving with a boundary-detection trigger.

    Compaction is allowed only when EITHER:
      (a) a boundary signal was just emitted, OR
      (b) the hard budget is hit (safety fallback)
    """

    name = "boundary_aware"

    def __init__(
        self,
        keep_first_turns: int = 6,
        keep_recent_turns: int = 4,
        trigger_ratio: float = 0.85,
        boundary_grace_steps: int = 1,
    ):
        super().__init__(
            keep_first_turns=keep_first_turns,
            keep_recent_turns=keep_recent_turns,
            trigger_ratio=trigger_ratio,
            boundary_grace_steps=boundary_grace_steps,
        )
        self.boundary_grace_steps = boundary_grace_steps
        self._inner = PrefixPreserving(
            keep_first_turns=keep_first_turns,
            keep_recent_turns=keep_recent_turns,
            trigger_ratio=trigger_ratio,
        )
        self._steps_since_boundary: int = 1_000_000  # large = boundary not seen yet
        self._last_compaction_step: int = -1

    def _is_boundary(self, messages: List[dict]) -> bool:
        # Look at the most recent assistant message
        for m in reversed(messages):
            if m.get("role") == "assistant":
                content = m.get("content") or ""
                if isinstance(content, str) and _PLAN_MARKERS.search(content):
                    return True
                return False
        return False

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        if self._is_boundary(messages):
            self._steps_since_boundary = 0
        else:
            self._steps_since_boundary += 1

        n_tok = self._token_count(messages, ctx.tokenizer)
        soft_full = n_tok >= self._inner.trigger_ratio * ctx.budget
        hard_full = n_tok >= ctx.hard_budget

        boundary_window = self._steps_since_boundary <= self.boundary_grace_steps

        if not (hard_full or (soft_full and boundary_window)):
            return messages, None

        return self._inner.maybe_compact(messages, ctx)
