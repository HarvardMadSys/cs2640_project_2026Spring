"""Memento policy — lazy compaction of tool messages.

The policy waits until total context exceeds a trigger ratio of the
budget, then generates mementos for the OLDEST un-memento'd tool
messages until projected context drops below a target ratio. This
mirrors the smart_evict / prefix_preserving trigger logic from Paper 1.

Why lazy: an eager "memento every big obs" is wasteful — most agent
steps don't need memento at all (the agent moves on; the obs gets
naturally pushed back by new content but doesn't have to leave KV
since the budget isn't hit). Each memento is a Haiku API call (~3-4s
latency, ~$0.005). Generating one per turn dominates wall-clock and
adds up over an eval.

Lazy generation keeps the same structural property (latest big obs
gets evicted via masking when context fills up) but does so only when
needed.

The policy is also the natural place to record memento-generation
cost as a CompactionEvent so analysis treats it the same as any other
compaction.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Pipeline imports (relative — works in both worktree and editable installs).
from ...pipeline.policies.base import CompactionContext, CompactionPolicy
from ...pipeline.types import CompactionEvent
from ...pipeline.tokenization import count_messages
from ...pipeline.benchmarks.base import Tool

from ..memento_writer import HaikuMementoWriter
from .recall_strategy import RecallStrategy, build_recall_strategy


def _tool_obs(msg: Dict[str, Any]) -> Optional[str]:
    """Return the obs text if the message is a tool message, else None."""
    if msg.get("role") != "tool":
        return None
    c = msg.get("content")
    return c if isinstance(c, str) else None


class MementoPolicy(CompactionPolicy):
    """Lazy memento generator — tags oldest tool obs only when budget bites.

    Args:
        min_obs_chars: Don't memento obs smaller than this — too small to
            be worth the API call + evict cost.
        trigger_ratio: Fire when total tokens exceed `trigger_ratio * budget`.
        target_ratio: Stop firing once tokens estimated to fall below
            `target_ratio * budget` after generating mementos.
        max_obs_chars: Truncate large obs sent to Haiku.
        writer: Inject a custom writer (useful for tests).
    """

    name = "memento"

    def __init__(
        self,
        *,
        min_obs_chars: int = 500,
        trigger_ratio: float = 0.85,
        target_ratio: float = 0.55,
        compaction_enabled: bool = True,
        recall_enabled: bool = True,
        recall_low_water_ratio: float = 0.60,
        recall_cooldown_steps: int = 3,
        recall_strategy: str = "lru",
        recall_strategy_kwargs: Optional[Dict[str, Any]] = None,
        recall_query_window: int = 4,
        recall_mode: str = "inplace",
        recall_tool_enabled: bool = False,
        writer: Optional[HaikuMementoWriter] = None,
        memento_model: str = "claude-haiku-4-5",
        max_obs_chars: int = 8000,
        eager_compact_at_suffix: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._min_obs_chars = min_obs_chars
        self._trigger_ratio = trigger_ratio
        self._eager_compact_at_suffix = eager_compact_at_suffix
        self._target_ratio = target_ratio
        self._compaction_enabled = compaction_enabled
        self._recall_enabled = recall_enabled
        self._recall_low_water_ratio = recall_low_water_ratio
        self._recall_cooldown_steps = recall_cooldown_steps
        self._recall_strategy: RecallStrategy = build_recall_strategy(
            recall_strategy, **(recall_strategy_kwargs or {})
        )
        self._recall_query_window = recall_query_window
        if recall_mode not in ("inplace", "append", "attmask", "drop", "kvrestore"):
            raise ValueError(
                f"recall_mode must be 'inplace' | 'append' | 'attmask' | "
                f"'drop' | 'kvrestore', got {recall_mode!r}"
            )
        self._recall_mode = recall_mode
        # Phase 4e: attmask mode stages obs_text payloads here. The runner
        # drains this list before the next chat() call and asks the
        # adapter to queue_recall(obs_text), which writes to the engine's
        # IPC file. The engine then skips masking for that obs on its
        # next compaction so attention can read it back.
        self._pending_attmask_recalls: List[str] = []
        # Phase 3c: kvrestore mode stages obs_text payloads here. Runner
        # drains and calls model.queue_kv_restore(obs_text), which hashes
        # the obs and writes to the engine's kv_restore queue. The
        # scheduler picks it up at the next request's transition to
        # RUNNING, allocates fresh GPU blocks, queues a worker CPU→GPU
        # copy, and splices the blocks into req_to_blocks at the obs's
        # original logical position.
        self._pending_kvrestore_recalls: List[str] = []
        self._writer = writer or HaikuMementoWriter(
            model=memento_model, max_obs_chars=max_obs_chars
        )
        # Phase 5: model-controlled recall. When enabled, every memento we
        # generate gets tagged `[memento_id=mem-N]` and the original obs is
        # registered in `_recall_table`. A `recall(memento_id)` tool exposed
        # to the model lets it deliberately bring back the full obs by id.
        self._recall_tool_enabled = recall_tool_enabled
        self._recall_table: Dict[str, str] = {}
        self._memento_counter: int = 0

    def _estimate_tokens(self, messages: List[Dict[str, Any]], ctx: CompactionContext) -> int:
        """Use the runner-provided tokenizer for total context estimate."""
        return count_messages(messages, ctx.tokenizer)

    def _recent_text_window(self, messages: List[Dict[str, Any]]) -> str:
        """Concatenate the last K messages' text content as the recall query.

        Captures the agent's most recent reasoning + tool args, which is the
        best free signal for "what obs does the agent need now."
        """
        import json
        K = max(1, self._recall_query_window)
        chunks: List[str] = []
        for m in messages[-K:]:
            role = m.get("role", "")
            content = m.get("content")
            if isinstance(content, str) and content:
                chunks.append(f"[{role}] {content}")
            tcs = m.get("tool_calls")
            if tcs:
                chunks.append(json.dumps(tcs))
        return "\n".join(chunks)

    def _rendered_token_estimate(
        self, messages: List[Dict[str, Any]], ctx: CompactionContext
    ) -> int:
        """Approximate the size of the prompt the engine actually sees.

        A tool message with `memento` set renders as the short memento text
        (`[tool_response, evicted, memento]\\n{memento}`) — the full obs is
        never sent to the engine. count_messages counts the underlying
        `content`, which overcounts by 5-10× once compaction has fired and
        is therefore wrong for trigger logic that wants to track engine
        load.
        """
        import json
        total = 0
        for m in messages:
            total += 3
            role = m.get("role")
            if role == "tool" and m.get("memento"):
                total += ctx.tokenizer.count(m.get("memento") or "")
                continue
            content = m.get("content") or ""
            if isinstance(content, str):
                total += ctx.tokenizer.count(content)
            else:
                total += ctx.tokenizer.count(json.dumps(content))
            tcs = m.get("tool_calls")
            if tcs:
                total += ctx.tokenizer.count(json.dumps(tcs))
            name = m.get("name")
            if isinstance(name, str):
                total += ctx.tokenizer.count(name)
        return total

    def maybe_compact(
        self,
        messages: List[Dict[str, Any]],
        ctx: CompactionContext,
    ) -> Tuple[List[Dict[str, Any]], Optional[CompactionEvent]]:
        # 0. Compaction-disabled (true full-context baseline).
        if not self._compaction_enabled:
            return messages, None

        # Eager-at-suffix mode: compact the just-arrived tool obs while it
        # is still the LAST tool message in the prompt. The memento appendix
        # `<|fim_prefix|>memento<|fim_middle|>` lands at the suffix tail
        # (no mid-prompt insertion), so the chain hash up to and including
        # this obs is identical across every subsequent chat — no
        # prefix-cache cliff at the next step. Skips the trigger / target
        # ratio gating: every qualifying tool obs gets memento'd as it
        # arrives. Pairs naturally with `always_wrap=True` in the renderer
        # so the obs's pre-memento and post-memento renderings differ only
        # by the appendix bytes (no structural reformatting).
        total_tok = self._estimate_tokens(messages, ctx)  # used downstream
        if self._eager_compact_at_suffix:
            tool_indices = [i for i, m in enumerate(messages)
                            if m.get("role") == "tool"]
            if not tool_indices:
                return messages, None
            last_idx = tool_indices[-1]
            m = messages[last_idx]
            if m.get("memento"):
                return messages, None
            recalled_at = m.get("recalled_step")
            if (recalled_at is not None
                    and ctx.step - int(recalled_at) < self._recall_cooldown_steps):
                return messages, None
            obs = m.get("content", "")
            if not isinstance(obs, str) or len(obs) < self._min_obs_chars:
                return messages, None
            print(f"[policy-compact-eager] step={ctx.step} idx={last_idx} "
                  f"obs_chars={len(obs)} (suffix-memento, no mid-prompt insertion)",
                  flush=True)
            candidates = [last_idx]
        else:
            # 1. Trigger check — total tokens must exceed trigger threshold.
            trigger = int(self._trigger_ratio * ctx.budget)
            if total_tok < trigger:
                return messages, None

            # 2. Find tool messages that are big enough and not yet memento'd,
            #    in OLDEST-FIRST order (we evict the oldest aggressively-bigger
            #    obs first; recent obs stay visible).
            # Heuristic: average ~4 chars/token. Skip recent N=2 tool msgs to
            # leave the agent's most recent context intact.
            candidates: List[int] = []
            recent_skip = 2
            # walk in chronological order; collect indices, then drop the last N
            all_tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
            keep_recent_set = set(all_tool_indices[-recent_skip:]) if all_tool_indices else set()
            for i in all_tool_indices:
                if i in keep_recent_set:
                    continue
                m = messages[i]
                if m.get("memento"):
                    continue
                # Cooldown: don't immediately re-memento something that was just
                # recalled. Without this, recall+compact thrash within a step pair.
                recalled_at = m.get("recalled_step")
                if (
                    recalled_at is not None
                    and ctx.step - int(recalled_at) < self._recall_cooldown_steps
                ):
                    continue
                obs = m.get("content", "")
                if not isinstance(obs, str) or len(obs) < self._min_obs_chars:
                    continue
                candidates.append(i)

            if not candidates:
                return messages, None

        # 3. Generate mementos in oldest-first order, until projected context
        #    falls below target_ratio * budget. Each generated memento
        #    "saves" roughly (obs_tokens - memento_tokens) ≈ 90% of obs size.
        # Phase 6: cap to ONE memento per maybe_compact call. The Phase 5
        # smoke surfaced a cache cliff (99% → 27% prefix-cache hit) when
        # five mementos got generated in a single burst — every memento
        # inserted in the middle of the prompt shifts every suffix token,
        # invalidating prefix cache past it. Generating one at a time lets
        # subsequent chats absorb the new memento gracefully (the next
        # chat's prompt is a clean superset of the prior, suffix-only tax).
        target = int(self._target_ratio * ctx.budget)
        max_per_call = 1
        t0 = time.perf_counter()
        in_toks_total = 0
        out_toks_total = 0
        cost_total = 0.0
        bytes_tagged = 0
        projected_total = total_tok
        n_fired = 0

        for i in candidates:
            if projected_total < target:
                break
            if n_fired >= max_per_call:
                break
            msg = messages[i]
            # Round-trip shortcut: if this msg was recalled (memento cleared
            # but prior_memento stashed) and the cooldown is now satisfied,
            # restore the stashed text instead of paying Haiku again.
            stashed = msg.get("prior_memento")
            if stashed:
                text = stashed
                msg["memento"] = text
                msg["prior_memento"] = None
                msg["memento_step"] = ctx.step
                # Phase 9: re-marker on next chat so engine recaptures.
                msg["_compacted_already"] = False
            else:
                tool_name, tool_args = _trace_tool_call(messages, i)
                text, usage = self._writer.write(
                    obs=msg["content"],
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                in_toks_total += usage.input_tokens
                out_toks_total += usage.output_tokens
                cost_total += usage.cost_usd
                # Phase 5: tag every freshly-written memento with a stable
                # `mem-N` id so the model can refer to it via the recall
                # tool. The full obs is registered in _recall_table so the
                # tool handler can look it up and queue_recall(obs_text).
                if self._recall_tool_enabled:
                    self._memento_counter += 1
                    mem_id = f"mem-{self._memento_counter}"
                    text = f"[memento_id={mem_id}]\n{text}"
                    self._recall_table[mem_id] = msg["content"]
                    msg["memento_id"] = mem_id
                msg["memento"] = text
                msg["memento_step"] = ctx.step
                # Phase 9: ensure markers fire on the chat after this set.
                msg["_compacted_already"] = False
                # Phase 7: when in `drop` recall mode, the obs needs to be
                # rendered with markers ONE more time so the engine can
                # capture+pin its KV. After that capture chat, the runner
                # flips `_obs_dropped=True` and subsequent renders drop the
                # obs. Mark this msg as "fresh" so the renderer keeps obs
                # visible for the next chat only.
                if self._recall_mode == "drop":
                    msg["_freshly_mementoed"] = True
                    msg["_obs_dropped"] = False
                # Phase 6: compress the matching assistant message too.
                # The asst turn that issued this tool_call carries reasoning
                # text + tool_calls JSON — typically 50-300 tokens of
                # scaffolding that's redundant once the tool obs is
                # summarized. We replace it with a one-line ref. The chat
                # template still renders an asst turn, just without bulky
                # content/tool_calls.
                #
                # SKIP under drop mode: drop+restore relies on vLLM's
                # content-hash prefix cache finding the obs's pinned KV
                # blocks. Block hashes are chained through the prefix, so
                # mutating the asst message preceding the obs would break
                # the chain → cache miss → re-prefill of obs (defeats the
                # whole point). Asst is small (~100 tok) anyway; not worth
                # the trade.
                if self._recall_mode != "drop":
                    _compact_paired_asst(messages, i)
            bytes_tagged += len(msg["content"])
            n_fired += 1
            # Project size reduction: obs goes from ~len/4 tokens to
            # roughly len(memento)/4 tokens once rendered as inline plain text.
            obs_tok = len(msg["content"]) // 4
            mem_tok = len(text) // 4
            projected_total -= max(0, obs_tok - mem_tok)

        if n_fired == 0:
            # Nothing fired (target already met by the time we ran)
            return messages, None

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

    def maybe_recall(
        self,
        messages: List[Dict[str, Any]],
        ctx: CompactionContext,
    ) -> Tuple[List[Dict[str, Any]], Optional[CompactionEvent]]:
        """Restore an evicted obs back into the engine's view.

        Two modes for HOW the obs comes back:

        * `inplace` — clear the original tool message's `memento` field so the
          renderer falls back to the full obs at its original chronological
          position. This is what v1 shipped. Cost is high: adding tokens in
          the middle of the prompt shifts every suffix position, killing the
          prefix cache past that point (≈Paper-1 compaction-event cliff).

        * `append` (default) — leave the original message mementoed; push a
          synthetic user message at the END of the conversation containing
          `[recalled, obs_id=N, originally_step=K]\\n<full obs>`. The
          chronological prefix is unchanged; only an addendum appends to the
          tail. Prefix cache hits everything before the addendum, so cost is
          ≈ prefilling the obs (~$0.001 vs ~$0.10 cliff). The "stale
          historical truth" framing — agent really did proceed memento-only;
          recall extends the present with new context rather than rewriting
          the past.

        Trigger: total tokens below `recall_low_water_ratio * budget`. The
        intuition is that if we have spare budget, bringing back one obs is
        cheap and lets the model see the bytes instead of the placeholder.

        Restores at most one obs per step (LRU floor; later policies will
        do similarity-driven recall of multiple at once).
        """
        if not self._recall_enabled:
            return messages, None

        # Headroom check — only recall when the engine prompt has room to
        # grow back. We use a memento-aware estimate so the trigger reflects
        # what the engine actually sees, not the raw conversation size.
        total_tok = self._rendered_token_estimate(messages, ctx)
        low_water = int(self._recall_low_water_ratio * ctx.budget)
        n_memento = sum(
            1 for m in messages
            if m.get("role") == "tool" and m.get("memento")
        )
        if total_tok >= low_water:
            print(f"[policy-recall] step={ctx.step} SKIP: "
                  f"total_tok={total_tok} >= low_water={low_water} "
                  f"(n_memento'd={n_memento})",
                  flush=True)
            return messages, None
        print(f"[policy-recall] step={ctx.step} eligible: "
              f"total_tok={total_tok} < low_water={low_water} "
              f"n_memento'd={n_memento} mode={self._recall_mode}",
              flush=True)

        # Build a content-aware "query" for the strategy: the trailing
        # assistant/tool/user content. LRU ignores it; embedding-similarity
        # uses it to pick what the agent is currently focused on.
        recent_text = self._recent_text_window(messages)
        target_idx = self._recall_strategy.pick(
            messages, step=ctx.step, recent_text=recent_text
        )

        if target_idx is None:
            print(f"[policy-recall] step={ctx.step} SKIP: "
                  f"strategy.pick returned None (no eligible candidates)",
                  flush=True)
            return messages, None
        print(f"[policy-recall] step={ctx.step} picked target_idx={target_idx} "
              f"(memento_step={messages[target_idx].get('memento_step')})",
              flush=True)

        t0 = time.perf_counter()
        msg = messages[target_idx]

        if self._recall_mode == "inplace":
            # v1 behavior: clear memento → renderer drops in full obs at the
            # original position. Stash prior memento so the next compaction
            # can restore it without re-paying Haiku.
            msg["prior_memento"] = msg.get("memento")
            msg["memento"] = None
            msg["recalled_step"] = ctx.step
            new_messages = messages
        elif self._recall_mode == "attmask":
            # v4 Phase 4e: keep memento + markers intact (so prefix cache
            # hits across turns), but stage the obs_text for the runner
            # to push into the engine's recall queue. On next chat()'s
            # compaction the engine will SKIP adding this obs to the
            # request's masked_block_ids — attention reads the obs from
            # already-pinned KV. No re-prefill cost.
            obs = msg.get("content") or ""
            if obs:
                self._pending_attmask_recalls.append(obs)
            msg["recalled_step"] = ctx.step
            new_messages = messages
        elif self._recall_mode == "kvrestore":
            # Phase 3c + 9: clear memento so the renderer puts the full obs
            # back at original position. Stage (obs_text, delta_tokens) for
            # the runner. delta_tokens = m_obs - p_memento_placeholder; this
            # is the suffix shift the rotation kernel must correct so RoPE
            # phase matches the new logical positions (no re-prefill).
            #
            # Phase 9: the engine captures asynchronously w.r.t. the policy
            # (one obs per chat in markers-always-on mode). If we recall
            # an obs the engine hasn't captured yet, the splice fails AND
            # we've cleared msg.memento — leaving the msg neither memento'd
            # nor restorable. Check captured-obs set up front; if not yet
            # captured, leave the memento intact and skip this recall.
            obs = msg.get("content") or ""
            memento_text = msg.get("memento") or ""
            if obs:
                # Compute obs_id using the MODEL's tokenizer (set by runner
                # via _obs_id_for_text, defaults to Qwen3 for kvrestore).
                # ctx.tokenizer is the budget-counting tokenizer (often
                # tiktoken cl100k_base) and produces different token IDs for
                # `<tool_response>` etc. — wrong hash, recall never matches.
                try:
                    from vllm.v1.core.block_masking.memento_store import (
                        read_captured_obs_ids,
                    )
                    obs_id_fn = getattr(self, "_obs_id_for_text", None)
                    if callable(obs_id_fn):
                        expected_obs_id = obs_id_fn(obs)
                        obs_token_ids = []  # not needed for diag below
                    else:
                        # Fallback: budget tokenizer (works only if it
                        # matches the model's tokenizer, which it usually
                        # doesn't).
                        from vllm.v1.core.block_masking import compute_obs_id
                        wrapped = f"<tool_response>\n{obs}\n</tool_response>"
                        obs_token_ids = ctx.tokenizer.encode(wrapped)
                        expected_obs_id = (
                            compute_obs_id(obs_token_ids) if obs_token_ids else None
                        )
                    captured = read_captured_obs_ids()
                except Exception:
                    expected_obs_id = None
                    obs_token_ids = []
                    captured = set()
                if expected_obs_id and expected_obs_id not in captured:
                    # Engine hasn't captured this obs yet; skip recall to
                    # avoid losing the memento. Try again next step.
                    print(f"[policy-recall] step={ctx.step} SKIP: "
                          f"target obs_id={expected_obs_id} not yet captured "
                          f"(captured={sorted(captured)[:5]}{'…' if len(captured)>5 else ''}) "
                          f"obs_token_len={len(obs_token_ids) if obs_token_ids else 0} "
                          f"obs_first3={obs_token_ids[:3] if obs_token_ids else []} "
                          f"obs_last3={obs_token_ids[-3:] if obs_token_ids else []}",
                          flush=True)
                    return messages, None
                m_obs = ctx.tokenizer.count(obs)
                p_placeholder = ctx.tokenizer.count(memento_text)
                delta = m_obs - p_placeholder
                print(f"[policy-recall] step={ctx.step} FIRING: "
                      f"obs_id={expected_obs_id} delta={delta} "
                      f"m_obs={m_obs} p_placeholder={p_placeholder}",
                      flush=True)
                self._pending_kvrestore_recalls.append((obs, delta))
            msg["prior_memento"] = msg.get("memento")
            msg["memento"] = None
            msg["recalled_step"] = ctx.step
            new_messages = messages
        elif self._recall_mode == "drop":
            # Phase 7: un-drop the obs so the next render shows it again at
            # its original chronological position. The obs's KV blocks are
            # still pinned in the block pool from the chat that originally
            # captured them; vLLM's content-hash prefix cache should match
            # them when the obs tokens reappear in the prompt → no
            # re-prefill of the obs. Suffix tokens stay at their compacted
            # positions in the cache; on this chat they end up at original-
            # layout positions → small RoPE-phase mismatch (the "slight
            # correctness" tradeoff we're accepting).
            msg["_obs_dropped"] = False
            msg["recalled_step"] = ctx.step
            new_messages = messages
        else:
            # append mode: leave the original message mementoed; push the
            # recalled obs as a synthetic user message at the tail. The
            # original `messages` list is preserved (we return a NEW list so
            # callers that retained the input don't see the addendum).
            obs = msg.get("content") or ""
            originally_step = msg.get("step_added")
            tag = f"obs_id={target_idx}"
            if originally_step is not None:
                tag += f", originally_step={originally_step}"
            addendum = {
                "role": "user",
                "content": f"[recalled, {tag}]\n{obs}",
                "_recall_marker": True,
                "_recall_target_idx": target_idx,
            }
            msg["recalled_step"] = ctx.step
            new_messages = list(messages) + [addendum]

        wall_ms = int((time.perf_counter() - t0) * 1000)
        evt = CompactionEvent(
            step=ctx.step,
            policy=f"{self.name}.recall.{self._recall_mode}",
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=0,
            tokens_after=0,
            compaction_input_cached_tokens=0,
            compaction_input_uncached_tokens=0,
            compaction_output_tokens=0,
            compaction_call_tokens=0,
            wallclock_ms=wall_ms,
        )
        return new_messages, evt


    def get_recall_tool(self, queue_recall_fn: Callable[[str], Optional[str]]) -> Optional[Tool]:
        """Phase 5: build a `recall(memento_id)` Tool the model can call.

        The handler looks up the obs text by memento_id in `_recall_table`
        and forwards it to `queue_recall_fn` (the model adapter's
        `queue_recall`), which writes the obs hash into the engine's IPC
        queue so the next compaction skips masking it.

        Returns None when recall_tool_enabled=False so callers can wire
        this up unconditionally.
        """
        if not self._recall_tool_enabled:
            return None

        def _handle(args: Dict[str, Any]) -> str:
            mem_id = args.get("memento_id") or args.get("id") or ""
            if not isinstance(mem_id, str) or not mem_id:
                return "[recall error] memento_id is required (e.g. 'mem-3')"
            obs_text = self._recall_table.get(mem_id)
            if obs_text is None:
                known = sorted(self._recall_table.keys())
                return (f"[recall error] no memento with id={mem_id!r}. "
                        f"Known: {known[-10:] if known else 'none yet'}")
            try:
                queue_recall_fn(obs_text)
            except Exception as e:
                return f"[recall error] queue_recall failed: {type(e).__name__}: {e}"
            return (f"OK: queued recall for {mem_id}. The full original observation "
                    f"will be visible again on the next assistant turn.")

        return Tool(
            name="recall",
            description=(
                "Bring back the full text of a previously summarized observation. "
                "Earlier tool messages may have been replaced by a short memento "
                "summary tagged `[memento_id=mem-N]`. Calling recall(memento_id=\"mem-N\") "
                "asks the cache layer to make the original observation visible again "
                "to the model on the NEXT assistant turn — no re-fetch needed. Use "
                "this when the summary isn't enough and you need exact details."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memento_id": {
                        "type": "string",
                        "description": "The mem-N id from a `[memento_id=mem-N]` tag in a memento."
                    }
                },
                "required": ["memento_id"],
            },
            fn=_handle,
        )

    def get_system_prompt_addendum(self) -> Optional[str]:
        """Phase 5: extra system-prompt text the runner should append when
        the recall tool is exposed. Tells the model the convention so it
        can use the tool effectively without a one-shot example."""
        if not self._recall_tool_enabled:
            return None
        return (
            "Memory note: when context fills up, older tool observations may "
            "be auto-summarized. A summarized observation looks like:\n"
            "  [tool_response, evicted, memento]\n"
            "  [memento_id=mem-3]\n"
            "  <short summary>\n"
            "If you later need the EXACT contents of that observation (e.g. "
            "specific code, error text, file lines), call the `recall` tool "
            "with that memento_id. The full original observation will reappear "
            "on your next turn at near-zero cost — much cheaper than re-running "
            "the original tool. Don't recall pre-emptively; only when the "
            "summary alone is genuinely insufficient."
        )


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


def _compact_paired_asst(
    messages: List[Dict[str, Any]], tool_msg_idx: int
) -> None:
    """Phase 6: collapse the assistant turn that issued the tool call whose
    obs we just memento'd. Replace bulky reasoning text + tool_calls JSON
    with a tiny `[step compacted: tool=name(args_brief)]` ref. Idempotent —
    skips messages already marked `_step_compacted`.

    The matching asst is found by scanning backwards for an assistant
    message with a tool_call whose `id` matches the tool message's
    `tool_call_id`. If found, we keep `tool_calls` (chat templates need it
    to maintain the call → response linkage when rendering Qwen3-style
    `<tool_call>...</tool_call>`) but strip its `arguments` and reasoning
    text down to a brief ref. The bulk content (often 50-300 tokens) goes
    away; the structural pairing stays intact.
    """
    import json as _json
    target_id = messages[tool_msg_idx].get("tool_call_id")
    for j in range(tool_msg_idx - 1, -1, -1):
        m = messages[j]
        if m.get("role") != "assistant":
            continue
        tcs = m.get("tool_calls") or []
        match = None
        for tc in tcs:
            if tc.get("id") == target_id:
                match = tc
                break
        if match is None:
            continue
        if m.get("_step_compacted"):
            return
        fn = match.get("function") or {}
        tool_name = fn.get("name", "unknown")
        # Brief args ref: keep keys + truncated values so the agent can
        # remember "I called read_file on path=foo.py" without paying for
        # the full args. For most agent tools args are small anyway.
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = _json.loads(raw_args)
            except Exception:
                args = {"_raw": raw_args[:80]}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        brief = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                 for k, v in args.items()}
        m["content"] = f"[step compacted: {tool_name}({_json.dumps(brief, ensure_ascii=False)})]"
        # Replace this tool_call's arguments with the brief, preserve id+name
        # so the chat template still renders a coherent <tool_call>.
        match["function"] = {
            "name": tool_name,
            "arguments": _json.dumps(brief, ensure_ascii=False),
        }
        m["_step_compacted"] = True
        return
