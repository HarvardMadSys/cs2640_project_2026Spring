"""Anthropic native adapter.

Anthropic has explicit prompt-cache breakpoints (`cache_control` markers,
up to 4 per request) and reports cache reads/writes separately:
  - usage.cache_read_input_tokens   → our Usage.cached_tokens
  - usage.cache_creation_input_tokens → our Usage.cache_write_tokens

This adapter places a cache_control breakpoint after the index specified
by `cache_breakpoint_after`, which the runner sets to the end of the
prefix-preserving frozen prefix. That's the entire mechanism by which our
prefix_preserving policy actually wins on Anthropic.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..types import Usage
from .base import ChatModel, ChatResponse


class AnthropicNative(ChatModel):
    def __init__(
        self,
        model_name: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        import anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=timeout,
        )
        # Strip the "anthropic/" provider prefix
        self._anth_model = model_name.split("/", 1)[-1]

    @staticmethod
    def _split_system(messages: List[Dict[str, Any]]) -> tuple:
        """Anthropic separates system from messages. Pull leading system."""
        sys_parts = []
        rest = []
        for m in messages:
            if m.get("role") == "system" and not rest:
                sys_parts.append(m.get("content") or "")
            else:
                rest.append(m)
        return "\n\n".join(sys_parts) if sys_parts else None, rest

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
        sys_text, rest = self._split_system(messages)
        if system is not None:
            sys_text = (sys_text + "\n\n" + system) if sys_text else system

        # Convert OpenAI-shape messages to Anthropic-shape and inject
        # cache_control on the message at cache_breakpoint_after (0-indexed
        # within `rest`). Anthropic accepts cache_control on message content
        # blocks.
        anth_msgs: List[Dict[str, Any]] = []
        for i, m in enumerate(rest):
            content = m.get("content") or ""
            blocks: List[Dict[str, Any]]
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = content  # already structured
            if cache_breakpoint_after is not None and i == cache_breakpoint_after:
                # Add cache_control to the LAST block of this message
                blocks = list(blocks)
                blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            anth_msgs.append({"role": "assistant" if m["role"] == "assistant" else "user", "content": blocks})

        # System with cache_control on the system itself (very common pattern)
        sys_param: Any = None
        if sys_text:
            sys_param = [{
                "type": "text",
                "text": sys_text,
                "cache_control": {"type": "ephemeral"},
            }]

        kwargs: Dict[str, Any] = dict(
            model=self._anth_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=anth_msgs,
        )
        if sys_param is not None:
            kwargs["system"] = sys_param
        if tools:
            kwargs["tools"] = tools

        resp = self._client.messages.create(**kwargs)
        # Concatenate text blocks; collect tool_use blocks
        text_parts = []
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": block.input},
                })

        u = resp.usage
        return ChatResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls or None,
            usage=Usage(
                prompt_tokens=getattr(u, "input_tokens", 0)
                              + getattr(u, "cache_read_input_tokens", 0)
                              + getattr(u, "cache_creation_input_tokens", 0),
                completion_tokens=getattr(u, "output_tokens", 0),
                cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
