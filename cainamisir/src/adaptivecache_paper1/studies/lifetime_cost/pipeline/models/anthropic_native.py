"""Anthropic native adapter — supports agentic tool use.

Anthropic's prompt-cache breakpoints (`cache_control` markers, up to 4 per
request) and explicit cached-tokens reporting (`cache_read_input_tokens`)
make it the cleanest provider for the lifetime-cost study. This adapter
converts our OpenAI-shape message dicts (the format the runner appends) to
Anthropic's content-block shape, including:

  - assistant messages with `tool_calls`  → text + `tool_use` blocks
  - `role: tool` results                  → `tool_result` blocks within a
                                            single user message (Anthropic
                                            requires alternating roles, so
                                            consecutive tool replies are
                                            merged into one user turn)
  - tools (OpenAI shape)                  → Anthropic shape
                                            (`name` / `description` /
                                            `input_schema`)

cache_control placement: by default we put one `ephemeral` breakpoint on
the system prompt (so the system + tool defs are cached) and another at
`cache_breakpoint_after` if the policy specifies one. Anthropic counts
this against the 4-per-request limit.

Usage reporting: we expose `cache_read_input_tokens` as `cached_tokens`
and `cache_creation_input_tokens` as `cache_write_tokens`. `prompt_tokens`
is the SUM of fresh + cached + cache-write so it matches what the user
would see on a per-request invoice.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from ..types import Usage
from .base import ChatModel, ChatResponse


class AnthropicNative(ChatModel):
    def __init__(
        self,
        model_name: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        **kwargs,
    ):
        super().__init__(model_name=model_name, **kwargs)
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=timeout,
            max_retries=max_retries,
        )
        # Strip the "anthropic/" provider prefix
        self._anth_model = model_name.split("/", 1)[-1]

    # ------------------------------------------------------------------
    # Format conversions
    # ------------------------------------------------------------------

    @staticmethod
    def _split_system(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Pull leading system messages out of the message list. Anthropic
        passes them as a separate `system` parameter."""
        sys_parts = []
        rest = []
        seen_non_system = False
        for m in messages:
            if m.get("role") == "system" and not seen_non_system:
                sys_parts.append(m.get("content") or "")
            else:
                seen_non_system = True
                rest.append(m)
        return ("\n\n".join(sys_parts) if sys_parts else None), rest

    @staticmethod
    def _convert_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """OpenAI tool spec → Anthropic tool spec. The runner passes:
            {"type": "function", "function": {"name", "description", "parameters"}}
        Anthropic wants:
            {"name", "description", "input_schema"}
        """
        if not tools:
            return None
        out = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) and "function" in t else t
            if not fn:
                continue
            out.append({
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

    def _convert_messages(
        self, rest: List[Dict[str, Any]], cache_breakpoint_after: Optional[int]
    ) -> List[Dict[str, Any]]:
        """OpenAI-shape messages → Anthropic-shape messages.

        Key conversions:
          - assistant with tool_calls: text block + tool_use blocks
          - tool messages: collected into the next user message as tool_result
            blocks (Anthropic requires alternating roles)
        """
        anth_msgs: List[Dict[str, Any]] = []
        i = 0
        while i < len(rest):
            m = rest[i]
            role = m.get("role")

            if role == "assistant":
                blocks: List[Dict[str, Any]] = []
                content = m.get("content") or ""
                if isinstance(content, str) and content.strip():
                    blocks.append({"type": "text", "text": content})
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        # Some adapters store stringified JSON
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {"_raw": args}
                    if not isinstance(args, dict):
                        args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or f"toolu_{i}",
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                anth_msgs.append({"role": "assistant", "content": blocks})
                i += 1
                continue

            if role == "tool":
                # Coalesce a run of consecutive tool messages into one user
                # message with multiple tool_result blocks. Anthropic
                # requires roles to alternate; multiple `tool` messages map
                # to ONE user turn.
                tr_blocks: List[Dict[str, Any]] = []
                while i < len(rest) and rest[i].get("role") == "tool":
                    tm = rest[i]
                    tcid = tm.get("tool_call_id") or f"toolu_unk_{i}"
                    content = tm.get("content") or ""
                    if not isinstance(content, str):
                        content = json.dumps(content)
                    tr_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tcid,
                        "content": content,
                    })
                    i += 1
                anth_msgs.append({"role": "user", "content": tr_blocks})
                continue

            # role == "user" (or fallback)
            content = m.get("content") or ""
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = content
            anth_msgs.append({"role": "user", "content": blocks})
            i += 1

        # Insert cache_control breakpoint on the cache_breakpoint_after-th
        # message in the converted list. Note: index here refers to the
        # *converted* list (not the original), since the count differs after
        # tool-result coalescing.
        if cache_breakpoint_after is not None and 0 <= cache_breakpoint_after < len(anth_msgs):
            target = anth_msgs[cache_breakpoint_after]
            blocks = list(target["content"])
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            anth_msgs[cache_breakpoint_after] = {**target, "content": blocks}

        return anth_msgs

    # ------------------------------------------------------------------
    # chat()
    # ------------------------------------------------------------------

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

        anth_msgs = self._convert_messages(rest, cache_breakpoint_after)
        anth_tools = self._convert_tools(tools)

        # System with cache_control on it (very common pattern — the system
        # prompt is the most stable cacheable region).
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
        if anth_tools is not None:
            kwargs["tools"] = anth_tools

        try:
            resp = self._client.messages.create(**kwargs)
        except self._anthropic.BadRequestError as e:
            # Surface the API-side message (often diagnostic about
            # malformed tool_use/tool_result pairing or context overflow)
            raise RuntimeError(f"Anthropic BadRequest: {e}") from e

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
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        fresh = getattr(u, "input_tokens", 0) or 0
        return ChatResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls or None,
            usage=Usage(
                prompt_tokens=fresh + cached + cache_write,
                completion_tokens=getattr(u, "output_tokens", 0) or 0,
                cached_tokens=cached,
                cache_write_tokens=cache_write,
            ),
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
