"""Baseline eviction policies: FIFO, LRU, FixedWindow.

All implement the same interface as AdaptiveCache for fair comparison.
These are the 75% milestone baselines from detailed-research-plan.md.
"""

from __future__ import annotations

from adaptive_cache.config import CacheConfig
from adaptive_cache.segmenter import segment_new_step
from adaptive_cache.types import Block, BlockType, Zone


class _BaselineCache:
    """Common base for baseline policies."""

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()
        self.blocks: list[Block] = []
        self.step: int = 0
        self._next_block_id: int = 0

    def init(self, system_prompt: str, task: str) -> list[dict]:
        from adaptive_cache.segmenter import segment_messages

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.blocks = segment_messages(messages, step=0, start_block_id=0)
        self._next_block_id = len(self.blocks)
        for block in self.blocks:
            block.zone = Zone.PIN
        return messages

    def _add_new_blocks(
        self, thought: str, action: dict | None, observation: str | None, tool_name: str | None
    ) -> None:
        self.step += 1
        new_blocks = segment_new_step(
            thought=thought,
            action=action,
            observation=observation,
            step=self.step,
            start_block_id=self._next_block_id,
            tool_name=tool_name,
        )
        self.blocks.extend(new_blocks)
        self._next_block_id += len(new_blocks)

    @property
    def total_tokens(self) -> int:
        return sum(b.token_count for b in self.blocks if b.zone != Zone.EVICT)

    @property
    def pinned_tokens(self) -> int:
        return sum(
            b.token_count
            for b in self.blocks
            if b.block_type in (BlockType.SYSTEM, BlockType.TASK)
        )

    def to_messages(self) -> list[dict]:
        from adaptive_cache.cache import _block_to_message

        return [
            msg
            for b in self.blocks
            if b.zone != Zone.EVICT
            for msg in [_block_to_message(b)]
            if msg is not None
        ]

    @property
    def stats(self) -> dict:
        return {
            "step": self.step,
            "total_tokens": self.total_tokens,
            "pinned_tokens": self.pinned_tokens,
            "num_blocks": len([b for b in self.blocks if b.zone != Zone.EVICT]),
            "cache_hit_estimate": self.pinned_tokens / max(self.total_tokens, 1),
        }


class FIFOCache(_BaselineCache):
    """Evict oldest non-system/task block when over budget."""

    def update(
        self,
        thought: str,
        action: dict | None = None,
        observation: str | None = None,
        tool_name: str | None = None,
    ) -> list[dict]:
        self._add_new_blocks(thought, action, observation, tool_name)

        budget = self.config.soft_budget
        while self.total_tokens > budget:
            # Find oldest non-pinned block
            evicted = False
            for block in self.blocks:
                if block.zone != Zone.EVICT and block.block_type not in (
                    BlockType.SYSTEM,
                    BlockType.TASK,
                ):
                    block.zone = Zone.EVICT
                    evicted = True
                    break
            if not evicted:
                break  # only system/task remain

        return self.to_messages()


class LRUCache(_BaselineCache):
    """Evict least-recently-referenced non-system/task block when over budget."""

    def __init__(self, config: CacheConfig | None = None) -> None:
        super().__init__(config)
        self._last_referenced: dict[int, int] = {}  # block_id → step

    def update(
        self,
        thought: str,
        action: dict | None = None,
        observation: str | None = None,
        tool_name: str | None = None,
    ) -> list[dict]:
        self._add_new_blocks(thought, action, observation, tool_name)

        # Update reference timestamps: check if any block content appears in recent output
        recent = (thought or "") + " " + (observation or "")
        for block in self.blocks:
            if block.zone == Zone.EVICT:
                continue
            # Simple: mark as referenced if any 10+ char substring of block appears in recent
            if block.content and len(block.content) >= 10 and block.content[:50] in recent:
                self._last_referenced[block.block_id] = self.step
            elif block.block_id not in self._last_referenced:
                self._last_referenced[block.block_id] = block.step_created

        budget = self.config.soft_budget
        while self.total_tokens > budget:
            # Find least-recently-referenced non-pinned block
            candidates = [
                b
                for b in self.blocks
                if b.zone != Zone.EVICT
                and b.block_type not in (BlockType.SYSTEM, BlockType.TASK)
            ]
            if not candidates:
                break

            lru_block = min(
                candidates,
                key=lambda b: self._last_referenced.get(b.block_id, 0),
            )
            lru_block.zone = Zone.EVICT

        return self.to_messages()


class FixedWindowCache(_BaselineCache):
    """Keep only the last N tokens (StreamingLLM-style). System/task always kept."""

    def __init__(self, config: CacheConfig | None = None, window_tokens: int | None = None) -> None:
        super().__init__(config)
        self.window_tokens = window_tokens or (config or CacheConfig()).soft_budget

    def update(
        self,
        thought: str,
        action: dict | None = None,
        observation: str | None = None,
        tool_name: str | None = None,
    ) -> list[dict]:
        self._add_new_blocks(thought, action, observation, tool_name)

        # Keep system/task + last N tokens of dynamic content
        pinned = [
            b for b in self.blocks
            if b.block_type in (BlockType.SYSTEM, BlockType.TASK)
        ]
        dynamic = [
            b for b in self.blocks
            if b.block_type not in (BlockType.SYSTEM, BlockType.TASK)
            and b.zone != Zone.EVICT
        ]

        pinned_tokens = sum(b.token_count for b in pinned)
        remaining_budget = self.window_tokens - pinned_tokens

        # Keep from the end (newest)
        kept: list[Block] = []
        tokens_kept = 0
        for block in reversed(dynamic):
            if tokens_kept + block.token_count <= remaining_budget:
                kept.append(block)
                tokens_kept += block.token_count
            else:
                block.zone = Zone.EVICT

        kept.reverse()
        self.blocks = pinned + kept + [b for b in self.blocks if b.zone == Zone.EVICT]

        return self.to_messages()
