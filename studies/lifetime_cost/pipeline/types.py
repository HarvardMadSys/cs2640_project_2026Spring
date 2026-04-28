"""Shared types for the lifetime-cost pipeline.

All types are JSON-serializable so trajectories can be persisted, replayed,
and compared across providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    """A single chat-completions-style message.

    `tool_calls` and `tool_call_id` follow the OpenAI schema. Anthropic
    messages are mapped onto this shape by the model adapter.

    `_msg_id` and `_token_count` are simulation-only metadata used by the
    identity-model cache estimator in `external_traces.simulate_policy`.
    Same `_msg_id` ⇒ byte-identical message from the cache's perspective.
    Real model adapters and the runner ignore these fields.
    """

    role: Role
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    _msg_id: Optional[str] = None
    _token_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"role": self.role, "content": self.content}
        if self.name is not None:
            d["name"] = self.name
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self._msg_id is not None:
            d["_msg_id"] = self._msg_id
        if self._token_count is not None:
            d["_token_count"] = self._token_count
        return d


@dataclass
class Usage:
    """Per-call usage. cached_tokens is the slice of prompt_tokens that hit
    the provider's prefix cache. completion_tokens is generated output."""

    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    cache_write_tokens: int = 0  # Anthropic-style explicit cache writes

    @property
    def uncached_prompt(self) -> int:
        return max(self.prompt_tokens - self.cached_tokens, 0)


@dataclass
class CompactionEvent:
    """Records a single compaction firing."""

    step: int                       # the step *after which* compaction ran
    policy: str
    msgs_before: int
    msgs_after: int
    tokens_before: int
    tokens_after: int

    # Summarizer LLM call cost (split so we can bill input-as-cached and output
    # at the output rate, since the messages-to-summarize sit in the agent's
    # prefix cache from the just-completed step).
    compaction_input_cached_tokens: int = 0    # the prefix that hits cache
    compaction_input_uncached_tokens: int = 0  # the "now summarize" instruction
    compaction_output_tokens: int = 0          # the summary itself
    compaction_call_tokens: int = 0            # legacy total; deprecated
    wallclock_ms: int = 0


@dataclass
class Step:
    """One LLM call inside an agent trajectory."""

    index: int
    messages_in: List[Message]      # what was sent to the model
    response: Message               # what came back
    usage: Usage
    wallclock_ms: int = 0
    compaction_after: Optional[CompactionEvent] = None  # if compaction fired right after

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "messages_in": [m.to_dict() for m in self.messages_in],
            "response": self.response.to_dict(),
            "usage": asdict(self.usage),
            "wallclock_ms": self.wallclock_ms,
            "compaction_after": asdict(self.compaction_after) if self.compaction_after else None,
        }


@dataclass
class Trajectory:
    """A full agent run on a single task."""

    task_id: str
    benchmark: str
    model: str
    policy: str
    steps: List[Step] = field(default_factory=list)
    resolved: Optional[bool] = None     # filled in by benchmark.evaluate()
    final_answer: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "benchmark": self.benchmark,
            "model": self.model,
            "policy": self.policy,
            "resolved": self.resolved,
            "final_answer": self.final_answer,
            "steps": [s.to_dict() for s in self.steps],
            "extra": self.extra,
        }

    @property
    def total_prompt_tokens(self) -> int:
        return sum(s.usage.prompt_tokens for s in self.steps)

    @property
    def total_cached_tokens(self) -> int:
        return sum(s.usage.cached_tokens for s in self.steps)

    @property
    def total_completion_tokens(self) -> int:
        return sum(s.usage.completion_tokens for s in self.steps)

    @property
    def total_compaction_tokens(self) -> int:
        return sum(
            s.compaction_after.compaction_call_tokens
            for s in self.steps
            if s.compaction_after is not None
        )

    @property
    def num_compactions(self) -> int:
        return sum(1 for s in self.steps if s.compaction_after is not None)


@dataclass
class LifetimeCost:
    """Decomposed cost for one trajectory under one price sheet entry."""

    model: str
    input_uncached_dollars: float
    input_cached_dollars: float
    output_dollars: float
    cache_write_dollars: float
    compaction_dollars: float

    @property
    def total(self) -> float:
        return (
            self.input_uncached_dollars
            + self.input_cached_dollars
            + self.output_dollars
            + self.cache_write_dollars
            + self.compaction_dollars
        )
