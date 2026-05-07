"""LLM-scored periodic compaction.

Decouples *scoring* from *eviction*:

  - Every `rescore_interval_tokens` of context growth, run ONE LLM call that
    scores all tool observations 1-10 for current task relevance. Cache the
    scores by message position.
  - When budget bites, eviction reads cached scores. Same Tier-1 gate as
    smart_evict: only drop msgs whose cached score ≤ max_drop_score.
    Emergency override at emergency_ratio × hard_budget.

Why this beats `llm_reorganizer`:

  - Scoring is task-anchored: prompt includes the problem statement and
    the LLM sees ALL obs at once, so scores are mutually-relative.
  - Re-scoring catches stale relevance: an obs that mattered at step 5
    can be marked safe-to-drop at step 25 once the agent has moved on.
  - Scoring frequency decouples from compaction frequency. Many compaction
    events can share one set of scores; one set of scores can outlive many
    new tool obs.

The compaction-event cost field still tracks the LLM call cost — to look at
the "scoring is free" view, run analyze with --exclude_compaction_costs.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


_PREVIEW_CHARS = 240
_SCORE_FALLBACK = 5
_SCORE_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


class ScorePeriodic(CompactionPolicy):
    name = "score_periodic"

    def __init__(
        self,
        rescore_interval_tokens: int = 10000,
        trigger_ratio: float = 0.85,
        target_ratio: float = 0.55,
        protect_recent: int = 3,
        min_obs_tokens: int = 300,
        use_hard_budget_trigger: bool = False,
        max_drop_score: int = 3,
        emergency_ratio: float = 0.95,
        max_score_input_msgs: int = 60,
    ):
        super().__init__(
            rescore_interval_tokens=rescore_interval_tokens,
            trigger_ratio=trigger_ratio,
            target_ratio=target_ratio,
            protect_recent=protect_recent,
            min_obs_tokens=min_obs_tokens,
            use_hard_budget_trigger=use_hard_budget_trigger,
            max_drop_score=max_drop_score,
            emergency_ratio=emergency_ratio,
            max_score_input_msgs=max_score_input_msgs,
        )
        self.rescore_interval_tokens = rescore_interval_tokens
        self.trigger_ratio = trigger_ratio
        self.target_ratio = target_ratio
        self.protect_recent = protect_recent
        self.min_obs_tokens = min_obs_tokens
        self.use_hard_budget_trigger = use_hard_budget_trigger
        self.max_drop_score = max_drop_score
        self.emergency_ratio = emergency_ratio
        self.max_score_input_msgs = max_score_input_msgs
        # Position-keyed score cache. Position is stable because we only ever
        # hole messages (replace content), never delete or reorder.
        self._score_by_pos: Dict[int, int] = {}
        self._last_rescore_at_tokens: int = 0
        # Track the cumulative cost of scoring across all rescore rounds, so
        # we can roll it into the single CompactionEvent we emit when
        # eviction actually happens.
        self._pending_cost = {"in_uncached": 0, "in_cached": 0, "out": 0}

    # ------------------------------------------------------------------
    # main hook
    # ------------------------------------------------------------------

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        n_tok = self._token_count(messages, ctx.tokenizer)
        budget_for_trigger = ctx.hard_budget if self.use_hard_budget_trigger else ctx.budget

        # 1. Periodic rescoring (independent of compaction trigger)
        if (n_tok - self._last_rescore_at_tokens >= self.rescore_interval_tokens
                and ctx.summarizer_model is not None):
            self._rescore(messages, ctx)
            self._last_rescore_at_tokens = n_tok

        # 2. Compaction trigger
        if n_tok < self.trigger_ratio * budget_for_trigger:
            return messages, None

        # 3. Find evictable tool messages outside the protect-recent window
        tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_idxs) <= self.protect_recent:
            return messages, None
        evictable_idxs = tool_idxs[: -self.protect_recent]

        # Filter to non-trivial sizes and skip already-evicted markers
        sizes: Dict[int, int] = {}
        candidates = []
        for i in evictable_idxs:
            content = messages[i].get("content") or ""
            if not isinstance(content, str):
                continue
            if content.startswith("[evicted") or content.startswith("[microcompacted"):
                continue
            tok = ctx.tokenizer.count(content)
            sizes[i] = tok
            if tok >= self.min_obs_tokens:
                candidates.append(i)
        if not candidates:
            return messages, None

        # If the cache doesn't cover some candidates (e.g. they came in after
        # the last rescore round), force a fresh rescore so the gate has data.
        uncached = [i for i in candidates if i not in self._score_by_pos]
        if uncached and ctx.summarizer_model is not None:
            self._rescore(messages, ctx)
            self._last_rescore_at_tokens = n_tok

        # 4. Apply the safe-to-drop gate using cached scores
        scores = {i: self._score_by_pos.get(i, _SCORE_FALLBACK) for i in candidates}
        safe = [i for i in candidates if scores[i] <= self.max_drop_score]
        is_emergency = n_tok >= self.emergency_ratio * ctx.hard_budget
        if not safe and not is_emergency:
            return messages, None
        eviction_set = safe if safe else candidates

        target_tokens = self.target_ratio * budget_for_trigger
        sorted_candidates = sorted(eviction_set, key=lambda i: scores[i])

        new_messages = list(messages)
        evicted_count = 0
        evicted_tokens = 0
        for i in sorted_candidates:
            tag = "emergency" if (is_emergency and not safe) else "safe"
            tok = sizes[i]
            holed = dict(new_messages[i])
            holed["content"] = (
                f"[evicted: ~{tok} tokens, score={scores[i]}, score_periodic/{tag}]"
            )
            new_messages[i] = holed
            evicted_count += 1
            evicted_tokens += tok
            n_now = self._token_count(new_messages, ctx.tokenizer)
            if n_now <= target_tokens:
                break

        if evicted_count == 0:
            return messages, None

        n_after = self._token_count(new_messages, ctx.tokenizer)
        # Roll the accumulated scoring cost into THIS compaction event,
        # then reset. Subsequent rescores will accumulate again until the
        # next compaction.
        evt = CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=self._pending_cost["in_cached"],
            compaction_input_uncached_tokens=self._pending_cost["in_uncached"],
            compaction_output_tokens=self._pending_cost["out"],
        )
        self._pending_cost = {"in_uncached": 0, "in_cached": 0, "out": 0}
        return new_messages, evt

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------

    def _rescore(self, messages: List[dict], ctx: CompactionContext) -> None:
        """One LLM call. Score every non-holed tool obs 1-10 for current relevance."""
        # Find tool obs to score
        tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        scoreable = []
        for i in tool_idxs:
            c = messages[i].get("content") or ""
            if not isinstance(c, str) or c.startswith("[evicted") or c.startswith("[microcompacted"):
                continue
            scoreable.append(i)
        if not scoreable:
            return
        if len(scoreable) > self.max_score_input_msgs:
            scoreable = scoreable[-self.max_score_input_msgs:]  # newest scoreable

        # Build the scoring prompt
        anchor_msg = next((m for m in messages if m.get("role") == "user"), None)
        anchor = ""
        if anchor_msg is not None:
            a = (anchor_msg.get("content") or "")[:1200].replace("\n", " ")
            anchor = f"Task / problem statement:\n{a!r}"

        # Get latest assistant turn for "current focus"
        recent_assistant = ""
        for m in reversed(messages):
            if m.get("role") == "assistant":
                rc = (m.get("content") or "")[:600].replace("\n", " ")
                if rc.strip():
                    recent_assistant = f"Most recent agent reasoning: {rc!r}"
                break

        previews = []
        for k, mi in enumerate(scoreable):
            m = messages[mi]
            content = m.get("content") or ""
            preview = content[:_PREVIEW_CHARS].replace("\n", " ").strip()
            # Identify which tool call produced this obs
            tcid = m.get("tool_call_id", "")
            tool_name = "?"
            for j in range(mi - 1, -1, -1):
                prev = messages[j]
                if prev.get("role") != "assistant":
                    continue
                for tc in (prev.get("tool_calls") or []):
                    if tc.get("id") == tcid:
                        fn = tc.get("function") or {}
                        tool_name = fn.get("name", "?")
                        break
                break
            previews.append(f"[{k}] tool={tool_name} preview={preview!r}")

        scoring_text = (
            "You are a context manager for an LLM coding agent. The agent is "
            "working through the task below. Score each tool result 1-10 for "
            "how likely the agent will need to look at this specific result "
            "AGAIN later (1=safe to drop, exhaust the agent has already moved "
            "past; 10=critical, must keep). Output ONLY a single JSON object "
            'mapping the bracketed index to a score, like {"0": 7, "1": 3}. '
            "No prose.\n\n"
            f"{anchor}\n\n"
            f"{recent_assistant}\n\n"
            "Tool results:\n" + "\n".join(previews) + "\n\nJSON scores:"
        )

        try:
            resp = ctx.summarizer_model.chat(
                [{"role": "user", "content": scoring_text}],
                max_tokens=400,
            )
        except Exception:
            return  # silently fail — keep prior scores

        text = resp.content or ""
        new_scores = _parse_scores(text, len(scoreable))
        for k in range(len(scoreable)):
            sc = new_scores.get(k, _SCORE_FALLBACK)
            self._score_by_pos[scoreable[k]] = sc

        # Track scoring call cost
        if resp.usage is not None:
            self._pending_cost["in_uncached"] += resp.usage.prompt_tokens or 0
            self._pending_cost["out"] += resp.usage.completion_tokens or 0


def _parse_scores(text: str, n: int) -> Dict[int, int]:
    m = _SCORE_RE.search(text)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    out: Dict[int, int] = {}
    for k, v in d.items():
        try:
            ki = int(k)
            vi = int(v)
            if 0 <= ki < n:
                out[ki] = max(1, min(10, vi))
        except Exception:
            continue
    return out
