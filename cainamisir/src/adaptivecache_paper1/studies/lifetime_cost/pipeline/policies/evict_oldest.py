"""Pure-eviction compaction: drop oldest big tool observations, no LLM.

This is the structural Tier-1 policy from system_design.md §6 — hole-leaving
eviction simulated by replacing message content with a small placeholder
while preserving the message envelope (role + tool_call_id) so the chat
template doesn't choke on dangling tool refs.

When the budget bites:
  - Walk tool messages oldest-first
  - Evict any whose content is over `min_obs_tokens`, skipping the most
    recent `protect_recent` of them
  - Replace content with `[evicted: N tokens, evict_oldest policy]`
  - Stop once content tokens are under `target_ratio * budget` OR no more
    candidates remain

No LLM call. No summary. Cliff cost still applies in the byte-prefix sim
(everything after the first eviction position changes), but the SAVED
suffix bytes are pure win when the agent doesn't re-reference the evicted
content. On benchmarks where tool obs are mostly write-once-consumed (file
reads, search results, run_tests output) this should be the cheapest
non-trivial policy.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


class EvictOldest(CompactionPolicy):
    name = "evict_oldest"

    def __init__(
        self,
        min_obs_tokens: int = 400,
        protect_recent: int = 4,
        trigger_ratio: float = 0.85,
        target_ratio: float = 0.50,
    ):
        super().__init__(
            min_obs_tokens=min_obs_tokens,
            protect_recent=protect_recent,
            trigger_ratio=trigger_ratio,
            target_ratio=target_ratio,
        )
        self.min_obs_tokens = min_obs_tokens
        self.protect_recent = protect_recent
        self.trigger_ratio = trigger_ratio
        self.target_ratio = target_ratio

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * ctx.budget:
            return messages, None

        # Find candidate indices: tool messages, oldest first, excluding the
        # most recent protect_recent tool messages.
        tool_idxs: List[int] = [
            i for i, m in enumerate(messages) if m.get("role") == "tool"
        ]
        if len(tool_idxs) <= self.protect_recent:
            return messages, None
        evictable = tool_idxs[: -self.protect_recent]

        target = self.target_ratio * ctx.budget
        new_messages = list(messages)
        evicted = 0
        evicted_tokens = 0
        for i in evictable:
            m = new_messages[i]
            content = m.get("content") or ""
            if not isinstance(content, str):
                continue
            tok = ctx.tokenizer.count(content)
            if tok < self.min_obs_tokens:
                continue
            # Replace with a small placeholder; keep tool_call_id intact so
            # the assistant's prior tool_call still has its referent.
            holed = dict(m)
            holed["content"] = f"[evicted: ~{tok} tokens, evict_oldest policy]"
            new_messages[i] = holed
            evicted += 1
            evicted_tokens += tok
            n_now = self._token_count(new_messages, ctx.tokenizer)
            if n_now <= target:
                break

        if evicted == 0:
            return messages, None

        n_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),  # same: we hole, not delete
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=0,
            compaction_input_uncached_tokens=0,
            compaction_output_tokens=0,
        )
