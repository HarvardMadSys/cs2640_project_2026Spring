"""Local HuggingFace transformers chat model — no HTTP server, no vLLM.

Wraps a transformers AutoModelForCausalLM in our ChatModel interface so the
agent runner can drive it directly. Used for cluster-local Phase A
shake-down before any production serving setup.

Tool-call parsing follows the Hermes format that Qwen3 emits with the
`tools` argument to its chat template:
  <tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>

Usage estimation: prompt_tokens = exact tokenizer count of the rendered
chat template; cached_tokens = byte-prefix common length with the previous
call (so the cliff metric works without a real prefix cache backend).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import torch

from ..types import Usage
from .base import ChatModel, ChatResponse


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


class HFLocalModel(ChatModel):
    """Drives a HuggingFace causal LM in-process. Single GPU, eager generation.

    cached_tokens is computed by byte-prefix-matching the rendered chat
    template against the previous call's render. This simulates a perfect
    prefix cache (vLLM's automatic behavior) without an actual cache
    backend, which is what we want for Phase A: the cliff metric should
    work the same whether or not a real cache is in play.
    """

    def __init__(
        self,
        model_name: str,
        *,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
        device: Optional[str] = None,
        dtype: torch.dtype = torch.float16,
        attn_implementation: str = "sdpa",
        enable_thinking: bool = False,    # Qwen3 default is True; agent loops need direct action
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        self._enable_thinking = enable_thinking
        from transformers import AutoConfig, AutoTokenizer, AutoModelForCausalLM
        if isinstance(dtype, str):
            dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16,
                     "float32": torch.float32, "auto": "auto"}[dtype]
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)

        # Multimodal hybrid models (Qwen3.5/3.6) ship as ForConditionalGeneration
        # with a vision tower. We want text-only inference: use the *language*
        # submodel via AutoModelForImageTextToText and pull `.language_model`,
        # which exposes a clean causal LM head and skips the vision encoder.
        cfg = AutoConfig.from_pretrained(model_name, trust_remote_code=True)
        is_vl = getattr(cfg, "vision_config", None) is not None or "ConditionalGeneration" in (cfg.architectures or [""])[0]
        if is_vl:
            from transformers import AutoModelForImageTextToText
            full = AutoModelForImageTextToText.from_pretrained(
                model_name,
                torch_dtype=dtype,
                attn_implementation=attn_implementation,
                trust_remote_code=True,
            )
            # Drop the vision tower to free memory; keep the LM head + text backbone.
            for attr in ("vision_model", "vision_tower", "visual"):
                if hasattr(full, attr):
                    delattr(full, attr)
            self._model = full.to(self._device).eval()
            self._is_vl = True
        else:
            self._model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=dtype,
                attn_implementation=attn_implementation,
                trust_remote_code=True,
            ).to(self._device).eval()
            self._is_vl = False
        self._default_max_new_tokens = max_new_tokens
        self._default_temperature = temperature
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
        msgs = list(messages)
        if system is not None:
            msgs = [{"role": "system", "content": system}] + msgs

        kwargs = {"add_generation_prompt": True, "tokenize": False, "enable_thinking": self._enable_thinking}
        if tools:
            kwargs["tools"] = tools
        try:
            rendered = self._tokenizer.apply_chat_template(msgs, **kwargs)
        except TypeError:
            # Older tokenizers don't accept enable_thinking
            kwargs.pop("enable_thinking", None)
            rendered = self._tokenizer.apply_chat_template(msgs, **kwargs)

        # Cached vs uncached estimate: byte-prefix vs previous call
        n = min(len(self._prev_rendered), len(rendered))
        i = 0
        while i < n and self._prev_rendered[i] == rendered[i]:
            i += 1
        prefix_chars = i

        ids = self._tokenizer(rendered, return_tensors="pt", add_special_tokens=False).input_ids.to(self._device)
        prompt_tokens = int(ids.shape[1])
        cached_tokens = int(prompt_tokens * (prefix_chars / max(len(rendered), 1)))

        gen_kwargs: Dict[str, Any] = dict(
            max_new_tokens=max_tokens or self._default_max_new_tokens,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        eff_temp = temperature if temperature is not None else self._default_temperature
        if eff_temp and eff_temp > 0:
            gen_kwargs["do_sample"] = True
            gen_kwargs["temperature"] = eff_temp
        else:
            gen_kwargs["do_sample"] = False

        with torch.no_grad():
            out = self._model.generate(ids, **gen_kwargs)
        new_ids = out[0, ids.shape[1]:]
        completion_text = self._tokenizer.decode(new_ids, skip_special_tokens=True)
        completion_tokens = int(new_ids.shape[0])

        # Parse Hermes-style tool calls from the assistant content
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
