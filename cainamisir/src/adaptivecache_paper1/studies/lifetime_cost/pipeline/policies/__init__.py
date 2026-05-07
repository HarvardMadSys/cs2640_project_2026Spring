"""Compaction policies. Each is a callable: compact(messages, ctx) -> messages."""

from .base import CompactionPolicy, CompactionContext, NoCompaction
from .naive_summary import NaiveSummary
from .microcompact import Microcompact
from .prefix_preserving import PrefixPreserving
from .boundary_aware import BoundaryAware
from .position_aware import PositionAware
from .evict_oldest import EvictOldest
from .llm_reorganizer import LLMReorganizer
from .smart_evict import SmartEvict
from .score_periodic import ScorePeriodic
from .consumption_evict import ConsumptionEvict


REGISTRY = {
    "none": NoCompaction,
    "naive_summary": NaiveSummary,
    "microcompact": Microcompact,
    "prefix_preserving": PrefixPreserving,
    "boundary_aware": BoundaryAware,
    "position_aware": PositionAware,
    "evict_oldest": EvictOldest,
    "llm_reorganizer": LLMReorganizer,
    "smart_evict": SmartEvict,
    "score_periodic": ScorePeriodic,
    "consumption_evict": ConsumptionEvict,
    # Alias: same class, but the config sets `preserve_facts=true` to enable
    # Step B behavior (placeholder includes a structured one-line fact).
    "consumption_evict_facts": ConsumptionEvict,
    # Alias: same class, but config sets `outline_mode=true`. Placeholder
    # contains a multi-line file outline (line# → header) plus an explicit
    # "re-read to act" instruction. Designed to give location breadcrumbs
    # without inducing the function-name commitment loop seen in `_facts`.
    "consumption_evict_outline": ConsumptionEvict,
}


def build_policy(name: str, **kwargs) -> CompactionPolicy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown policy {name!r}. Choices: {list(REGISTRY)}")
    return REGISTRY[name](**kwargs)


__all__ = [
    "CompactionPolicy", "CompactionContext", "NoCompaction",
    "NaiveSummary", "Microcompact", "PrefixPreserving", "BoundaryAware",
    "REGISTRY", "build_policy",
]
