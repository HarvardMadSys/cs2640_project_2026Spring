"""Two-tier KV cache controller for AdaptiveCache.

Tier 1 (every step): Keep prompt bytes identical, delete low-value KV blocks
    from LMCache. vLLM reports those as cache misses and recomputes them.
Tier 2 (rare, ~every 10-20 steps): Restructure message list for a new stable
    prefix, accepting a one-time full cache miss in exchange for a longer
    stable prefix going forward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .config import CacheConfig
from .evictor import Evictor
from .layout import LayoutOptimizer
from .scorer import Scorer
from .segmenter import segment_messages
from .types import Block, BlockType, Zone

CHUNK_SIZE = 256  # Must match LMCache LMCACHE_CHUNK_SIZE


@dataclass
class BlockPosition:
    """Maps a scored block to its token position range and LMCache chunk indices."""

    block: Block
    token_start: int
    token_end: int
    chunk_indices: List[int]  # Which 256-token chunks this block spans


class KVController:
    """Two-tier KV cache management.

    Tier 1 (every step): Keep prompt identical, delete low-value KV blocks.
    Tier 2 (rare, ~every 10-20 steps): Restructure message list for new stable prefix.

    The controller works without a live lmcache_client — pass None when
    testing locally and block operations become no-ops.
    """

    def __init__(self, config: CacheConfig = None):
        self.config = config or CacheConfig()
        self.scorer = Scorer(self.config)
        self.evictor = Evictor(self.config)
        self.layout = LayoutOptimizer(self.config)
        self._prev_top_k: set = set()
        self._steps_since_reorg: int = 0
        self._step_count: int = 0

    def on_step_complete(
        self,
        messages: list,
        prompt_token_ids: list,
        lmcache_client,  # Has delete_kv_blocks() and pin_kv_blocks() methods; may be None
    ) -> Tuple[list, bool]:
        """Called after each agent step.

        Returns:
            (messages_for_next_step, did_reorganize)
            - If Tier 1 only: returns original messages (KV eviction happened silently)
            - If Tier 2 fired: returns reorganized messages (one-time cache miss)
        """
        self._step_count += 1

        # Parse messages into blocks and score them
        blocks = segment_messages(messages, step=self._step_count)
        if not blocks:
            return messages, False

        # Build recent content string for reference-count scoring
        recent_content = " ".join(
            b.content for b in blocks[-4:] if b.content
        )
        self.scorer.score_blocks(blocks, step=self._step_count, all_content=recent_content)

        # Assign zones (needed for eviction and reorg decisions)
        self.layout.assign_zones(blocks, step=self._step_count)

        # Compute token positions for each block
        block_positions = self._compute_block_positions(blocks, prompt_token_ids)

        # Check if over budget
        total_tokens = sum(b.token_count for b in blocks if b.zone != Zone.EVICT)
        if total_tokens <= self.config.soft_budget:
            # Under budget — just pin high-value blocks
            self._tier1_pin(block_positions, prompt_token_ids, lmcache_client)
            return messages, False

        # Tier 1: evict low-value blocks from LMCache
        blocks_to_evict = self._select_blocks_to_evict(blocks, total_tokens)
        if blocks_to_evict:
            evict_ids = {b.block_id for b in blocks_to_evict}
            chunk_indices: list = []
            for bp in block_positions:
                if bp.block.block_id in evict_ids:
                    chunk_indices.extend(bp.chunk_indices)
            if chunk_indices and lmcache_client is not None:
                lmcache_client.delete_kv_blocks(
                    prompt_token_ids, list(set(chunk_indices))
                )

        # Tier 2: check if message-level reorganization is needed
        current_top_k = {
            b.block_id
            for b in sorted(blocks, key=lambda b: -b.pin_score)[
                : self.config.reorg_top_k
            ]
        }
        should_reorg = self._should_reorganize(current_top_k, blocks)

        if should_reorg:
            self.layout.assign_zones(blocks, step=self._step_count)
            reorganized = self.layout.reorganize(blocks)
            self._prev_top_k = current_top_k
            self._steps_since_reorg = 0
            # Convert reorganized blocks back to messages
            new_messages = self._blocks_to_messages(reorganized)
            return new_messages, True

        self._prev_top_k = current_top_k
        self._steps_since_reorg += 1
        return messages, False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_block_positions(
        self, blocks: List[Block], prompt_token_ids: list
    ) -> List[BlockPosition]:
        """Map each block to its token position range and chunk indices."""
        positions = []
        current_pos = 0
        for block in blocks:
            token_start = current_pos
            token_end = current_pos + block.token_count
            # Which 256-token chunks does this block overlap?
            if token_end > token_start:
                chunk_start = token_start // CHUNK_SIZE
                chunk_end = (token_end - 1) // CHUNK_SIZE
            else:
                chunk_start = token_start // CHUNK_SIZE
                chunk_end = chunk_start
            chunk_indices = list(range(chunk_start, chunk_end + 1))
            positions.append(
                BlockPosition(
                    block=block,
                    token_start=token_start,
                    token_end=token_end,
                    chunk_indices=chunk_indices,
                )
            )
            current_pos = token_end
        return positions

    def _select_blocks_to_evict(
        self, blocks: List[Block], total_tokens: int
    ) -> List[Block]:
        """Select blocks to evict, never evicting SYSTEM/TASK or Zone.PIN blocks."""
        evictable = [
            b
            for b in blocks
            if b.zone not in (Zone.PIN, Zone.EVICT)
            and b.block_type not in (BlockType.SYSTEM, BlockType.TASK)
        ]
        # Sort by evict_priority ascending (lowest = evict first)
        evictable.sort(key=lambda b: b.evict_priority)

        to_evict = []
        tokens_freed = 0
        target = total_tokens - self.config.soft_budget
        for block in evictable:
            if tokens_freed >= target:
                break
            to_evict.append(block)
            tokens_freed += block.token_count
        return to_evict

    def _tier1_pin(
        self,
        block_positions: List[BlockPosition],
        prompt_token_ids: list,
        lmcache_client,
    ) -> None:
        """Pin high-value blocks when under budget."""
        if lmcache_client is None:
            return
        high_value = [
            bp
            for bp in block_positions
            if bp.block.pin_score > self.config.pin_threshold
        ]
        if high_value:
            pin_indices: list = []
            for bp in high_value:
                pin_indices.extend(bp.chunk_indices)
            lmcache_client.pin_kv_blocks(
                prompt_token_ids, list(set(pin_indices))
            )

    def _should_reorganize(self, current_top_k: set, blocks: List[Block]) -> bool:
        """Tier 2 trigger: importance structure shifted significantly."""
        if not self._prev_top_k:
            return False

        change = len(current_top_k - self._prev_top_k) / max(
            len(self._prev_top_k), 1
        )
        total_tokens = sum(b.token_count for b in blocks)
        hole_ratio = (
            sum(b.token_count for b in blocks if b.zone == Zone.EVICT)
            / max(total_tokens, 1)
        )

        return (
            change > self.config.reorg_top_k_change_pct
            or self._steps_since_reorg >= 20
            or hole_ratio > self.config.hole_ratio_threshold
        )

    def _blocks_to_messages(self, blocks: List[Block]) -> list:
        """Convert reorganized blocks back to OpenAI message format."""
        messages = []
        for block in blocks:
            if block.zone == Zone.EVICT:
                continue
            msg = _block_to_message(block)
            if msg is not None:
                messages.append(msg)
        return messages


def _block_to_message(block: Block) -> Optional[dict]:
    """Convert a single Block to an OpenAI-format message dict."""
    if block.block_type == BlockType.SYSTEM:
        return {"role": "system", "content": block.content}
    if block.block_type == BlockType.TASK:
        return {"role": "user", "content": block.content}
    if block.block_type in (BlockType.THOUGHT, BlockType.ACTION):
        return {"role": "assistant", "content": block.content}
    # Observation types → tool role with a stable placeholder tool_call_id
    return {
        "role": "tool",
        "content": block.content,
        "tool_call_id": "evicted",
    }
