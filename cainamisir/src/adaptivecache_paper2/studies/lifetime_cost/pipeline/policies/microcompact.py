"""Microcompact: per-tool-result summarization in place.

Approximates Claude Code's microcompact and OpenHands' per-observation
condensation: each large tool/observation message is independently
shrunk to a 1-2 line summary. Other messages are untouched.

Cliff impact: smaller than naive_summary because untouched messages
between compactions stay byte-identical, but still significant — the first
rewritten observation invalidates the prefix from that position onward.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


class Microcompact(CompactionPolicy):
    name = "microcompact"

    def __init__(
        self,
        per_msg_threshold_tokens: int = 800,
        trigger_ratio: float = 0.85,
        protect_recent: int = 4,
    ):
        super().__init__(
            per_msg_threshold_tokens=per_msg_threshold_tokens,
            trigger_ratio=trigger_ratio,
            protect_recent=protect_recent,
        )
        self.per_msg_threshold_tokens = per_msg_threshold_tokens
        self.trigger_ratio = trigger_ratio
        self.protect_recent = protect_recent
        self._compacted_already: set = set()  # message-id-by-position-hash

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * ctx.budget:
            return messages, None
        if ctx.summarizer is None:
            return messages, None

        # Find oversized observation/tool messages (excluding the protected tail)
        cutoff = max(0, len(messages) - self.protect_recent)
        candidates = []
        for i in range(cutoff):
            m = messages[i]
            if m.get("role") not in ("tool", "user"):
                continue
            # Use _token_count if attached (identity-model simulation), else
            # actually tokenize the content.
            if "_token_count" in m and m["_token_count"] is not None:
                tok = int(m["_token_count"])
            else:
                content = m.get("content") or ""
                if not isinstance(content, str):
                    continue
                tok = ctx.tokenizer.count(content)
            if tok >= self.per_msg_threshold_tokens:
                candidates.append(i)

        if not candidates:
            return messages, None

        new_messages = list(messages)
        total_in_cached = 0
        total_in_uncached = 0
        total_out = 0
        t0 = time.perf_counter()
        for i in candidates:
            summary, ic, iu, ot = ctx.summarizer([new_messages[i]])
            total_in_cached += ic
            total_in_uncached += iu
            total_out += ot
            new_messages[i] = dict(new_messages[i])
            new_messages[i]["content"] = f"[microcompacted] {summary}"
        ms = int((time.perf_counter() - t0) * 1000)

        n_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=total_in_cached,
            compaction_input_uncached_tokens=total_in_uncached,
            compaction_output_tokens=total_out,
            wallclock_ms=ms,
        )
