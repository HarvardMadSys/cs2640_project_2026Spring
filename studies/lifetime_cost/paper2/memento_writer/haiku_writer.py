"""Generates memento text for tool observations via Haiku.

For Paper 2 v0 we don't have a Memento-trained model, so an off-the-shelf
frontier model produces the memento text. Cost is small: ~$0.005 per
memento at Haiku 4.5 rates (3K input + 200 output tokens).

Mementos preserve key facts from a tool obs so the agent can later
operate without the raw obs in attention. Memento's iterative-judge
recipe is overkill for v0 — single-pass works fine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


SYSTEM_PROMPT = """You write concise mementos for an LLM coding agent.

A memento is a terse, factual summary of a tool observation that lets the agent reason about the obs *without re-reading it*. The memento must preserve:
  - key file paths, function names, and line numbers
  - identifiers, error messages, exit codes
  - command outputs that affect subsequent decisions
  - any concrete fact the agent would otherwise need to look up again

Drop:
  - boilerplate, syntax noise, imports, irrelevant code
  - prose explanations, repetition

Output ONLY the memento text. No prefix, no quotes, no markdown. Aim for 50-200 tokens. Use compact notation: name:value pairs, semicolons, line ranges."""


USER_TEMPLATE = """The agent ran the following tool call:
  tool: {tool_name}
  args: {tool_args}

The tool returned this observation (truncated to first {obs_chars} chars):
{obs}

Write the memento."""


@dataclass
class MementoUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float


# Haiku 4.5 pricing per Anthropic docs (per Mtok)
_INPUT_USD_PER_MTOK = 1.0
_OUTPUT_USD_PER_MTOK = 5.0


class HaikuMementoWriter:
    """Off-the-shelf Haiku-based memento generator. v0."""

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5",
        api_key: Optional[str] = None,
        max_obs_chars: int = 8000,
        max_memento_tokens: int = 250,
    ):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "anthropic SDK not installed; pip install anthropic"
            ) from e
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._model = model
        self._max_obs_chars = max_obs_chars
        self._max_memento_tokens = max_memento_tokens
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.calls = 0

    def write(
        self,
        obs: str,
        *,
        tool_name: str = "unknown",
        tool_args: Optional[dict] = None,
    ) -> tuple[str, MementoUsage]:
        """Generate a memento for the given tool obs. Returns (text, usage)."""
        truncated = obs[: self._max_obs_chars]
        user_msg = USER_TEMPLATE.format(
            tool_name=tool_name,
            tool_args=str(tool_args or {}),
            obs_chars=self._max_obs_chars,
            obs=truncated,
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_memento_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()

        in_toks = resp.usage.input_tokens
        out_toks = resp.usage.output_tokens
        cost = (in_toks / 1_000_000) * _INPUT_USD_PER_MTOK + (
            out_toks / 1_000_000
        ) * _OUTPUT_USD_PER_MTOK

        self.total_input_tokens += in_toks
        self.total_output_tokens += out_toks
        self.total_cost_usd += cost
        self.calls += 1

        return text, MementoUsage(
            input_tokens=in_toks, output_tokens=out_toks, cost_usd=cost
        )
