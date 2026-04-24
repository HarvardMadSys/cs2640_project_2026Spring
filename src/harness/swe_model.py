"""AdaptiveCache model wrapper for mini-swe-agent.

The key insight: on the Anthropic API, messages are a linear list and the
prefix is automatically cached. AdaptiveCache's value comes when eviction
is needed — it evicts low-value COMPLETE STEPS from the middle of the
conversation, preserving the stable prefix for cache reuse.

Contrast with:
- FIFO: evicts oldest steps from the beginning → destroys cached prefix
- Summarization: generates new tokens → destroys cached prefix
- AdaptiveCache: evicts from the middle → cached prefix preserved

We operate at the STEP level (assistant + tool_result pairs), not individual
messages. This naturally respects Anthropic's tool pairing constraint.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from adaptive_cache.segmenter import count_tokens
from adaptive_cache.scorer import IMPORTANCE_PRIOR, STABILITY_PRIOR
from adaptive_cache.types import BlockType

try:
    from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig
except ImportError:
    raise ImportError("mini-swe-agent is required: pip install mini-swe-agent")

logger = logging.getLogger("adaptive_cache_model")


class AdaptiveCacheModelConfig(LitellmModelConfig):
    cache_policy: str = "adaptive"
    """'adaptive', 'fifo', 'summarize', 'compaction', 'kv_adaptive', or 'none'."""
    cache_budget: int = 64_000
    """Token budget. When exceeded, eviction kicks in."""


# --- Step: the atomic unit of context management ---

@dataclass
class Step:
    """A complete agent step: assistant message + tool result(s).

    This is the atomic eviction unit. You evict a whole step or keep it.
    """
    step_index: int                   # 0-based position in conversation
    msg_indices: list[int]            # indices into the messages list
    token_count: int = 0
    block_type: BlockType = BlockType.OBS_GENERIC
    importance: float = 0.0
    stability: float = 0.0
    reference_count: int = 0
    importance_history: list[float] = field(default_factory=list)
    protected: bool = False           # system/task — never evict

    @property
    def score(self) -> float:
        return self.importance * self.stability


def _parse_steps(messages: list[dict]) -> list[Step]:
    """Group messages into Steps (the atomic eviction unit).

    Groups:
    - system message → protected step
    - first user message (task) → protected step
    - (assistant + following tool/user-obs messages) → one step
    - standalone user messages → own step
    """
    steps: list[Step] = []
    i = 0
    step_idx = 0
    seen_task = False

    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "system":
            steps.append(Step(step_index=step_idx, msg_indices=[i], protected=True,
                            block_type=BlockType.SYSTEM,
                            token_count=count_tokens(_extract_content(msg)) or 1))
            step_idx += 1
            i += 1

        elif role == "user" and not seen_task:
            seen_task = True
            steps.append(Step(step_index=step_idx, msg_indices=[i], protected=True,
                            block_type=BlockType.TASK,
                            token_count=count_tokens(_extract_content(msg)) or 1))
            step_idx += 1
            i += 1

        elif role == "assistant":
            # Collect assistant + all following tool/user-observation messages
            indices = [i]
            tokens = count_tokens(_extract_content(msg)) or 1
            obs_content = ""
            i += 1
            while i < len(messages) and messages[i].get("role") in ("tool", "user"):
                # Stop if it's a user message that isn't a tool observation
                if messages[i].get("role") == "user":
                    content = _extract_content(messages[i])
                    if "<output>" not in content and "<returncode>" not in content:
                        break
                indices.append(i)
                c = _extract_content(messages[i])
                tokens += count_tokens(c) or 1
                obs_content += c
                i += 1

            bt = _classify_obs(obs_content)
            steps.append(Step(step_index=step_idx, msg_indices=indices,
                            block_type=bt, token_count=tokens))
            step_idx += 1

        elif role == "exit":
            steps.append(Step(step_index=step_idx, msg_indices=[i], protected=True,
                            block_type=BlockType.THOUGHT,
                            token_count=count_tokens(_extract_content(msg)) or 1))
            step_idx += 1
            i += 1

        else:
            # Standalone user/other message
            steps.append(Step(step_index=step_idx, msg_indices=[i],
                            block_type=BlockType.OBS_GENERIC,
                            token_count=count_tokens(_extract_content(msg)) or 1))
            step_idx += 1
            i += 1

    return steps


def _classify_obs(content: str) -> BlockType:
    lower = content[:500].lower()
    if any(w in lower for w in ("error", "exception", "traceback", "failed")):
        return BlockType.OBS_ERROR
    if any(w in lower for w in ("def ", "class ", "import ", "function ")):
        return BlockType.OBS_FILE
    return BlockType.OBS_SHELL


def _score_step(step: Step, recent_content: str) -> None:
    """Score a step's importance and stability using structural priors + reference count."""
    # Importance: structural prior + reference count
    structural = IMPORTANCE_PRIOR.get(step.block_type, 0.3)

    # Reference count: check if this step's content is referenced in recent output
    # (Simple: count how many times identifiers from this step appear later)
    ref_boost = min(step.reference_count / 10.0, 1.0)

    step.importance = 0.6 * structural + 0.4 * ref_boost

    # Stability: structural prior + importance variance
    type_stability = STABILITY_PRIOR.get(step.block_type, 0.3)
    if len(step.importance_history) >= 2:
        mean = sum(step.importance_history) / len(step.importance_history)
        var = sum((x - mean) ** 2 for x in step.importance_history) / len(step.importance_history)
        variance_stability = max(0.0, 1.0 - min(var, 1.0))
    else:
        variance_stability = 0.5  # uncertain for new steps

    step.stability = 0.6 * type_stability + 0.4 * variance_stability
    step.importance_history.append(step.importance)
    if len(step.importance_history) > 10:
        step.importance_history.pop(0)


