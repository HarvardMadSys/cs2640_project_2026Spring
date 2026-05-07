"""vLLM-backed local chat model — same ChatModel interface as HFLocalModel,
but uses vLLM for ~5x faster generation via paged attention + automatic
prefix caching + CUDA graph capture.

The vLLM engine is expensive to construct (~80s) but stateless across
requests, so we cache one engine per (model_name, dtype, gpu_memory_util,
max_model_len) tuple at the class level. The harness rebuilds a model
adapter per task, but those rebuilds reuse the cached engine.

Tool-call parsing matches HFLocalModel: Hermes-format
`<tool_call>{...}</tool_call>` (which Qwen3 emits when `tools` is passed
to the chat template).

cached_tokens estimation: byte-prefix common length with the previous
call (same as HFLocalModel) — deliberately consistent so analysis code
sees the same cache-hit semantics across both adapters.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ..types import Usage
from .base import ChatModel, ChatResponse


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


class VLLMLocalModel(ChatModel):
    """Drives a HuggingFace causal LM through vLLM. Single engine, single GPU."""

    _engine_cache: Dict[Tuple, Any] = {}
    _tokenizer_cache: Dict[str, Any] = {}

    def __init__(
        self,
        model_name: str,
        *,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
        dtype: str = "bfloat16",
        enable_thinking: bool = False,
        gpu_memory_utilization: float = 0.85,
        max_model_len: int = 16000,
        enable_prefix_caching: bool = True,
        # accepted for HFLocalModel compatibility, ignored here
        device: Optional[str] = None,
        attn_implementation: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        self._enable_thinking = enable_thinking
        self._default_max_new_tokens = max_new_tokens
        self._default_temperature = temperature

        if model_name not in VLLMLocalModel._tokenizer_cache:
            from transformers import AutoTokenizer
            VLLMLocalModel._tokenizer_cache[model_name] = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=True
            )
        self._tokenizer = VLLMLocalModel._tokenizer_cache[model_name]

        cache_key = (model_name, dtype, gpu_memory_utilization, max_model_len, enable_prefix_caching)
        if cache_key not in VLLMLocalModel._engine_cache:
            os.environ.setdefault("VLLM_USE_V1", "0")
            os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
            from vllm import LLM
            VLLMLocalModel._engine_cache[cache_key] = LLM(
                model=model_name,
                dtype=dtype,
                gpu_memory_utilization=gpu_memory_utilization,
                max_model_len=max_model_len,
                enable_prefix_caching=enable_prefix_caching,
                trust_remote_code=True,
            )
        self._llm = VLLMLocalModel._engine_cache[cache_key]
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
    ) -> ChatResponse:
        from vllm import SamplingParams

        msgs = list(messages)
        if system is not None:
            msgs = [{"role": "system", "content": system}] + msgs

        kwargs = {"add_generation_prompt": True, "tokenize": False, "enable_thinking": self._enable_thinking}
        if tools:
            kwargs["tools"] = tools
        try:
            rendered = self._tokenizer.apply_chat_template(msgs, **kwargs)
        except TypeError:
            kwargs.pop("enable_thinking", None)
            rendered = self._tokenizer.apply_chat_template(msgs, **kwargs)

        # byte-prefix simulation of cached_tokens (matches HFLocalModel)
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
            # Most likely: prompt > max_model_len. Surface a sentinel completion
            # so the runner can end the task gracefully (treats as final answer)
            # instead of crashing the whole matrix. Tracked via `_overflow` flag
            # in the response usage so analysis can count overflow events.
            if "longer than the maximum" not in str(e):
                raise
            self._prev_rendered = rendered  # don't poison the byte-prefix sim
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

        tool_calls = []
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
