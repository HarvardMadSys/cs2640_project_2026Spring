"""Smart, type-aware lazy eviction.

Differences from `evict_oldest`:

1. **Lazy trigger.** Fires only when content tokens approach `hard_budget`,
   not when they cross `soft_budget × trigger_ratio`. The default
   `evict_oldest` triggers at 17K (when budget is 20K and hard is 40K) —
   evicting at 17K leaves 23K of headroom on the table and burns the
   prefix cache for no gain. Smart_evict triggers at `0.85 × hard_budget`,
   defaulting to ~34K, so eviction only runs when we genuinely cannot
   afford to keep all the bytes.

2. **Type-aware scoring.** Tool observations are not all equal. A
   `read_file` result is the source-of-truth content the agent will
   reason against; a `search` result is exhaust the agent can re-run for
   pennies. The scoring rule:

       score(msg) = TYPE_PRIOR[tool_name] × (1 + 0.3 × log(ref_count + 1))

   where `ref_count` is a cheap textual-overlap proxy: how many later
   assistant messages contain at least one rare token from this obs.
   This rewards keeping content that the agent has actually been
   referring back to.

3. **Hole-leaving with score annotation.** Each evicted message keeps its
   tool_call_id and role; only the content is replaced with a small
   `[evicted: ~N tokens, score=S, smart_evict]` marker so the chat
   template doesn't choke on dangling tool refs.

What this does NOT do (yet): true layout reorganization (moving
high-score blocks toward the prefix). That requires careful handling of
assistant→tool pairing under the chat template; deferred to a follow-up
policy if the score-only fix turns out to be insufficient.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


# Tool-type priors. Higher = harder to recover by re-running, more important
# to keep. Tuned by hand from the v1/v2 SWE-bench Lite trajectory analysis:
# read_file misses caused the regex-loop pathology; search hits did not.
TYPE_PRIORS: Dict[str, float] = {
    "read_file": 1.00,    # source of truth, large + idempotent re-fetch but expensive bytes
    "edit_file": 0.90,    # edit confirmation; useful proof-of-action trail
    "run_tests": 0.65,    # output is exhaust, but expensive to re-run
    "list_files": 0.50,   # cheap to re-run, but useful structural context
    "search": 0.20,       # cheap to re-run, mostly exhaust
    "submit": 0.10,       # acknowledgement, can drop
}
_DEFAULT_TYPE_PRIOR = 0.50


class SmartEvict(CompactionPolicy):
    name = "smart_evict"

    def __init__(
        self,
        trigger_ratio: float = 0.85,
        target_ratio: float = 0.55,
        protect_recent: int = 3,
        min_obs_tokens: int = 300,
        use_hard_budget_trigger: bool = True,
        ref_overlap_sample: int = 20,
        max_drop_score: float = 0.4,
        emergency_ratio: float = 0.95,
    ):
        super().__init__(
            trigger_ratio=trigger_ratio,
            target_ratio=target_ratio,
            protect_recent=protect_recent,
            min_obs_tokens=min_obs_tokens,
            use_hard_budget_trigger=use_hard_budget_trigger,
            ref_overlap_sample=ref_overlap_sample,
            max_drop_score=max_drop_score,
            emergency_ratio=emergency_ratio,
        )
        self.trigger_ratio = trigger_ratio
        self.target_ratio = target_ratio
        self.protect_recent = protect_recent
        self.min_obs_tokens = min_obs_tokens
        self.use_hard_budget_trigger = use_hard_budget_trigger
        self.ref_overlap_sample = ref_overlap_sample
        # Only evict obs whose score ≤ max_drop_score. Anything above is
        # considered "still load-bearing" and is left in place — even if
        # the budget bites. (Tier-1 philosophy from system_design.md: only
        # do the cheap eviction when there's clearly something safe to drop;
        # don't compact useful context just to satisfy a budget.)
        self.max_drop_score = max_drop_score
        # Safety override: at this fraction of the hard budget we're
        # close to overflow, so we DO drop the lowest-scoring even if
        # nothing scored ≤ max_drop_score. Avoids hard crashes.
        self.emergency_ratio = emergency_ratio

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        budget_for_trigger = ctx.hard_budget if self.use_hard_budget_trigger else ctx.budget
        n_tok = self._token_count(messages, ctx.tokenizer)
        if n_tok < self.trigger_ratio * budget_for_trigger:
            return messages, None

        # Evictable = tool messages outside the recent-protect window AND
        # large enough to be worth evicting.
        tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_idxs) <= self.protect_recent:
            return messages, None
        evictable_idxs = tool_idxs[: -self.protect_recent]

        # Pre-compute per-msg tokens once (avoids recounting in the eviction loop)
        sizes: Dict[int, int] = {}
        for i in evictable_idxs:
            content = messages[i].get("content") or ""
            sizes[i] = ctx.tokenizer.count(content) if isinstance(content, str) else 0

        # Filter to messages large enough to bother evicting
        candidates = [i for i in evictable_idxs if sizes[i] >= self.min_obs_tokens]
        if not candidates:
            return messages, None

        # Score each candidate
        scores = {i: self._score(messages, i) for i in candidates}

        # Filter to "safe-to-drop" candidates: score ≤ max_drop_score. Anything
        # above is content we believe is still load-bearing for the agent and
        # we refuse to evict it just to satisfy a soft budget. The point of
        # compaction is to remove things we DON'T need anymore — not to make
        # room by sacrificing things we do need.
        safe = [i for i in candidates if scores[i] <= self.max_drop_score]

        # Emergency override: if we're approaching the HARD budget (would
        # overflow soon), evict the lowest-scoring even if nothing scored
        # safely. Bounded by the hard budget so we don't crash; better to
        # damage one obs than to crash the whole task.
        is_emergency = n_tok >= self.emergency_ratio * ctx.hard_budget
        if not safe and not is_emergency:
            return messages, None

        # Choose which set to drop from
        eviction_set = safe if safe else candidates  # fallback to "least bad" in emergency

        # Evict lowest-scoring first until we're under target
        target_tokens = self.target_ratio * (ctx.hard_budget if self.use_hard_budget_trigger else ctx.budget)
        sorted_candidates = sorted(eviction_set, key=lambda i: scores[i])

        new_messages = list(messages)
        evicted_count = 0
        evicted_tokens = 0
        for i in sorted_candidates:
            tok = sizes[i]
            tag = "emergency" if (is_emergency and not safe) else "safe"
            holed = dict(new_messages[i])
            holed["content"] = (
                f"[evicted: ~{tok} tokens, score={scores[i]:.2f}, smart_evict/{tag}]"
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

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, messages: List[dict], idx: int) -> float:
        """type_prior × (1 + 0.3 × log(ref_count + 1))."""
        # 1. Type prior — find the assistant message that issued this tool_call
        m = messages[idx]
        tool_call_id = m.get("tool_call_id")
        tool_name = "_default"
        for j in range(idx - 1, -1, -1):
            prev = messages[j]
            if prev.get("role") != "assistant":
                continue
            for tc in (prev.get("tool_calls") or []):
                if tc.get("id") == tool_call_id:
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "_default")
                    break
            break
        type_prior = TYPE_PRIORS.get(tool_name, _DEFAULT_TYPE_PRIOR)

        # 2. Reference count — does this obs's content show up in later
        # assistant messages? Cheap textual overlap proxy for attention.
        content = m.get("content") or ""
        if not isinstance(content, str):
            return type_prior
        tokens = {t for t in content.split() if len(t) >= 5 and any(c.isalnum() for c in t)}
        # Strip trailing punctuation for matching robustness
        tokens = {t.strip(".,();:[]'\"") for t in tokens}
        tokens = {t for t in tokens if len(t) >= 5}
        if not tokens:
            return type_prior
        sample = list(tokens)[: self.ref_overlap_sample]

        ref_count = 0
        for j in range(idx + 1, len(messages)):
            later = messages[j]
            if later.get("role") != "assistant":
                continue
            later_content = later.get("content") or ""
            if not isinstance(later_content, str) or not later_content:
                continue
            for t in sample:
                if t in later_content:
                    ref_count += 1
                    break  # at most one credit per later message

        return type_prior * (1.0 + 0.3 * math.log(ref_count + 1))
