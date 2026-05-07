"""vLLM 0.13.0 + Memento overlay adapter.

Drives a self-hosted Qwen3-style model through vLLM with Memento's block
masking enabled. Tool messages whose `memento` field is populated are
rendered as `<tool_response>obs</tool_response><|fim_prefix|>memento<|fim_middle|>`
so the engine masks the obs from KV after summary_end is consumed.

Block-token mapping (no tokenizer modification — these IDs already exist
in Qwen3's vocab):
    block_start    = <tool_response>  (151665)  natural fit for tool obs
    block_end      = </tool_response> (151666)  natural fit
    summary_start  = <|fim_prefix|>   (151659)  repurposed (FIM unused in chat)
    summary_end    = <|fim_middle|>   (151660)  repurposed

Required env: VLLM_ATTENTION_BACKEND=FLASHINFER (Blackwell + CUDA 12.8;
default FlashAttention-2 .so requires CUDA >=13).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# Defer pipeline imports so this module remains importable in standalone tests.
try:
    from ...pipeline.types import Usage
    from ...pipeline.models.base import ChatModel, ChatResponse
except Exception:  # pragma: no cover
    Usage = None  # type: ignore
    ChatModel = object  # type: ignore
    ChatResponse = None  # type: ignore


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

# Qwen3 reserved-token IDs we drive the masking overlay with.
BLOCK_START_ID = 151665   # <tool_response>
BLOCK_END_ID = 151666     # </tool_response>
SUMMARY_START_ID = 151659  # <|fim_prefix|>
SUMMARY_END_ID = 151660    # <|fim_middle|>

SUMMARY_START_STR = "<|fim_prefix|>"
SUMMARY_END_STR = "<|fim_middle|>"


def wrap_tool_message_for_masking(
    obs: str, memento: Optional[str], *, always_wrap: bool = False
) -> str:
    """Render tool obs + optional memento as a single user-role payload.

    With memento: full block + summary markers. Engine sees block_start..
    block_end..summary_start..summary_end and fires compaction.

    Without memento: by default plain text with no special tokens (so the
    engine doesn't track phantom blocks in non-masking modes). Pass
    `always_wrap=True` for v4 attention_mask_mode runs — the markers stay
    around the obs from the start, and adding a memento later only appends
    `<|fim_prefix|>{memento}<|fim_middle|>` to the suffix instead of
    transforming the message's whole structure. That reshape is the source
    of the prefix-cache cliff (Phase 6 smoke showed 99% → 28% hit at the
    chat after the first compaction); keeping markers always avoids it.
    """
    if memento:
        return (
            f"<tool_response>\n{obs}\n</tool_response>"
            f"{SUMMARY_START_STR}{memento}{SUMMARY_END_STR}"
        )
    if always_wrap:
        # Markers present from the start; engine sees the block boundary.
        # No summary markers yet → engine doesn't compact this turn (it
        # waits for `<|fim_middle|>` to fire). Position-stable across the
        # eventual transition to mementoed.
        return f"<tool_response>\n{obs}\n</tool_response>"
    # No memento + no always_wrap → plain text, no special tokens.
    return f"[tool_response]\n{obs}"


def wrap_tool_message_inlined(obs: str, memento: Optional[str]) -> str:
    """Render an older tool message as plain text — never with markers.

    Older turns under last_only_masking should never re-trigger the
    masking processor. Emit memento text if available, else fall back to
    the obs itself, but in either case use plain text with no special
    tokens (no `<tool_response>` literal, no summary delimiters).
    """
    if memento:
        return f"[tool_response, evicted, memento]\n{memento}"
    return f"[tool_response]\n{obs}"


def transform_messages(
    messages: List[Dict[str, Any]],
    *,
    last_only_masking: bool = True,
) -> List[Dict[str, Any]]:
    """Replace each tool message with a user message ready for the engine.

    With `last_only_masking=True` (default), only the LAST tool message
    in the conversation gets block + summary markers; earlier ones are
    inlined as plain text (memento-only when available). This matches the
    measured-good multi-turn recipe: each chat() call fires at most one
    compaction → no cascading rewinds → ~12% overhead vs ~395%.

    With `last_only_masking=False`, every tool message that has a memento
    field gets full markers, EXCEPT messages flagged `_obs_dropped` which
    render as memento-only (no obs). This is the Phase 7 drop-and-restore
    path: a freshly-mementoed tool gets full markers + obs for one chat
    (so the engine captures and pins the obs's KV blocks), then the policy
    flips `_obs_dropped=True` so subsequent chats render only the memento
    (no carry cost). On recall, the policy clears `_obs_dropped`; the obs
    re-appears at its original position and vLLM's content-hash prefix
    cache finds the pinned blocks → no re-prefill. Suffix's K vectors
    were baked at compacted positions and end up reused at original-layout
    positions — slight RoPE-phase mismatch, accepted.
    """
    last_tool_idx = -1
    if last_only_masking:
        for i, m in enumerate(messages):
            if m.get("role") == "tool":
                last_tool_idx = i

    out: List[Dict[str, Any]] = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            out.append(m)
            continue
        obs = m.get("content", "")
        memento = m.get("memento")
        if m.get("_obs_dropped") and memento:
            # Phase 7: stale memento — drop the obs, render summary only.
            # The obs's KV blocks are still pinned in the block pool from
            # the chat that originally captured them.
            body = wrap_tool_message_inlined(obs=obs, memento=memento)
        elif last_only_masking and i != last_tool_idx:
            body = wrap_tool_message_inlined(obs=obs, memento=memento)
        else:
            # always_wrap=True keeps `<tool_response>...</tool_response>`
            # markers from the start. When the eager-at-suffix policy then
            # memento's the obs the very next turn, the rendering only
            # APPENDS `<|fim_prefix|>memento<|fim_middle|>` — chain hash
            # up through `</tool_response>` is identical, so the next
            # request's prefix-cache walk hits everything before the
            # appendix.
            body = wrap_tool_message_for_masking(
                obs=obs, memento=memento, always_wrap=True
            )
        out.append({"role": "user", "content": body})
    return out


class MementoVLLMModel(ChatModel):
    """ChatModel backed by vLLM 0.13.0 with Memento block masking."""

    _engine_cache: Dict[Tuple, Any] = {}
    _tokenizer_cache: Dict[str, Any] = {}

    def compute_obs_id_for_text(self, obs_text: str) -> Optional[str]:
        """Compute the engine-side obs_id for a given obs_text.

        Phase 9: the policy needs this to gate recalls on whether the
        engine has captured the obs (file IPC on /tmp/paper2_captured_obs.jsonl).
        Crucially this MUST use the same tokenizer the engine uses (Qwen3),
        NOT the budget-counting tokenizer (often tiktoken cl100k_base) —
        the hashes won't match otherwise. This method wraps + tokenizes +
        hashes the same way `queue_kv_restore` does.
        """
        try:
            from vllm.v1.core.block_masking import compute_obs_id
        except Exception:
            return None
        wrapped = f"<tool_response>\n{obs_text}\n</tool_response>"
        token_ids = self._tokenizer(wrapped, add_special_tokens=False).input_ids
        if not token_ids:
            return None
        return compute_obs_id(token_ids)

    def queue_kv_restore(
        self, obs_text: str, rotate_delta: int = 0
    ) -> Optional[str]:
        """Phase 3c + 9: trigger CPU→GPU KV restore + block_table splice +
        suffix K rotation on the next chat's request.

        Hashes the obs (matching the engine's compute_obs_id), writes the
        memento_id and rotate_delta to the kv_restore queue file. The
        scheduler picks it up at request-add time and calls its own
        queue_kv_restore(request_id, memento_id, rotate_delta) to:
          1. Allocate fresh GPU blocks + schedule worker CPU→GPU copy.
          2. Splice obs blocks at original logical position.
          3. Queue a worker KVRotateOp that rotates the suffix's cached
             K by rotate_delta tokens (RoPE composition) so phase matches
             the new logical positions — no re-prefill of suffix.

        rotate_delta=0 falls back to legacy Phase 3c behavior (no rotation;
        suffix RoPE is wrong by Δ tokens, accuracy degrades for nontrivial Δ).

        Returns the obs_id, or None on failure.
        """
        try:
            from vllm.v1.core.block_masking import compute_obs_id
            from vllm.v1.core.block_masking.memento_store import (
                queue_kv_restore_request,
                read_captured_obs_ids,
            )
        except Exception:
            return None
        wrapped = f"<tool_response>\n{obs_text}\n</tool_response>"
        token_ids = self._tokenizer(wrapped, add_special_tokens=False).input_ids
        if not token_ids:
            return None
        obs_id = compute_obs_id(token_ids)
        # Phase 9: only queue recall for obs the engine has already captured.
        # The engine appends to a shared file (announce_captured_obs) on
        # every successful stash. If the obs isn't there yet, the recall
        # would fail with 'unknown memento_id' anyway — skip it.
        captured = read_captured_obs_ids()
        if obs_id not in captured:
            print(f"[v3c-adapter-kv-restore-skip] obs_id={obs_id} not yet "
                  f"captured (store has {len(captured)} obs); skipping recall",
                  flush=True)
            return None
        print(f"[v3c-adapter-kv-restore] len={len(token_ids)} first={token_ids[:3]} "
              f"last={token_ids[-3:]} delta={rotate_delta} → {obs_id}",
              flush=True)
        queue_kv_restore_request(obs_id, rotate_delta=rotate_delta)
        return obs_id

    def queue_recall(self, obs_text: str) -> Optional[str]:
        """Phase 4e: tell the engine to skip masking the named obs on the
        next compaction. Returns the obs_id (so the caller can verify).

        Mirrors the engine's content-hash exactly. The renderer wraps
        the obs as `<tool_response>\\n{obs}\\n</tool_response>...` —
        the engine's compaction span is the entire `<tool_response>…
        </tool_response>` token range (inclusive of the markers; the
        `mask_delimiters=False` flag controls attention masking of the
        markers, not whether they appear in the captured span). We
        tokenize the same wrapper so adapter and engine produce the
        same content hash.

        Returns None if tokenization fails or imports are unavailable.
        """
        try:
            from vllm.v1.core.block_masking import compute_obs_id, queue_recall
        except Exception:
            return None
        # Match the engine's compaction span: include both delimiters.
        wrapped = f"<tool_response>\n{obs_text}\n</tool_response>"
        token_ids = self._tokenizer(wrapped, add_special_tokens=False).input_ids
        if not token_ids:
            return None
        obs_id = compute_obs_id(token_ids)
        # Phase 4e diag: log so we can compare to engine's [v4e-id] line.
        print(f"[v4e-adapter-id] len={len(token_ids)} first={token_ids[:3]} "
              f"last={token_ids[-3:]} → {obs_id}",
              flush=True)
        queue_recall(obs_id)
        return obs_id

    def __init__(
        self,
        model_name: str,
        *,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
        default_seed: Optional[int] = None,
        dtype: str = "bfloat16",
        gpu_memory_utilization: float = 0.85,
        max_model_len: int = 16000,
        enable_prefix_caching: bool = True,
        masking_enabled: bool = True,
        keep_last_n_blocks: int = 0,
        compact_on_summary_end: bool = True,
        restart_mode: bool = True,
        last_only_masking: bool = True,
        debug_masking: bool = False,
        auto_capture_mementos: bool = False,
        attention_mask_mode: bool = False,
        pin_captured_blocks: bool = True,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        self._default_max_new_tokens = max_new_tokens
        self._default_temperature = temperature
        self._default_seed = default_seed
        self._masking_enabled = masking_enabled
        self._last_only_masking = last_only_masking

        # v8: env-var gating for the Phase 4a refcount pin. The flag is
        # read inside the engine subprocess (single_type_kv_cache_manager)
        # at compaction time. Setting it here propagates to the worker
        # forks via the standard env inheritance.
        if not pin_captured_blocks:
            os.environ["PAPER2_NO_PIN"] = "1"
        else:
            os.environ.pop("PAPER2_NO_PIN", None)

        if model_name not in MementoVLLMModel._tokenizer_cache:
            from transformers import AutoTokenizer
            MementoVLLMModel._tokenizer_cache[model_name] = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=True
            )
        self._tokenizer = MementoVLLMModel._tokenizer_cache[model_name]

        # last_only_masking is a render-time concern; doesn't change the
        # engine config so it stays out of the cache_key.
        cache_key = (
            model_name, dtype, gpu_memory_utilization, max_model_len,
            enable_prefix_caching, masking_enabled, keep_last_n_blocks,
            compact_on_summary_end, restart_mode, auto_capture_mementos,
            attention_mask_mode, pin_captured_blocks,
        )
        if cache_key not in MementoVLLMModel._engine_cache:
            os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
            os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
            from vllm import LLM
            from vllm.config.block_masking import BlockMaskingConfig

            engine_kwargs: Dict[str, Any] = dict(
                model=model_name,
                dtype=dtype,
                gpu_memory_utilization=gpu_memory_utilization,
                max_model_len=max_model_len,
                enable_prefix_caching=enable_prefix_caching,
                trust_remote_code=True,
            )
            if masking_enabled:
                engine_kwargs["block_masking_config"] = BlockMaskingConfig(
                    enable=True,
                    keep_last_n_blocks=keep_last_n_blocks,
                    block_start_token=str(BLOCK_START_ID),
                    block_end_token=str(BLOCK_END_ID),
                    summary_start_token=str(SUMMARY_START_ID),
                    summary_end_token=str(SUMMARY_END_ID),
                    require_assistant_section=False,
                    mask_delimiters=False,  # Qwen3 style
                    compact_on_summary_end=compact_on_summary_end,
                    restart_mode=restart_mode,
                    auto_capture_mementos=auto_capture_mementos,
                    attention_mask_mode=attention_mask_mode,
                    debug=debug_masking,
                )
                # v8: pin_captured_blocks gating uses env var PAPER2_NO_PIN
                # (read inside the engine's compact path), so we don't need
                # to extend BlockMaskingConfig — that schema is vendored
                # into the Modal image via a static patch and would require
                # a rebuild to flip.
            MementoVLLMModel._engine_cache[cache_key] = LLM(**engine_kwargs)
        self._llm = MementoVLLMModel._engine_cache[cache_key]
        self._prev_rendered: str = ""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        cache_breakpoint_after: Optional[int] = None,
    ) -> "ChatResponse":
        from vllm import SamplingParams

        msgs = list(messages)
        if system is not None:
            msgs = [{"role": "system", "content": system}] + msgs
        msgs = transform_messages(msgs, last_only_masking=self._last_only_masking)

        kwargs: Dict[str, Any] = {"add_generation_prompt": True, "tokenize": False}
        if tools:
            kwargs["tools"] = tools
        rendered = self._tokenizer.apply_chat_template(msgs, **kwargs)

        # Byte-prefix simulation of cached_tokens — same heuristic as
        # vllm_local.py for cross-adapter analysis consistency.
        n = min(len(self._prev_rendered), len(rendered))
        i = 0
        while i < n and self._prev_rendered[i] == rendered[i]:
            i += 1
        prefix_chars = i

        prompt_token_ids = self._tokenizer(rendered, add_special_tokens=False).input_ids
        prompt_tokens = len(prompt_token_ids)
        cached_tokens = int(prompt_tokens * (prefix_chars / max(len(rendered), 1)))
        print(f"[adapter] chat() prompt_tokens={prompt_tokens} "
              f"cached~{cached_tokens} → vLLM.generate ENTER",
              flush=True)

        eff_temp = temperature if temperature is not None else self._default_temperature
        sp_kwargs: Dict[str, Any] = {
            "max_tokens": max_tokens or self._default_max_new_tokens,
            "temperature": eff_temp,
        }
        if self._default_seed is not None:
            sp_kwargs["seed"] = self._default_seed
        sp = SamplingParams(**sp_kwargs)
        import time as _time
        _gen_t0 = _time.perf_counter()
        try:
            outs = self._llm.generate(
                prompts=[{"prompt_token_ids": prompt_token_ids}],
                sampling_params=sp,
                use_tqdm=False,
            )
            print(f"[adapter] vLLM.generate EXIT in "
                  f"{(_time.perf_counter()-_gen_t0)*1000:.0f}ms",
                  flush=True)
        except ValueError as e:
            if "longer than the maximum" not in str(e):
                raise
            self._prev_rendered = rendered
            return ChatResponse(
                content="[context overflow: prompt exceeded model max_model_len; ending task]",
                tool_calls=None,
                usage=Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    cached_tokens=cached_tokens,
                ),
            )
        out = outs[0].outputs[0]
        completion_text = out.text
        completion_tokens = len(out.token_ids)

        tool_calls: List[Dict[str, Any]] = []
        for j, m in enumerate(TOOL_CALL_RE.finditer(completion_text)):
            try:
                d = json.loads(m.group(1))
                tool_calls.append({
                    "id": f"tc_{j}",
                    "type": "function",
                    "function": {
                        "name": d.get("name", ""),
                        "arguments": d.get("arguments", {}),
                    },
                })
            except json.JSONDecodeError:
                pass

        text = TOOL_CALL_RE.sub("", completion_text).strip()
        self._prev_rendered = rendered + completion_text

        return ChatResponse(
            content=text,
            tool_calls=tool_calls or None,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
            ),
        )
