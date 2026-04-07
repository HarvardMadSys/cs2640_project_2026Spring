from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BlockType(Enum):
    """Structural taxonomy of context blocks."""

    SYSTEM = "system"
    TASK = "task"
    THOUGHT = "thought"
    ACTION = "action"
    OBS_FILE = "obs_file"
    OBS_SHELL = "obs_shell"
    OBS_GREP = "obs_grep"
    OBS_ERROR = "obs_error"
    OBS_GENERIC = "obs_generic"


class Zone(Enum):
    """Three-zone layout assignment."""

    PIN = "pin"          # Zone 1: pinned prefix — stable, high importance x stability
    MIDDLE = "middle"    # Zone 2: moderate scores, promotion/demotion candidates
    SUFFIX = "suffix"    # Zone 3: volatile, recent, evict-first
    EVICT = "evict"      # Marked for eviction (becomes a hole)


@dataclass
class Block:
    """Atomic unit of context management.

    Each block is a logically coherent unit of content (one tool call result,
    one reasoning step, the system prompt, etc.) with scoring metadata.
    """

    block_id: int
    block_type: BlockType
    content: str
    token_count: int
    step_created: int

    # Scoring (updated each step by the scorer)
    importance: float = 0.0
    stability: float = 0.0
    zone: Zone = Zone.SUFFIX

    # Tracking signals
    reference_count: int = 0
    importance_history: list[float] = field(default_factory=list)
    attention_history: list[float] = field(default_factory=list)

    # Layout tracking
    pinned_since_step: int | None = None  # step when this block was first pinned

    @property
    def pin_score(self) -> float:
        return self.importance * self.stability

    @property
    def evict_priority(self) -> float:
        """Lower = evict first."""
        return self.importance * (1 - self.stability)

    @property
    def age(self) -> int:
        """Steps since creation. Requires current step to be set externally."""
        return 0  # computed by scorer with current step context

    def importance_variance(self) -> float:
        if len(self.importance_history) < 2:
            return 1.0  # high variance = unstable = default for new blocks
        mean = sum(self.importance_history) / len(self.importance_history)
        return sum((x - mean) ** 2 for x in self.importance_history) / len(
            self.importance_history
        )
