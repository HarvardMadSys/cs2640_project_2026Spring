"""Sliding-window hot-directory placement policy plugin."""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import PredictiveHotDirectoryPolicy


def create_policy(config):
    return PredictiveHotDirectoryPolicy(config)
