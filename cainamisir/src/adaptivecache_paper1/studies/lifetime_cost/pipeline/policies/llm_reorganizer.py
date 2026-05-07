"""LLM-as-reorganizer compaction — Paper 1's headline claim.

Uses a small auxiliary LLM (e.g. Qwen3-4B) to score each evictable tool
observation for "is the agent likely to reference this later?", then drops
the bottom-K via hole-leaving (placeholder content, message envelope kept).

Key differences from the structural baselines:
  - position_aware uses a fixed structural rule (size + role) — no
    task-aware judgement.
  - prefix_preserving / microcompact use an LLM to *summarize* — produces
    new content, invalidates more cache than necessary.
  - llm_reorganizer uses an LLM only for a *score-and-drop* decision. No
    new content tokens to render. The cost is one short scoring call per
    compaction event.

Scoring prompt is structured to be robust:
  - Each evictable message gets a [k] index prefix and a 200-char preview
  - The LLM is asked for a JSON map index → 1-10 score
  - Parsing falls back to "all 5" if the LLM output is unparseable

This is "training-free" — the small LLM uses general instruction following.
The 125% milestone in overview.md replaces this scoring head with one
trained via pairwise ranking on oracle eviction decisions.
"""

from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


_PREVIEW_CHARS = 240
_SCORE_FALLBACK = 5
_SCORE_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