# --- Main model class ---

class AdaptiveCacheModel(LitellmModel):
    """LitellmModel with step-level context management.

    Operates on complete steps (assistant + tool pairs) as the atomic unit.
    """

    def __init__(self, *, config_class=AdaptiveCacheModelConfig, **kwargs):
        # For adaptive policy, we handle cache_control ourselves — disable the default
        if kwargs.get("cache_policy", "adaptive") == "adaptive":
            kwargs.setdefault("set_cache_control", None)
        super().__init__(config_class=config_class, **kwargs)
        self._policy = self.config.cache_policy
        self._budget = self.config.cache_budget
        self._call_count = 0
        self._steps: list[Step] = []  # persistent step scoring state
        self._last_prompt_tokens = 0   # actual API token count from last response
        self.cache_trace: list[dict] = []

        # KV-mode fields (used only when cache_policy == "kv_adaptive")
        self.kv_client: Optional[Any] = None   # LMCacheClient instance
        self.kv_controller: Optional[Any] = None  # KVController instance

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self._call_count += 1

        if self._policy == "none":
            result = super().query(messages, **kwargs)
            self._log(messages, messages, result)
            return result

        if self._policy == "compaction":
            result = super().query(messages, **{
                **kwargs,
                "extra_headers": {"anthropic-beta": "compact-2026-01-12"},
                "context_management": {
                    "edits": [{"type": "compact_20260112", "trigger": self._budget}]
                },
            })
            self._log(messages, messages, result)
            return result

        if self._policy == "kv_adaptive":
            return self._apply_kv_adaptive(messages)

        optimized = self._apply(messages)
        result = super().query(optimized, **kwargs)
        self._log(messages, optimized, result)
        return result

    def _apply(self, messages: list[dict]) -> list[dict]:
        if self._policy == "adaptive":
            return self._apply_adaptive(messages)
        elif self._policy == "fifo":
            return self._apply_fifo(messages)
        elif self._policy == "summarize":
            return self._apply_summarize(messages)
        return messages

    # --- Adaptive: position-aware eviction that maximizes prefix cache ---

    def _apply_adaptive(self, messages: list[dict]) -> list[dict]:
        """Evict steps to maximize the length of the cached prefix.

        Key insight: the prefix cache preserves everything BEFORE the first
        evicted message. So evicting step 20 preserves 19 steps of cache,
        while evicting step 3 only preserves 2. Position matters more than
        importance score for cache efficiency.

        Strategy:
        1. Score steps by importance × stability
        2. Among eviction candidates, prefer evicting LATER steps (preserves
           more prefix cache) unless a step is critically important
        3. Set cache_control breakpoint at the boundary between the stable
           prefix and the first eviction point
        """
        steps = _parse_steps(messages)

        # Score each non-protected step
        recent = " ".join(_extract_content(m) for m in messages[-4:])
        for step in steps:
            if not step.protected:
                for idx in step.msg_indices:
                    content = _extract_content(messages[idx])
                    for ident in _extract_short_identifiers(content):
                        if ident in recent:
                            step.reference_count += 1
                _score_step(step, recent)

        # Check if we're over budget.
        # Use actual API token count from last response if available (more accurate
        # than tiktoken estimate which misses message framing / tool schemas).
        total = self._last_prompt_tokens if self._last_prompt_tokens > 0 else sum(s.token_count for s in steps)
        if total <= self._budget:
            return self._set_cache_breakpoint(messages, steps)

        non_protected = [s for s in steps if not s.protected]
        if not non_protected:
            return messages

        # Keep last 3 steps for recency
        recency_keep = min(3, max(1, len(non_protected) - 1))
        evictable = non_protected[:-recency_keep] if recency_keep < len(non_protected) else []

        if not evictable:
            return messages

        # Position-aware eviction: prefer evicting LATER steps.
        # Eviction cost = (prefix tokens lost) / (tokens freed)
        # Evicting a later step loses less prefix than evicting an earlier step.
        #
        # Combined score: evict steps with lowest (importance_score - position_penalty)
        # where position_penalty is higher for early steps (costly to evict).
        max_pos = max(s.step_index for s in evictable) + 1
        for step in evictable:
            # position_ratio: 0 = earliest (expensive to evict), 1 = latest (cheap to evict)
            position_ratio = step.step_index / max_pos if max_pos > 0 else 0
            # Eviction priority: low score + late position → evict first
            # We want to evict steps that are (a) low value AND (b) late in the conversation
            step._evict_priority = step.score - 0.3 * (1 - position_ratio)

        # Sort: lowest eviction priority = evict first
        evictable_sorted = sorted(evictable, key=lambda s: s._evict_priority)

        evicted_indices: set[int] = set()
        first_evict_step = None
        for step in evictable_sorted:
            if total <= self._budget:
                break
            total -= step.token_count
            for idx in step.msg_indices:
                evicted_indices.add(idx)
            if first_evict_step is None or step.step_index < first_evict_step:
                first_evict_step = step.step_index

        # Count prefix preserved (steps before first eviction)
        prefix_preserved = 0
        if first_evict_step is not None:
            prefix_preserved = sum(
                s.token_count for s in steps if s.step_index < first_evict_step
            )

        logger.info(
            "Evicted %d msgs (%d tokens freed). Prefix preserved: %d tokens. Remaining: %d tokens.",
            len(evicted_indices), sum(s.token_count for s in evictable_sorted if any(i in evicted_indices for i in s.msg_indices)),
            prefix_preserved, total,
        )

        result = [m for i, m in enumerate(messages) if i not in evicted_indices]
        return self._set_cache_breakpoint(result, _parse_steps(result))

    def _set_cache_breakpoint(self, messages: list[dict], steps: list[Step]) -> list[dict]:
        """Set cache_control breakpoint at the end of the stable prefix.

        Instead of marking the last message (mini-swe-agent default), we mark
        the last message of the stable prefix zone — the system + task + early
        high-scoring steps that we'll never evict. This tells Anthropic to
        aggressively cache everything up to that point.
        """
        import copy
        messages = copy.deepcopy(messages)

        # Find the last protected step's last message index
        # (or last high-scoring step that we consider "stable prefix")
        breakpoint_msg_idx = None
        for step in steps:
            if step.protected:
                breakpoint_msg_idx = step.msg_indices[-1]

        if breakpoint_msg_idx is not None and breakpoint_msg_idx < len(messages):
            msg = messages[breakpoint_msg_idx]
            content = msg.get("content", "")
            if isinstance(content, str):
                msg["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list):
                # Add cache_control to the last text block
                for block in reversed(content):
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["cache_control"] = {"type": "ephemeral"}
                        break

        return messages

    # --- KV Adaptive: block-level LMCache eviction (Tier 1) + message reorg (Tier 2) ---

    def _apply_kv_adaptive(self, messages: list[dict]) -> dict:
        """Position-aware context management with optional KV-level block eviction.

        Architecture (two-tier):
          Tier 2 (message-level, always active): Uses KVController to score blocks
              and reorganize the message list when the importance structure shifts.
              Works with any backend (Anthropic, vLLM, etc.).
          Tier 1 (KV-level, requires vLLM+LMCache): After each step, deletes low-value
              KV blocks from LMCache so vLLM recomputes only those positions on the
              next call. The prompt bytes stay identical — the prefix cache is never
              invalidated. Requires self.kv_client pointing at the same vLLM server.

        Inference always goes through super().query() (litellm), which handles OpenAI
        tool calling correctly. The KV controller runs as a post-step side effect.

        For Tier 1 KV eviction to be meaningful:
          - litellm must be pointed at the same vLLM+LMCache server as kv_client
          - Set model_name to the vLLM server's OpenAI-compatible endpoint
          - This is the Phase E integration from IMPLEMENTATION_PLAN.md
        """
        # --- Lazy-initialize KVController (Tier 2, no KV client needed) ---
        if self.kv_controller is None:
            try:
                from adaptive_cache.kv_controller import KVController
                from adaptive_cache.config import CacheConfig
                cfg = CacheConfig(soft_budget=self._budget)
                self.kv_controller = KVController(config=cfg)
            except Exception as e:
                logger.warning("KVController init failed: %s", e)

        # --- Lazy-initialize KV client (Tier 1, requires Modal LLMServer) ---
        if self.kv_client is None:
            try:
                from harness.lmcache_client import LMCacheClient
                self.kv_client = LMCacheClient(mode="modal")
            except Exception as e:
                logger.debug("KV client unavailable (Tier 1 disabled): %s", e)

        # --- Step 1: Generate via litellm (handles tool calls correctly) ---
        result = super().query(messages)
        self._log(messages, messages, result)

        # --- Step 2: KVController post-step (Tier 2 always, Tier 1 if kv_client set) ---
        if self.kv_controller is not None:
            try:
                # prompt_token_ids: needed for Tier 1 block hash computation.
                # Not available from litellm response. Pass empty list to skip Tier 1
                # KV deletion while still enabling Tier 2 (message-level reorg).
                # When using serve.py directly (not litellm), pass real token IDs here.
                managed, did_reorg = self.kv_controller.on_step_complete(
                    messages,
                    prompt_token_ids=[],  # Tier 1 skipped without vLLM token IDs
                    lmcache_client=None,  # Tier 1 disabled until vLLM endpoint wired
                )
                if did_reorg:
                    logger.info("Step %d: KVController Tier-2 reorg fired.", self._call_count)
            except Exception as e:
                logger.warning("KVController step failed: %s", e)

        return result

    # --- FIFO: evict oldest steps from the BEGINNING (destroys prefix cache) ---

    def _apply_fifo(self, messages: list[dict]) -> list[dict]:
        """Evict oldest non-protected steps. This destroys the cached prefix."""
        steps = _parse_steps(messages)
        total = sum(s.token_count for s in steps)
        if total <= self._budget:
            return messages

        evicted_indices: set[int] = set()
        for step in steps:
            if total <= self._budget:
                break
            if step.protected:
                continue
            total -= step.token_count
            for idx in step.msg_indices:
                evicted_indices.add(idx)

        return [m for i, m in enumerate(messages) if i not in evicted_indices]

    # --- Summarize: compress old steps into a summary (destroys prefix cache) ---

    def _apply_summarize(self, messages: list[dict]) -> list[dict]:
        """Replace old steps with an LLM-generated summary.

        Only summarizes once when crossing the budget threshold — tracks
        whether a summary already exists to avoid re-summarizing every step.
        Uses the same model (fair comparison: same capability for the summary).
        """
        msg_tokens = [count_tokens(_extract_content(m)) or 1 for m in messages]
        total = sum(msg_tokens)
        if total <= self._budget:
            return messages

        # Don't re-summarize if we already have a summary in the messages
        # (indicated by "[Summary of previous work]" in a user message)
        has_summary = any(
            m.get("role") == "user" and "[Summary of previous work]" in _extract_content(m)
            for m in messages
        )

        if has_summary:
            # Already summarized before — just keep last messages that fit budget
            # Find the summary message and keep everything from there
            steps = _parse_steps(messages)
            total = sum(s.token_count for s in steps)
            if total <= self._budget:
                return messages
            # Evict oldest non-protected, non-summary steps (FIFO fallback)
            evicted_indices: set[int] = set()
            for step in steps:
                if total <= self._budget:
                    break
                if step.protected:
                    continue
                # Don't evict the summary message
                step_content = " ".join(_extract_content(messages[i]) for i in step.msg_indices)
                if "[Summary of previous work]" in step_content:
                    continue
                total -= step.token_count
                for idx in step.msg_indices:
                    evicted_indices.add(idx)
            return [m for i, m in enumerate(messages) if i not in evicted_indices]

        # First time over budget — generate a summary
        keep_start = min(2, len(messages))
        keep_end_start = max(keep_start, len(messages) - 6)
        to_summarize = messages[keep_start:keep_end_start]
        if not to_summarize:
            return messages

        summary_content = "\n\n".join(
            f"[{m.get('role', '?')}]: {_extract_content(m)[:500]}"
            for m in to_summarize
        )

        try:
            import litellm
            resp = litellm.completion(
                model=self.config.model_name,
                messages=[
                    {"role": "system", "content": "Summarize this conversation history concisely. Focus on: files examined, findings, changes made, what remains. Be brief."},
                    {"role": "user", "content": summary_content},
                ],
                max_tokens=500,
                temperature=0.0,
            )
            summary = resp.choices[0].message.content or ""
            logger.info("Summarized %d messages → %d chars", len(to_summarize), len(summary))
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            summary = f"[Summary of {len(to_summarize)} previous steps]"

        summary_msg = {"role": "user", "content": f"[Summary of previous work]\n{summary}"}
        return messages[:keep_start] + [summary_msg] + messages[keep_end_start:]

    # --- Logging with REAL cache stats from API ---

    def _log(self, original_msgs: list[dict], sent_msgs: list[dict], result: dict) -> None:
        """Log cache statistics from the API response.

        Handles both Anthropic (cache_read_input_tokens) and vLLM/OpenAI
        (prompt_tokens_details.cached_tokens) response formats.
        """
        response = result.get("extra", {}).get("response", {})
        usage = response.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0

        # Anthropic cache stats
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0

        # vLLM / OpenAI-compatible cache stats (prefix cache hits)
        if cache_read == 0:
            details = usage.get("prompt_tokens_details", {}) or {}
            cache_read = details.get("cached_tokens", 0) or 0

        real_hit_rate = cache_read / prompt_tokens if prompt_tokens > 0 else 0.0

        # Track actual API token count for next step's budget enforcement
        self._last_prompt_tokens = prompt_tokens

        cost = result.get("extra", {}).get("cost", 0.0)

        entry = {
            "step": self._call_count,
            "policy": self._policy,
            "original_messages": len(original_msgs),
            "sent_messages": len(sent_msgs),
            "messages_evicted": len(original_msgs) - len(sent_msgs),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cache_hit_rate": real_hit_rate,
            "cost": cost,
            "timestamp": time.time(),
        }
        self.cache_trace.append(entry)

        logger.info(
            "Step %d: %d→%d msgs, %d prompt tok, cache_read=%d (%.1f%%), $%.4f",
            self._call_count, len(original_msgs), len(sent_msgs),
            prompt_tokens, cache_read, real_hit_rate * 100, cost,
        )

    def serialize(self) -> dict:
        data = super().serialize()
        data["cache_trace"] = self.cache_trace
        data["cache_policy"] = self._policy
        data["cache_budget"] = self._budget
        return data


# --- Utilities ---

def _extract_content(msg: dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return str(content) if content else ""


def _extract_short_identifiers(content: str) -> list[str]:
    """Extract likely identifiers for reference counting."""
    import re
    idents = []
    idents.extend(re.findall(r"[\w./]+\.(?:py|js|ts|go|rs)", content))
    idents.extend(re.findall(r"(?:def|class|func)\s+(\w+)", content))
    return idents
