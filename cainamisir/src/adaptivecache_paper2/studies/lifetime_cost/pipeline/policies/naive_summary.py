"""Naive single-shot summarization: when over budget, replace
[turn_1 ... turn_{N-recent}] with one summary message.

This is the strawman that everyone implements. It maximally destroys the
prefix because every byte after [system] changes when the summary is
inserted.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


class NaiveSummary(CompactionPolicy):
    name = "naive_summary"

    def __init__(self, recent_keep: int = 4, trigger_ratio: float = 0.9):
        super().__init__(recent_keep=recent_keep, trigger_ratio=trigger_ratio)
        self.recent_keep = recent_keep
        self.trigger_ratio = trigger_ratio

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * ctx.budget:
            return messages, None
        if ctx.summarizer is None:
            return messages, None

        # Find the system + first user (task) — keep those
        head_end = 0
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                head_end = i + 1
            else:
                break
        # Identify the first user message (task) — keep it too
        for i in range(head_end, len(messages)):
            if messages[i].get("role") == "user":
                head_end = i + 1
                break

        if len(messages) - head_end <= self.recent_keep:
            return messages, None

        head = messages[:head_end]
        middle = messages[head_end : len(messages) - self.recent_keep]
        tail = messages[len(messages) - self.recent_keep :]

        t0 = time.perf_counter()
        summary, in_cached, in_uncached, out_toks = ctx.summarizer(middle)
        ms = int((time.perf_counter() - t0) * 1000)

        summary_msg = {"role": "user", "content": f"[Summary of earlier steps]\n{summary}"}
        new_messages = head + [summary_msg] + tail

        n_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=in_cached,
            compaction_input_uncached_tokens=in_uncached,
            compaction_output_tokens=out_toks,
            wallclock_ms=ms,
        )
