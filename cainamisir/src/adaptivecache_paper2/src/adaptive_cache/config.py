from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CacheConfig:
    """Configuration for the AdaptiveCache system.

    All thresholds and weights are tunable hyperparameters.
    Defaults are informed by system-design.md and importance-scoring.md.
    """

    # Budget (in tokens)
    soft_budget: int = 102_400       # 80% of 128K — eviction starts here
    hard_budget: int = 128_000       # absolute max — compaction triggered
    emergency_budget: int = 131_072  # beyond this, emergency summarization

    # Zone assignment thresholds (on pin_score = importance * stability)
    pin_threshold: float = 0.5       # pin_score above this → Zone.PIN
    middle_threshold: float = 0.2    # pin_score above this → Zone.MIDDLE
    importance_threshold: float = 0.4  # importance alone above this → Zone.SUFFIX (hot but volatile)

    # Layout optimizer triggers
    reorg_top_k: int = 10                    # track top-K blocks by pin_score
    reorg_top_k_change_pct: float = 0.3      # >30% change triggers reorg
    hole_ratio_threshold: float = 0.4        # hole tokens / total → compaction

    # Scoring weights — 75% milestone signals
    w_structural_prior: float = 0.4
    w_reference_count: float = 0.3
    w_importance_variance: float = 0.3

    # Scoring weights — 100% milestone signals (stubs, weight=0 until implemented)
    w_cumulative_attention: float = 0.0
    w_dependency_centrality: float = 0.0

    # Scoring weights — 125% milestone signals (stubs)
    w_expected_attention: float = 0.0
    w_low_entropy_boost: float = 0.0

    # Stability weights
    w_type_stability: float = 0.5
    w_variance_stability: float = 0.3
    w_dep_stability: float = 0.2

    # Sink tokens (StreamingLLM: positions 0-3 are attention sinks)
    sink_positions: int = 4

    # Reference count decay
    reference_count_max: int = 20  # normalize ref count to [0, 1]

    # Importance history window
    importance_history_window: int = 10

    @property
    def importance_weights(self) -> list[tuple[str, float]]:
        return [
            ("structural_prior", self.w_structural_prior),
            ("reference_count", self.w_reference_count),
            ("cumulative_attention", self.w_cumulative_attention),
            ("low_entropy_boost", self.w_low_entropy_boost),
        ]

    @property
    def stability_weights(self) -> list[tuple[str, float]]:
        return [
            ("type_stability", self.w_type_stability),
            ("variance_stability", self.w_variance_stability),
            ("dep_stability", self.w_dep_stability),
        ]
