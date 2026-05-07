"""Prefix-preserving compaction.

Layout: [system + first K turns]  [summary of middle]  [recent M turns]
                ^ kept byte-identical                   ^ kept verbatim

The first K turns are *frozen*: they never change after they're decided
to be kept. Subsequent compactions only summarize the growing middle
window, leaving K-prefix and recent tail intact. This means the
cache-hit ceiling stays at |sys + first K turns| for *all* subsequent
steps until K is itself rewritten.

Tradeoff: K determines the per-step cache hit floor. Larger K = more
cached, but less compression headroom. The ablation sweeps K.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


class PrefixPreserving(CompactionPolicy):
    name = "prefix_preserving"

    def __init__(
        self,
        keep_first_turns: int = 6,
        keep_recent_turns: int = 4,
        trigger_ratio: float = 0.85,
        target_ratio: float = 0.50,
        cooldown_steps: int = 0,
    ):
        super().__init__(
            keep_first_turns=keep_first_turns,
            keep_recent_turns=keep_recent_turns,
            trigger_ratio=trigger_ratio,
            target_ratio=target_ratio,
            cooldown_steps=cooldown_steps,
        )
        self.keep_first_turns = keep_first_turns
        self.keep_recent_turns = keep_recent_turns
        self.trigger_ratio = trigger_ratio
        self.target_ratio = target_ratio
        # Cooldown: after a compaction at step S, next fire allowed at S+cooldown_steps.
        # Prevents the rapid-fire pattern where summary lands just under the trigger
        # and the next 1-2 turns push it back over.
        self.cooldown_steps = cooldown_steps
        self._last_compaction_step: int = -10**9
        # Frozen prefix: once we commit to keep_first_turns turns at compaction
        # time, we never rewrite them. The runner enforces this via _frozen_idx.
        self._frozen_idx: int = 0

    def _turn_boundaries(self, messages: List[dict]) -> List[int]:
        """Return indices where a new 'turn' starts. A turn = one assistant
        action + its observation(s). Boundary is the start of an assistant
        message after a non-assistant. Always includes 0 and len(messages)."""
        boundaries = [0]
        prev_role = None
        for i, m in enumerate(messages):
            role = m.get("role")
            if role == "assistant" and prev_role not in (None, "assistant"):
                boundaries.append(i)
            prev_role = role
        boundaries.append(len(messages))
        return boundaries

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * ctx.budget:
            return messages, None
        if ctx.summarizer is None:
            return messages, None
        if ctx.step - self._last_compaction_step < self.cooldown_steps:
            return messages, None

        boundaries = self._turn_boundaries(messages)
        # boundaries[k] = start index of turn k (0-indexed, including pre-task system)
        if len(boundaries) - 1 < self.keep_first_turns + self.keep_recent_turns + 1:
            # Not enough turns to compact while preserving K head + M tail
            return messages, None

        head_end = boundaries[self.keep_first_turns]
        # Update frozen prefix the first time we compact; on subsequent
        # compactions we re-anchor at the same head_end (it's stable).
        if self._frozen_idx == 0:
            self._frozen_idx = head_end
        else:
            head_end = self._frozen_idx  # never expand the frozen window

        tail_start = boundaries[-1 - self.keep_recent_turns]

        if tail_start <= head_end:
            return messages, None

        head = messages[:head_end]
        middle = messages[head_end:tail_start]
        tail = messages[tail_start:]

        t0 = time.perf_counter()
        summary, in_cached, in_uncached, out_toks = ctx.summarizer(middle)
        ms = int((time.perf_counter() - t0) * 1000)

        summary_msg = {
            "role": "user",
            "content": (
                f"[Summary of intermediate steps {self.keep_first_turns}"
                f" through {len(boundaries) - 1 - self.keep_recent_turns}]"
                f"\n{summary}"
            ),
        }
        new_messages = head + [summary_msg] + tail
        n_after = self._token_count(new_messages, ctx.tokenizer)
        self._last_compaction_step = ctx.step

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
