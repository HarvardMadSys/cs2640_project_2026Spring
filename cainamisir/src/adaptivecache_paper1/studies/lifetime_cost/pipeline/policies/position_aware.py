"""Position-aware structural eviction policy.

Pure structural heuristic, no keyword regex, no LLM call. Implements the
empirical finding from the Hermes attention measurement (validated across
Qwen3 0.6B-8B): the first 10% of any agent trajectory carries 500-1500x
more attention per token than the middle. So:

  1. Always pin: system message + first K user/assistant turns
  2. Always pin: most recent M turns (recency)
  3. Evict from the middle, *biased toward big tool obs that fit the
     "ignored" profile* (large size + no recent reference)

This is the structural baseline you compare richer policies against. No
LLM, no embeddings, no learned scoring.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


class PositionAware(CompactionPolicy):
    name = "position_aware"

    def __init__(
        self,
        keep_first_turns: int = 4,
        keep_recent_turns: int = 4,
        trigger_ratio: float = 0.85,
        big_tool_obs_threshold_tokens: int = 1500,
    ):
        super().__init__(
            keep_first_turns=keep_first_turns,
            keep_recent_turns=keep_recent_turns,
            trigger_ratio=trigger_ratio,
            big_tool_obs_threshold_tokens=big_tool_obs_threshold_tokens,
        )
        self.keep_first_turns = keep_first_turns
        self.keep_recent_turns = keep_recent_turns
        self.trigger_ratio = trigger_ratio
        self.big_threshold = big_tool_obs_threshold_tokens

    def _turn_starts(self, messages: List[dict]) -> List[int]:
        """Indices where a new agent turn starts. A turn begins at each
        assistant message that follows a non-assistant message."""
        starts = [0]
        prev_role = None
        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and prev_role not in (None, "assistant"):
                starts.append(i)
            prev_role = m.get("role")
        starts.append(len(messages))
        return starts

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * ctx.budget:
            return messages, None

        boundaries = self._turn_starts(messages)
        if len(boundaries) - 1 < self.keep_first_turns + self.keep_recent_turns + 1:
            return messages, None

        head_end = boundaries[self.keep_first_turns]
        tail_start = boundaries[-1 - self.keep_recent_turns]
        if tail_start <= head_end:
            return messages, None

        head = messages[:head_end]
        middle = messages[head_end:tail_start]
        tail = messages[tail_start:]

        # Hole big tool obs in the middle (replace content with a small
        # placeholder, keep the message envelope so tool_call_id references
        # in the prior assistant turns still resolve under the chat template).
        # Keep small tool obs and all assistant/user messages.
        kept_middle = []
        dropped = 0
        dropped_tokens = 0
        for m in middle:
            tok = int(m.get("_token_count") or ctx.tokenizer.count(m.get("content") or ""))
            if m.get("role") == "tool" and tok >= self.big_threshold:
                dropped += 1
                dropped_tokens += tok
                holed = dict(m)
                holed["content"] = f"[evicted: ~{tok} tokens, position_aware policy]"
                kept_middle.append(holed)
                continue
            kept_middle.append(m)

        if dropped == 0:
            return messages, None

        new_messages = head + kept_middle + tail
        n_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=0,    # no LLM call
            compaction_input_uncached_tokens=0,
            compaction_output_tokens=0,
        )
