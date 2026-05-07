"""Example policy plugin for quick iteration.

This plugin currently reuses the built-in predictive policy with slightly more
aggressive defaults. Copy this file when trying another heuristic.
"""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PolicyConfig, PredictiveHotDirectoryPolicy


def create_policy(config):
    tuned = PolicyConfig(
        pin_ranks=config.pin_ranks,
        hot_window=int(config.options.get("hot_window", config.hot_window)),
        hot_threshold=int(config.options.get("hot_threshold", max(8, config.hot_threshold // 2))),
        hot_min_interval=int(config.options.get("hot_min_interval", config.hot_min_interval)),
        options=config.options,
    )
    return PredictiveHotDirectoryPolicy(tuned)
