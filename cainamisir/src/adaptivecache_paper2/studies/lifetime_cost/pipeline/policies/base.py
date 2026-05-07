"""CompactionPolicy abstract base.

A policy is a function from (messages, context) → (new_messages, event).
Policies are stateful — they may track step count, prior top-K, etc.
The runner calls policy.maybe_compact() after every assistant turn.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..tokenization import Tokenizer, count_messages
from ..types import CompactionEvent


@dataclass
class CompactionContext:
    """Everything a policy needs that's not the messages themselves."""

    step: int
    budget: int                                  # token budget (soft trigger)
    hard_budget: int                             # hard trigger; must compact
    tokenizer: Tokenizer
    summarizer: Optional[Callable[[List[dict]], Tuple[str, int, int, int]]] = None
    """A callable returning (summary_text, in_cached_tokens, in_uncached_tokens,
    out_tokens) given a list of messages to summarize.

    in_cached: tokens of the messages-to-summarize themselves. These hit the
        provider's prefix cache because they were just sent in the agent's
        most-recent step.
    in_uncached: a small ~50-token "now produce a concise summary" instruction
        appended after the cached prefix.
    out: the generated summary length.

    Policies that don't call an LLM (NoCompaction, free_template) ignore this
    or return zeros."""


class CompactionPolicy(abc.ABC):
    """Stateful compaction policy."""

    name: str = "abstract"

    def __init__(self, **kwargs):
        self._kwargs = kwargs

    @abc.abstractmethod
    def maybe_compact(
        self,
        messages: List[dict],
        ctx: CompactionContext,
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        """Returns (possibly-modified messages, event if compaction fired)."""
        ...

    def maybe_recall(
        self,
        messages: List[dict],
        ctx: CompactionContext,
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        """Bidirectional companion to maybe_compact.

        Called by the runner BEFORE each model.chat() call. Lets the policy
        restore previously-evicted content (e.g. swap an inlined memento
        back to its full obs) when the prompt has headroom and the agent is
        likely to need the bytes back.

        Default: noop. Policies that support recall (currently only
        MementoPolicy) override.
        """
        return messages, None

    # convenience
    @staticmethod
    def _token_count(messages: List[dict], tokenizer: Tokenizer) -> int:
        return count_messages(messages, tokenizer)


class NoCompaction(CompactionPolicy):
    """Identity. Upper-bound on quality, lower-bound on cost ceiling — until OOM."""

    name = "none"

    def maybe_compact(self, messages, ctx):
        return messages, None
