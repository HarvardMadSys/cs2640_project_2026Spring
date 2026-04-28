"""OpenAI-compatible adapter.

Works for: OpenAI, vLLM (any model served via vLLM's OpenAI server,
including our serve_unified.py), Together.ai, OpenRouter, Anthropic via
Anthropic's OpenAI-compat endpoint, etc.

cached_tokens is read from `usage.prompt_tokens_details.cached_tokens`
when present (OpenAI, vLLM ≥0.8 with --enable-prompt-tokens-details).
For providers that don't expose it, cached_tokens=0 — which conservatively
overestimates uncached cost.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..types import Usage
from .base import ChatModel, ChatResponse


class OpenAICompatible(ChatModel):
    def __init__(
        self,
        model_name: str,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        served_model_name: Optional[str] = None,
        timeout: float = 120.0,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        from openai import OpenAI
        self._client = OpenAI(
            base_url=base_url or os.environ.get("LIFETIME_BASE_URL"),
            api_key=api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("LIFETIME_API_KEY") or "dummy",
            timeout=timeout,
        )
        # served_model_name is what the server expects in the `model` field;
        # may differ from our pricing-sheet name (e.g. served as "default")
        self._served = served_model_name or model_name.split("/", 1)[-1]

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        cache_breakpoint_after: Optional[int] = None,
    ) -> ChatResponse:
        msgs = list(messages)
        if system is not None:
            msgs = [{"role": "system", "content": system}] + msgs
        # OpenAI-compat APIs don't expose explicit cache breakpoints —
        # prefix matching is automatic. cache_breakpoint_after is ignored.

        kwargs: Dict[str, Any] = dict(
            model=self._served,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        cached = 0
        if resp.usage and getattr(resp.usage, "prompt_tokens_details", None):
            ptd = resp.usage.prompt_tokens_details
            cached = getattr(ptd, "cached_tokens", 0) or 0

        return ChatResponse(
            content=msg.content or "",
            tool_calls=[
                {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in (msg.tool_calls or [])
            ] if msg.tool_calls else None,
            usage=Usage(
                prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
                cached_tokens=cached,
            ),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
