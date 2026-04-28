"""ChatModel interface — provider-agnostic chat-completion call."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..types import Usage


@dataclass
class ChatResponse:
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Usage = None
    raw: Optional[Dict[str, Any]] = None


class ChatModel(abc.ABC):
    """Provider-agnostic chat-completion model."""

    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        self._kwargs = kwargs

    @abc.abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,        # for Anthropic which separates system
        cache_breakpoint_after: Optional[int] = None,  # message index to mark cacheable
    ) -> ChatResponse:
        """Run one chat completion. Returns content + normalized usage."""
        ...
