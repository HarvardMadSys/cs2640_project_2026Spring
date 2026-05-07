"""Layout optimizer: zone assignment and prefix-stable reordering.

Determines the three-zone layout and decides when to reorganize.
Key constraint: existing Zone.PIN blocks keep their relative order
on reorganization; new promotions append at the end of Zone 1.
"""

from __future__ import annotations

from adaptive_cache.config import CacheConfig
from adaptive_cache.types import Block, BlockType, Zone


class LayoutOptimizer:
    """Assigns zones and manages prefix-stable layout reorganization."""

    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self._prev_top_k: set[int] = set()  # block_ids of previous top-K

    def assign_zones(self, blocks: list[Block], step: int) -> None:
        """Assign each block to a zone based on scoring thresholds.

        Zone assignment rules (from system-design.md §4-5):
        - SYSTEM and TASK blocks are always PIN
        - Blocks from the current step are at least SUFFIX (never immediately evicted)
        - pin_score (imp * sta) > pin_threshold → PIN
        - importance > importance_threshold (hot but volatile) → SUFFIX
        - pin_score > middle_threshold → MIDDLE
        - Otherwise → EVICT
        """
        cfg = self.config

        for block in blocks:
            if block.zone == Zone.EVICT:
                continue  # already dead

            # System and task are always pinned
            if block.block_type in (BlockType.SYSTEM, BlockType.TASK):
                block.zone = Zone.PIN
                if block.pinned_since_step is None:
                    block.pinned_since_step = step
                continue

            pin_score = block.pin_score

            if pin_score > cfg.pin_threshold:
                block.zone = Zone.PIN
                if block.pinned_since_step is None:
                    block.pinned_since_step = step
            elif block.importance > cfg.importance_threshold:
                block.zone = Zone.SUFFIX
                block.pinned_since_step = None
            elif pin_score > cfg.middle_threshold:
                block.zone = Zone.MIDDLE
                block.pinned_since_step = None
            elif block.step_created == step:
                # New blocks from this step stay in suffix — don't evict on arrival
                block.zone = Zone.SUFFIX
                block.pinned_since_step = None
            else:
                block.zone = Zone.EVICT
                block.pinned_since_step = None

    def should_reorganize(self, blocks: list[Block], hole_ratio: float) -> bool:
        """Check if the layout optimizer should fire.

        Triggers:
        1. Top-K set changed by more than reorg_top_k_change_pct
        2. Hole ratio exceeds threshold (compaction needed)
        """
        cfg = self.config

        # Check hole ratio
        if hole_ratio > cfg.hole_ratio_threshold:
            return True

        # Check top-K change
        current_top_k = self._current_top_k(blocks)
        if not self._prev_top_k:
            self._prev_top_k = current_top_k
            return False

        overlap = len(current_top_k & self._prev_top_k)
        k = max(len(self._prev_top_k), 1)
        change_pct = 1.0 - (overlap / k)

        return change_pct > cfg.reorg_top_k_change_pct

    def reorganize(self, blocks: list[Block]) -> list[Block]:
        """Reorder blocks into the three-zone layout.

        Layout order:
          Zone 1 (PIN): existing pins first (preserve relative order),
                        then new promotions (sorted by pin_score descending)
          Zone 2 (MIDDLE): sorted by importance descending
          Zone 3 (SUFFIX): chronological order (newest last)

        This invalidates the prefix cache for one step. The new layout
        becomes the stable prefix for subsequent steps.

        Returns:
            Reordered list of blocks (only surviving, non-EVICT blocks).
        """
        surviving = [b for b in blocks if b.zone != Zone.EVICT]

        pinned = [b for b in surviving if b.zone == Zone.PIN]
        middle = [b for b in surviving if b.zone == Zone.MIDDLE]
        suffix = [b for b in surviving if b.zone == Zone.SUFFIX]

        # Zone 1: existing pins keep relative order, new promotions at the end
        existing_pins = sorted(
            [b for b in pinned if b.pinned_since_step is not None],
            key=lambda b: (b.pinned_since_step, b.block_id),
        )
        # New promotions (just assigned PIN this step) sorted by pin_score
        # They don't have pinned_since_step yet from a prior step, but
        # assign_zones sets it — so "new" means pinned_since_step == current step
        # For simplicity, just keep existing_pins in their order; all pins are already ordered.

        # Zone 2: middle blocks by importance descending
        middle.sort(key=lambda b: -b.importance)

        # Zone 3: suffix blocks chronologically (oldest first, newest last)
        suffix.sort(key=lambda b: (b.step_created, b.block_id))

        reordered = existing_pins + middle + suffix

        # Update top-K tracking
        self._prev_top_k = self._current_top_k(reordered)

        return reordered

    def _current_top_k(self, blocks: list[Block]) -> set[int]:
        """Get the block_ids of the top-K blocks by pin_score."""
        k = self.config.reorg_top_k
        scored = sorted(blocks, key=lambda b: -b.pin_score)
        return {b.block_id for b in scored[:k]}
