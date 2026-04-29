"""Memento policy — tags tool messages with memento text.

This policy doesn't compact at the message level. It tags each
sufficiently-large tool message with a `memento` field; the
`MementoVLLMModel` adapter renders those into block + summary tokens, and
the vLLM engine masks the obs from KV during prefill.

In other words: the *effect* of compaction is delegated to the inference
engine. From the message-stream point of view, nothing is removed —
which keeps prefix-cache hits intact for the unmasked portion.

v0 strategy: eager. Generate a memento for every tool message whose obs
exceeds `min_obs_chars`, the first time we see it. Subsequent steps that
include the same tool message see the memento already attached.

The policy is the natural place to record memento-generation cost as a
CompactionEvent so analysis sees it the same as any other compaction.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

# Pipeline imports (relative — works in both worktree and editable installs).
from ...pipeline.policies.base import CompactionContext, CompactionPolicy
from ...pipeline.types import CompactionEvent

from ..memento_writer import HaikuMementoWriter


def _tool_obs(msg: Dict[str, Any]) -> Optional[str]:
    """Return the obs text if the message is a tool message, else None."""
    if msg.get("role") != "tool":
        return None
    c = msg.get("content")
    return c if isinstance(c, str) else None


class MementoPolicy(CompactionPolicy):
    """Tag tool messages with mementos; the model adapter handles masking."""

    name = "memento"

    def __init__(
        self,
        *,
        min_obs_chars: int = 500,
        writer: Optional[HaikuMementoWriter] = None,
        memento_model: str = "claude-haiku-4-5",
        max_obs_chars: int = 8000,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._min_obs_chars = min_obs_chars
        self._writer = writer or HaikuMementoWriter(
            model=memento_model, max_obs_chars=max_obs_chars
        )

    def maybe_compact(
        self,
        messages: List[Dict[str, Any]],
        ctx: CompactionContext,
    ) -> Tuple[List[Dict[str, Any]], Optional[CompactionEvent]]:
        # Find tool messages that are big enough and not yet memento'd.
        targets: List[int] = []
        for i, m in enumerate(messages):
            obs = _tool_obs(m)
            if obs is None or len(obs) < self._min_obs_chars:
                continue
            if m.get("memento"):
                continue
            targets.append(i)

        if not targets:
            return messages, None

        t0 = time.perf_counter()
        in_toks_total = 0
        out_toks_total = 0
        cost_total = 0.0
        bytes_tagged = 0

        # Walk back through the assistant messages to find the tool_call name
        # that produced each tool obs (best-effort; not strictly required).
        for i in targets:
            msg = messages[i]
            tool_name, tool_args = _trace_tool_call(messages, i)
            text, usage = self._writer.write(
                obs=msg["content"],
                tool_name=tool_name,
                tool_args=tool_args,
            )
            msg["memento"] = text
            in_toks_total += usage.input_tokens
            out_toks_total += usage.output_tokens
            cost_total += usage.cost_usd
            bytes_tagged += len(msg["content"])

        wall_ms = int((time.perf_counter() - t0) * 1000)
        evt = CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(messages),  # messages unchanged at the list level
            tokens_before=0,           # filled by analysis if needed
            tokens_after=0,
            compaction_input_cached_tokens=0,
            compaction_input_uncached_tokens=in_toks_total,
            compaction_output_tokens=out_toks_total,
            compaction_call_tokens=in_toks_total + out_toks_total,
            wallclock_ms=wall_ms,
        )
        return messages, evt


def _trace_tool_call(
    messages: List[Dict[str, Any]], tool_msg_idx: int
) -> Tuple[str, Dict[str, Any]]:
    """Look back for the matching tool_call by tool_call_id."""
    target_id = messages[tool_msg_idx].get("tool_call_id")
    for j in range(tool_msg_idx - 1, -1, -1):
        m = messages[j]
        if m.get("role") != "assistant":
            continue
        tcs = m.get("tool_calls") or []
        for tc in tcs:
            if tc.get("id") == target_id:
                fn = tc.get("function") or {}
                return fn.get("name", "unknown"), fn.get("arguments") or {}
    return "unknown", {}
