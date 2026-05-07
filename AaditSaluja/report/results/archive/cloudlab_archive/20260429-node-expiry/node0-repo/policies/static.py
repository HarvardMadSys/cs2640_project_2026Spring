"""Explicit static subtree-pinning policy plugin."""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import StaticSubtreePinningPolicy


def create_policy(config):
    return StaticSubtreePinningPolicy(config)
