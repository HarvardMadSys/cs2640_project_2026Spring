"""2D importance x stability scoring pipeline.

Implements the scoring framework from importance-scoring.md.
75% milestone: structural type prior + reference count + importance variance.
100% milestone stubs: cumulative attention + dependency centrality.
125% milestone stubs: expected attention + low-entropy protection.
"""

from __future__ import annotations

import re

from adaptive_cache.config import CacheConfig
from adaptive_cache.types import Block, BlockType


# --- Structural type priors (from importance-scoring.md) ---

IMPORTANCE_PRIOR: dict[BlockType, float] = {
    BlockType.SYSTEM: 1.0,
    BlockType.TASK: 1.0,
    BlockType.OBS_FILE: 0.6,
    BlockType.ACTION: 0.5,
    BlockType.OBS_GREP: 0.4,
    BlockType.THOUGHT: 0.4,
    BlockType.OBS_SHELL: 0.3,
    BlockType.OBS_GENERIC: 0.3,
    BlockType.OBS_ERROR: 0.5,  # important when fresh, volatile
}

STABILITY_PRIOR: dict[BlockType, float] = {
    BlockType.SYSTEM: 1.0,
    BlockType.TASK: 1.0,
    BlockType.OBS_FILE: 0.7,
    BlockType.ACTION: 0.5,
    BlockType.OBS_GREP: 0.3,
    BlockType.THOUGHT: 0.4,
    BlockType.OBS_SHELL: 0.2,
    BlockType.OBS_GENERIC: 0.3,
    BlockType.OBS_ERROR: 0.1,
}


class Scorer:
    """Computes importance and stability scores for all blocks."""

    def __init__(self, config: CacheConfig) -> None:
        self.config = config

    def score_blocks(self, blocks: list[Block], step: int, all_content: str = "") -> None:
        """Score all blocks in place. Call once per agent step.

        Args:
            blocks: All blocks currently in context.
            step: Current agent step number.
            all_content: Concatenated content of recent thoughts/actions for
                         reference count detection.
        """
        for block in blocks:
            imp = self._compute_importance(block, all_content)
            sta = self._compute_stability(block, step)

            block.importance = imp
            block.stability = sta

            # Track importance history for variance computation
            block.importance_history.append(imp)
            if len(block.importance_history) > self.config.importance_history_window:
                block.importance_history.pop(0)

    def _compute_importance(self, block: Block, recent_content: str) -> float:
        """Weighted sum of importance signals."""
        cfg = self.config

        signals: float = 0.0
        total_weight: float = 0.0

        # Signal 5: Structural type prior (75% milestone)
        w = cfg.w_structural_prior
        signals += w * IMPORTANCE_PRIOR.get(block.block_type, 0.3)
        total_weight += w

        # Signal 3: Reference count (75% milestone)
        w = cfg.w_reference_count
        if w > 0:
            ref_score = self._reference_count_signal(block, recent_content)
            signals += w * ref_score
            total_weight += w

        # Signal 2: Cumulative attention (100% milestone — stub)
        w = cfg.w_cumulative_attention
        if w > 0:
            att_score = self._cumulative_attention_signal(block)
            signals += w * att_score
            total_weight += w

        # Signal 4: Low-entropy protection (125% milestone — stub)
        w = cfg.w_low_entropy_boost
        if w > 0:
            le_score = self._low_entropy_signal(block)
            signals += w * le_score
            total_weight += w

        if total_weight > 0:
            return signals / total_weight
        return IMPORTANCE_PRIOR.get(block.block_type, 0.3)

    def _compute_stability(self, block: Block, step: int) -> float:
        """Weighted sum of stability signals."""
        cfg = self.config

        signals: float = 0.0
        total_weight: float = 0.0

        # Signal 5: Structural type stability prior
        w = cfg.w_type_stability
        signals += w * STABILITY_PRIOR.get(block.block_type, 0.3)
        total_weight += w

        # Signal 6: Importance variance (75% milestone)
        w = cfg.w_variance_stability
        variance = block.importance_variance()
        # Low variance → high stability. Clamp variance to [0, 1].
        stability_from_variance = max(0.0, 1.0 - min(variance, 1.0))
        signals += w * stability_from_variance
        total_weight += w

        # Signal 7: Dependency centrality (100% milestone — stub)
        w = cfg.w_dep_stability
        if w > 0 and cfg.w_dependency_centrality > 0:
            dep_score = self._dependency_centrality_signal(block)
            signals += w * dep_score
            total_weight += w

        if total_weight > 0:
            return signals / total_weight
        return STABILITY_PRIOR.get(block.block_type, 0.3)

    def _reference_count_signal(self, block: Block, recent_content: str) -> float:
        """Count how many times this block's content is referenced in recent output.

        Uses simple substring matching: extract identifiers from the block and
        check if they appear in recent thoughts/actions.
        """
        if not recent_content or not block.content:
            return min(block.reference_count / max(self.config.reference_count_max, 1), 1.0)

        # Extract identifiers (function names, file paths, variable names)
        identifiers = _extract_identifiers(block.content)
        new_refs = sum(1 for ident in identifiers if ident in recent_content)
        block.reference_count += new_refs

        return min(block.reference_count / max(self.config.reference_count_max, 1), 1.0)

    def _cumulative_attention_signal(self, block: Block) -> float:
        """Stub: cumulative past attention (H2O-style). Returns 0 until attention hooks are wired."""
        if not block.attention_history:
            return 0.0
        return sum(block.attention_history) / len(block.attention_history)

    def _dependency_centrality_signal(self, block: Block) -> float:
        """Stub: tool-call dependency graph in-degree. Returns 0 until DAG is built."""
        return 0.0

    def _low_entropy_signal(self, block: Block) -> float:
        """Stub: low-entropy token protection (ForesightKV). Returns 0 until perplexity is available."""
        return 0.0


def _extract_identifiers(content: str) -> list[str]:
    """Extract likely identifiers from block content for reference counting.

    Looks for: file paths, function/class names, variable names.
    """
    identifiers: list[str] = []

    # File paths
    identifiers.extend(re.findall(r"[\w./]+\.(?:py|js|ts|go|rs|java|c|h|md)", content))

    # Function/class names (def foo, class Bar, func baz)
    identifiers.extend(re.findall(r"(?:def|class|func|function)\s+(\w+)", content))

    # CamelCase or snake_case identifiers (3+ chars, not common words)
    identifiers.extend(
        m for m in re.findall(r"\b([a-z_][a-z0-9_]{2,})\b", content)
        if m not in _STOP_WORDS
    )

    return identifiers


_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all",
    "can", "had", "her", "was", "one", "our", "out", "has",
    "from", "that", "this", "with", "have", "will", "your",
    "been", "each", "make", "like", "then", "them", "than",
    "some", "into", "could", "other", "after", "also", "its",
    "which", "about", "these", "would", "there", "their",
    "what", "more", "when", "where", "most", "only",
    "true", "false", "none", "self", "return", "import",
    "print", "pass", "break", "continue", "else", "elif",
})
