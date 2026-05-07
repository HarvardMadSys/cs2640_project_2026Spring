"""Oracle-guided hybrid packing plugin.

Keeps known-hot paths native and packs only cold/bulk paths into append-only
segments with a compact batched binary index.
"""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.storage import OracleColdSegmentStorage, StorageConfig


def create_storage(config):
    tuned = StorageConfig(
        segment_size=int(config.options.get("segment_size", config.segment_size)),
        options=config.options,
    )
    return OracleColdSegmentStorage(tuned)