class LLMReorganizer(CompactionPolicy):
    name = "llm_reorganizer"

    def __init__(
        self,
        drop_count: int = 3,
        trigger_ratio: float = 0.85,
        protect_recent: int = 4,
        max_score_input_msgs: int = 30,
        use_hard_budget_trigger: bool = False,
        target_ratio: float | None = None,
        min_obs_tokens: int = 200,
        max_drop_score: int = 3,
        emergency_ratio: float = 0.95,
    ):
        super().__init__(
            drop_count=drop_count,
            trigger_ratio=trigger_ratio,
            protect_recent=protect_recent,
            max_score_input_msgs=max_score_input_msgs,
            use_hard_budget_trigger=use_hard_budget_trigger,
            target_ratio=target_ratio,
            min_obs_tokens=min_obs_tokens,
            max_drop_score=max_drop_score,
            emergency_ratio=emergency_ratio,
        )
        self.drop_count = drop_count
        self.trigger_ratio = trigger_ratio
        self.protect_recent = protect_recent
        self.max_score_input_msgs = max_score_input_msgs
        self.use_hard_budget_trigger = use_hard_budget_trigger
        # If target_ratio is set, we drop low-scoring messages until n_tok
        # falls below target_ratio × (hard_)budget. Otherwise we drop a
        # fixed `drop_count` messages.
        self.target_ratio = target_ratio
        self.min_obs_tokens = min_obs_tokens
        # Only drop msgs the LLM scored ≤ max_drop_score (1-10 scale; default
        # 3 = "safe to drop"). Above this, the LLM thinks the obs is still
        # important — refuse to evict. Same Tier-1 philosophy as smart_evict.
        self.max_drop_score = max_drop_score
        # At emergency_ratio of hard_budget, override and drop lowest-scoring
        # regardless to avoid context overflow.
        self.emergency_ratio = emergency_ratio

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        budget_for_trigger = ctx.hard_budget if self.use_hard_budget_trigger else ctx.budget
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * budget_for_trigger:
            return messages, None
        if ctx.summarizer_model is None:
            return messages, None

        # Evictable = tool messages that aren't already small holes and aren't
        # in the protected-recent tail.
        tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_idxs) <= self.protect_recent:
            return messages, None
        evictable = tool_idxs[: -self.protect_recent]
        # Filter out messages too small to bother evicting (already-holed, tiny obs)
        sizes: Dict[int, int] = {}
        for i in evictable:
            content = messages[i].get("content") or ""
            sizes[i] = ctx.tokenizer.count(content) if isinstance(content, str) else 0
        evictable = [i for i in evictable if sizes[i] >= self.min_obs_tokens]
        if not evictable:
            return messages, None
        if len(evictable) > self.max_score_input_msgs:
            evictable = evictable[: self.max_score_input_msgs]

        scores, score_in_tok, score_out_tok = self._score(messages, evictable, ctx)

        # Filter to "safe-to-drop" — only msgs the LLM scored ≤ max_drop_score.
        safe = [i for i in evictable if scores.get(i, _SCORE_FALLBACK) <= self.max_drop_score]

        # Emergency override: at >= emergency_ratio × hard_budget, drop
        # lowest-scoring even if no safe candidates. Avoids OOM.
        is_emergency = n_tok >= self.emergency_ratio * ctx.hard_budget
        if not safe and not is_emergency:
            # We paid the scoring call but found nothing safely droppable.
            # Still record the call cost so analysis can see it.
            return messages, CompactionEvent(
                step=ctx.step, policy=self.name,
                msgs_before=len(messages), msgs_after=len(messages),
                tokens_before=n_tok, tokens_after=n_tok,
                compaction_input_cached_tokens=0,
                compaction_input_uncached_tokens=score_in_tok,
                compaction_output_tokens=score_out_tok,
            )

        eviction_set = safe if safe else evictable

        # Drop lowest-scoring first. If target_ratio set, drop until n_tok ≤
        # target_ratio × budget_for_trigger; else drop fixed drop_count.
        sorted_idxs = sorted(eviction_set, key=lambda i: scores.get(i, _SCORE_FALLBACK))

        new_messages = list(messages)
        evicted_tokens = 0
        target_tokens = (self.target_ratio * budget_for_trigger
                         if self.target_ratio is not None else None)
        for k, i in enumerate(sorted_idxs):
            if target_tokens is None and k >= self.drop_count:
                break
            m = new_messages[i]
            content = m.get("content") or ""
            if not isinstance(content, str):
                continue
            tok = sizes[i]
            holed = dict(m)
            holed["content"] = (
                f"[evicted: ~{tok} tokens, llm_reorganizer score={scores.get(i, _SCORE_FALLBACK)}]"
            )
            new_messages[i] = holed
            evicted_tokens += tok
            if target_tokens is not None:
                n_now = self._token_count(new_messages, ctx.tokenizer)
                if n_now <= target_tokens:
                    break

        if evicted_tokens == 0:
            return messages, None

        n_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok,
            tokens_after=n_after,
            compaction_input_cached_tokens=0,           # the scoring prompt is fresh
            compaction_input_uncached_tokens=score_in_tok,
            compaction_output_tokens=score_out_tok,
        )

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _score(
        self, messages: List[dict], evictable: List[int], ctx: CompactionContext
    ) -> Tuple[Dict[int, int], int, int]:
        # Build a compact scoring prompt referencing each evictable message
        # by a synthetic index 0..K-1 (we'll map back to original positions).
        previews = []
        for k, mi in enumerate(evictable):
            m = messages[mi]
            content = m.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            preview = content[:_PREVIEW_CHARS].replace("\n", " ").strip()
            previews.append(f"[{k}] role={m.get('role')} preview={preview!r}")

        # Anchor on the *first* user message — that's the actual task / problem
        # statement. The last user message in an agent loop is typically a
        # tool-output reminder which is not what we want to weight against.
        # Fall back to the system prompt if no user msg yet.
        anchor = ""
        anchor_msg = None
        for m in messages:
            if m.get("role") == "user":
                anchor_msg = m
                break
        if anchor_msg is None:
            for m in messages:
                if m.get("role") == "system":
                    anchor_msg = m
                    break
        if anchor_msg is not None:
            a = (anchor_msg.get("content") or "")[:1200].replace("\n", " ")
            anchor = f"Task / problem statement:\n{a!r}"

        scoring_text = (
            "You are a context manager for an LLM coding agent. Score each "
            "tool result below 1-10 for how likely the agent is to need it "
            "again later. 1=safe to drop, 10=critical to keep. Output ONLY a "
            "single JSON object mapping the bracketed index to a score, like "
            '{"0": 7, "1": 3}. No prose.\n\n'
            f"{anchor}\n\n"
            "Tool results:\n" + "\n".join(previews) + "\n\nJSON scores:"
        )

        scoring_msgs = [{"role": "user", "content": scoring_text}]
        try:
            resp = ctx.summarizer_model.chat(scoring_msgs, max_tokens=300)
        except Exception:
            return {mi: _SCORE_FALLBACK for mi in evictable}, 0, 0

        text = resp.content or ""
        scores = _parse_scores(text, len(evictable))
        # Map synthetic indices back to original message positions
        out = {evictable[k]: scores.get(k, _SCORE_FALLBACK) for k in range(len(evictable))}

        in_tok = (resp.usage.prompt_tokens if resp.usage else 0)
        out_tok = (resp.usage.completion_tokens if resp.usage else 0)
        return out, in_tok, out_tok


def _parse_scores(text: str, n: int) -> Dict[int, int]:
    """Best-effort JSON-object scrape from `text`. Returns {synthetic_index: score}."""
    # Look for the first {...} block
    m = _SCORE_RE.search(text)
    if not m:
        return {}
    blob = m.group(0)
    try:
        d = json.loads(blob)
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
