"""Load pricing.yaml and compute lifetime cost from a Trajectory.

The price sheet is the single source of truth for $$ math. To add a model,
edit pricing.yaml — no code change required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import yaml

from .types import LifetimeCost, Trajectory


PRICING_YAML = Path(__file__).resolve().parent.parent / "pricing.yaml"


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token prices."""

    name: str
    input_uncached: float
    input_cached: float
    output: float
    cache_write: float
    tokenizer: str


class PriceSheet:
    """Lazy-loaded mapping name -> ModelPrice."""

    def __init__(self, path: Path = PRICING_YAML):
        with open(path) as f:
            data = yaml.safe_load(f)
        self._models: Dict[str, ModelPrice] = {}
        for name, spec in data["models"].items():
            self._models[name] = ModelPrice(
                name=name,
                input_uncached=float(spec["input_uncached"]),
                input_cached=float(spec["input_cached"]),
                output=float(spec["output"]),
                cache_write=float(spec.get("cache_write", 0.0)),
                tokenizer=spec.get("tokenizer", "tiktoken:cl100k_base"),
            )

    def __getitem__(self, name: str) -> ModelPrice:
        if name not in self._models:
            raise KeyError(f"Unknown model {name!r}; add it to pricing.yaml")
        return self._models[name]

    def __contains__(self, name: str) -> bool:
        return name in self._models

    def names(self):
        return list(self._models.keys())


def cost_of(traj: Trajectory, sheet: PriceSheet, *, override_model: str | None = None) -> LifetimeCost:
    """Compute the lifetime cost of a trajectory under a price sheet entry.

    `override_model` lets you re-cost a trajectory recorded against model A
    using model B's prices. This is the move that makes the study
    model-agnostic: we cost the *same* trajectory under multiple price
    sheets to show the policy ranking is invariant.
    """
    name = override_model or traj.model
    price = sheet[name]

    uncached = sum(s.usage.uncached_prompt for s in traj.steps)
    cached = sum(s.usage.cached_tokens for s in traj.steps)
    output = sum(s.usage.completion_tokens for s in traj.steps)
    cache_write = sum(s.usage.cache_write_tokens for s in traj.steps)

    # Compaction call cost: input is cached (the messages we're summarizing
    # are still in the cache from the just-completed agent step), plus a small
    # uncached "now summarize" instruction, plus the output.
    comp_in_cached = sum(
        s.compaction_after.compaction_input_cached_tokens
        for s in traj.steps if s.compaction_after is not None
    )
    comp_in_uncached = sum(
        s.compaction_after.compaction_input_uncached_tokens
        for s in traj.steps if s.compaction_after is not None
    )
    comp_out = sum(
        s.compaction_after.compaction_output_tokens
        for s in traj.steps if s.compaction_after is not None
    )
    # Legacy fallback: if a CompactionEvent only set the old aggregate field,
    # treat it as fully uncached input (worst-case, matches old semantics).
    legacy_total = sum(
        s.compaction_after.compaction_call_tokens
        for s in traj.steps if s.compaction_after is not None
    ) - (comp_in_cached + comp_in_uncached + comp_out)
    legacy_total = max(legacy_total, 0)

    M = 1_000_000.0
    return LifetimeCost(
        model=name,
        input_uncached_dollars=uncached * price.input_uncached / M,
        input_cached_dollars=cached * price.input_cached / M,
        output_dollars=output * price.output / M,
        cache_write_dollars=cache_write * price.cache_write / M,
        compaction_dollars=(
            comp_in_cached   * price.input_cached
          + comp_in_uncached * price.input_uncached
          + comp_out         * price.output
          + legacy_total     * price.input_uncached
        ) / M,
    )


def cost_per_resolved(trajs: list[Trajectory], sheet: PriceSheet, *, override_model: str | None = None) -> float:
    """$ per resolved instance. Returns inf if nothing resolved."""
    resolved = [t for t in trajs if t.resolved]
    if not resolved:
        return float("inf")
    total = sum(cost_of(t, sheet, override_model=override_model).total for t in trajs)
    return total / len(resolved)
