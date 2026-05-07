"""Hole-leaving eviction engine.

Evicts blocks by marking them as Zone.EVICT. Evicted blocks become "holes" —
their positions are non-attendable but surviving blocks are never renumbered.

Eviction order: lowest evict_priority first (importance * (1 - stability)),
with FIFO tiebreak on step_created. Never evicts Zone.PIN blocks.
"""

from __future__ import annotations

from adaptive_cache.config import CacheConfig
from adaptive_cache.types import Block, Zone


class Evictor:
    """Manages block eviction under budget constraints."""

    def __init__(self, config: CacheConfig) -> None:
        self.config = config

    def evict(self, blocks: list[Block], current_tokens: int) -> list[Block]:
        """Mark blocks for eviction until under soft budget. Returns surviving blocks.

        Eviction cascade:
        1. Evict blocks already marked Zone.EVICT
        2. If still over budget, evict lowest-scoring Zone.SUFFIX blocks
        3. If still over budget, evict lowest-scoring Zone.MIDDLE blocks
        4. Never evict Zone.PIN blocks

        Args:
            blocks: All blocks (including already-evicted holes).
            current_tokens: Total tokens in context (including holes).

        Returns:
            List of surviving (non-evicted) blocks.
        """
        budget = self.config.soft_budget
        if current_tokens <= budget:
            return [b for b in blocks if b.zone != Zone.EVICT]

        # Sort eviction candidates by priority (lowest evict_priority first, then oldest)
        # Cascade through zones: EVICT → SUFFIX → MIDDLE
        for target_zone in (Zone.EVICT, Zone.SUFFIX, Zone.MIDDLE):
            if current_tokens <= budget:
                break

            candidates = [b for b in blocks if b.zone == target_zone]
            candidates.sort(key=lambda b: (b.evict_priority, -b.step_created))

            for block in candidates:
                if current_tokens <= budget:
                    break
                block.zone = Zone.EVICT
                current_tokens -= block.token_count

        return [b for b in blocks if b.zone != Zone.EVICT]

    def compute_hole_ratio(self, blocks: list[Block], total_allocated: int) -> float:
        """Compute the ratio of evicted (hole) tokens to total allocated positions.

        A high hole ratio means many positions are wasted as holes and
        compaction (layout reorganization) should be triggered.
        """
        if total_allocated == 0:
            return 0.0
        surviving_tokens = sum(b.token_count for b in blocks if b.zone != Zone.EVICT)
        return 1.0 - (surviving_tokens / total_allocated)

    def needs_compaction(self, blocks: list[Block], total_allocated: int) -> bool:
        """Check if hole ratio exceeds the compaction threshold."""
        return self.compute_hole_ratio(blocks, total_allocated) > self.config.hole_ratio_threshold
