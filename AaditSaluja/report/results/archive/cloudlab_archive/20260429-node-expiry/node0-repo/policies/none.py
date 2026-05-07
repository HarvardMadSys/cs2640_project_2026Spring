"""Native CephFS metadata behavior."""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import NoopPolicy


def create_policy(config):
    del config
    return NoopPolicy()
