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


def wrap_tool_message_for_masking(obs: str, memento: Optional[str]) -> str:
    """Render tool obs + optional memento as a single user-role payload.

    With memento: the engine sees block_end immediately followed by
    summary_start..summary_end and triggers compaction of the obs content.

    Without memento: standard `<tool_response>` wrapping; no compaction
    fires (no summary block).
    """
    body = f"<tool_response>\n{obs}\n</tool_response>"
    if memento:
        body += f"{SUMMARY_START_STR}{memento}{SUMMARY_END_STR}"
    return body


def wrap_tool_message_inlined(obs: str, memento: Optional[str]) -> str:
    """Render an older tool message as PLAIN text (no markers).

    For the last-only recipe: every earlier tool message in a multi-turn
    conversation gets rendered as a plain user message containing only the
    memento text (the obs is already evicted from KV by a prior turn's
    compaction, so there's no need to re-mark it). This avoids cascading
    re-compaction in the engine.

    Falls back to the obs itself if no memento was generated yet.
    """
    if memento:
        return f"[tool_response, evicted, memento]\n{memento}"
    return f"<tool_response>\n{obs}\n</tool_response>"


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
    field gets full markers. Useful for ablations and unit testing.
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
        if last_only_masking and i != last_tool_idx:
            body = wrap_tool_message_inlined(obs=obs, memento=memento)
        else:
            body = wrap_tool_message_for_masking(obs=obs, memento=memento)
        out.append({"role": "user", "content": body})
    return out


class MementoVLLMModel(ChatModel):
    """ChatModel backed by vLLM 0.13.0 with Memento block masking."""

    _engine_cache: Dict[Tuple, Any] = {}
    _tokenizer_cache: Dict[str, Any] = {}

    def __init__(
        self,
        model_name: str,
        *,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
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
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        self._default_max_new_tokens = max_new_tokens
        self._default_temperature = temperature
        self._masking_enabled = masking_enabled
        self._last_only_masking = last_only_masking

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
            compact_on_summary_end, restart_mode,
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
                    debug=debug_masking,
                )
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

        eff_temp = temperature if temperature is not None else self._default_temperature
        sp = SamplingParams(
            max_tokens=max_tokens or self._default_max_new_tokens,
            temperature=eff_temp,
        )
        try:
            outs = self._llm.generate(
                prompts=[{"prompt_token_ids": prompt_token_ids}],
                sampling_params=sp,
                use_tqdm=False,
            )
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
