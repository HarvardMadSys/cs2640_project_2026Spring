"""Minimal standalone ReAct agent for development and testing.

Implements a simple Thought-Action-Observation loop with pluggable
context management (AdaptiveCache or baseline policies).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

from adaptive_cache.cache import AdaptiveCache
from harness.tools import Tool

logger = logging.getLogger(__name__)

REACT_SYSTEM_PROMPT = """You are a coding agent. You solve tasks by reasoning step by step and using tools.

At each step:
1. Think about what you need to do next
2. Choose a tool and provide arguments
3. Observe the result and continue

Available tools:
{tool_descriptions}

Respond with a JSON object:
{{"thought": "your reasoning", "tool": "tool_name", "arguments": {{"arg": "value"}}}}

When the task is complete, respond with:
{{"thought": "task complete", "tool": "done", "arguments": {{}}}}"""


@dataclass
class StepTrace:
    step: int
    thought: str
    tool: str
    arguments: dict
    observation: str
    context_tokens: int
    pinned_tokens: int
    cache_stats: dict = field(default_factory=dict)


@dataclass
class AgentTrace:
    task: str
    steps: list[StepTrace] = field(default_factory=list)
    success: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class ReactAgent:
    """Minimal ReAct agent with pluggable context management."""

    def __init__(
        self,
        client: OpenAI,
        tools: dict[str, Tool],
        context_manager: AdaptiveCache,
        model: str = "qwen2.5-7b-instruct",
        max_steps: int = 25,
    ) -> None:
        self.client = client
        self.tools = tools
        self.context_manager = context_manager
        self.model = model
        self.max_steps = max_steps

    def run(self, task: str) -> AgentTrace:
        """Execute the ReAct loop on a task."""
        trace = AgentTrace(task=task)

        # Build system prompt with tool descriptions
        tool_desc = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )
        system_prompt = REACT_SYSTEM_PROMPT.format(tool_descriptions=tool_desc)

        # Initialize context
        messages = self.context_manager.init(system_prompt, task)

        for step_num in range(1, self.max_steps + 1):
            # Call LLM
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=2048,
            )

            reply = response.choices[0].message.content or ""
            trace.total_input_tokens += response.usage.prompt_tokens if response.usage else 0
            trace.total_output_tokens += response.usage.completion_tokens if response.usage else 0

            # Parse response
            try:
                parsed = json.loads(reply)
            except json.JSONDecodeError:
                # Try to extract JSON from the response
                parsed = _extract_json(reply)
                if parsed is None:
                    logger.warning("Step %d: Could not parse response: %s", step_num, reply[:200])
                    parsed = {"thought": reply, "tool": "done", "arguments": {}}

            thought = parsed.get("thought", "")
            tool_name = parsed.get("tool", "done")
            arguments = parsed.get("arguments", {})

            # Check for completion
            if tool_name == "done":
                step_trace = StepTrace(
                    step=step_num,
                    thought=thought,
                    tool="done",
                    arguments={},
                    observation="",
                    context_tokens=self.context_manager.total_tokens,
                    pinned_tokens=self.context_manager.pinned_tokens,
                    cache_stats=self.context_manager.stats,
                )
                trace.steps.append(step_trace)
                trace.success = True
                break

            # Execute tool
            tool = self.tools.get(tool_name)
            if tool is None:
                observation = f"Error: unknown tool '{tool_name}'. Available: {list(self.tools.keys())}"
            else:
                observation = tool(**arguments)

            # Update context through the cache manager
            action = {"function": {"name": tool_name, "arguments": json.dumps(arguments)}}
            messages = self.context_manager.update(
                thought=thought,
                action=action,
                observation=observation,
                tool_name=tool_name,
            )

            step_trace = StepTrace(
                step=step_num,
                thought=thought,
                tool=tool_name,
                arguments=arguments,
                observation=observation[:500],  # truncate for trace
                context_tokens=self.context_manager.total_tokens,
                pinned_tokens=self.context_manager.pinned_tokens,
                cache_stats=self.context_manager.stats,
            )
            trace.steps.append(step_trace)
            logger.info(
                "Step %d: %s → %d tokens (pinned: %d)",
                step_num,
                tool_name,
                step_trace.context_tokens,
                step_trace.pinned_tokens,
            )

        return trace


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from text that may contain other content."""
    import re

    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None
