"""Experiment configuration for SWE-bench runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SWEConfig:
    """Configuration for a SWE-bench experiment run."""

    # Dataset
    dataset: str = "lite"
    """SWE-bench subset: 'lite', 'verified', 'full', or HuggingFace dataset path."""
    split: str = "dev"
    """Dataset split."""
    instance_ids: list[str] = field(default_factory=list)
    """Specific instance IDs to run (empty = all)."""
    slice_spec: str = ""
    """Slice specification (e.g., '0:5' for first 5 instances)."""

    # Model
    model_name: str = "anthropic/claude-sonnet-4-5-20250929"
    """LiteLLM model name (include provider prefix)."""

    # AdaptiveCache
    cache_policy: str = "adaptive"
    """Context management policy: 'adaptive', 'fifo', 'lru', 'window', 'none'."""
    cache_budget: int = 64_000
    """Soft token budget for context management."""

    # Agent
    max_steps: int = 30
    """Maximum agent steps per instance."""
    cost_limit: float = 3.0
    """Maximum cost per instance in USD."""

    # Execution
    workers: int = 1
    """Number of parallel workers."""
    output_dir: str = "results"
    """Output directory for predictions and traces."""
    run_id: str = ""
    """Run identifier (auto-generated if empty)."""

    def to_mini_swe_config(self) -> dict:
        """Convert to mini-swe-agent config dict format."""
        return {
            "model": {
                "model_class": "harness.swe_model.AdaptiveCacheModel",
                "model_name": self.model_name,
                "cache_policy": self.cache_policy,
                "cache_budget": self.cache_budget,
                "cost_tracking": "ignore_errors",
            },
            "agent": {
                "step_limit": self.max_steps,
                "cost_limit": self.cost_limit,
            },
        }
