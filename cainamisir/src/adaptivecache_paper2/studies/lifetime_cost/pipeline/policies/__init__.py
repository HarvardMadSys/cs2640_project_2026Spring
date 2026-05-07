"""Compaction policies. Each is a callable: compact(messages, ctx) -> messages."""

from .base import CompactionPolicy, CompactionContext, NoCompaction
from .naive_summary import NaiveSummary
from .microcompact import Microcompact
from .prefix_preserving import PrefixPreserving
from .boundary_aware import BoundaryAware
from .position_aware import PositionAware


REGISTRY = {
    "none": NoCompaction,
    "naive_summary": NaiveSummary,
    "microcompact": Microcompact,
    "prefix_preserving": PrefixPreserving,
    "boundary_aware": BoundaryAware,
    "position_aware": PositionAware,
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
