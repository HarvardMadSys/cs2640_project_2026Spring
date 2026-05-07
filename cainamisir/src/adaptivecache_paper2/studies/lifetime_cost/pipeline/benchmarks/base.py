"""Benchmark base classes.

A Benchmark yields Tasks. A Task has:
  - id, messages_init (system + user task)
  - tools (list of OpenAI-style tool schemas)
  - tool_env (executes tool calls and returns observations)
  - evaluate(trajectory) -> bool (resolved or not)

The runner doesn't know about benchmark internals — it just steps the
agent loop and asks tool_env to execute each tool call.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


@dataclass
class Tool:
    """OpenAI-style tool definition + a Python callable."""

    name: str
    description: str
    parameters: Dict[str, Any]               # JSON schema
    fn: Callable[[Dict[str, Any]], str]      # returns observation string

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolEnv:
    """A bag of tools. Stateful — tools may share state (filesystem, memory)."""

    def __init__(self, tools: List[Tool]):
        self._tools = {t.name: t for t in tools}

    def add(self, *tools: Tool) -> None:
        """Inject extra tools post-construction (e.g. a policy-provided
        `recall` tool that depends on the runner's model adapter)."""
        for t in tools:
            self._tools[t.name] = t

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.to_openai() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, args: Dict[str, Any]) -> str:
        if name not in self._tools:
            return f"[tool error] unknown tool: {name}"
        try:
            return self._tools[name].fn(args)
        except Exception as e:
            return f"[tool error] {type(e).__name__}: {e}"


@dataclass
class Task:
    id: str
    messages_init: List[Dict[str, Any]]
    tool_env: ToolEnv
    metadata: Dict[str, Any] = field(default_factory=dict)
    max_steps: int = 50
    # evaluator may need the full trajectory + final answer
    evaluator: Optional[Callable[["Trajectory"], bool]] = None  # type: ignore


class Benchmark(abc.ABC):
    """A benchmark yields Tasks."""

    name: str = "abstract"

    @abc.abstractmethod
    def tasks(self) -> Iterable[Task]:
        ...
