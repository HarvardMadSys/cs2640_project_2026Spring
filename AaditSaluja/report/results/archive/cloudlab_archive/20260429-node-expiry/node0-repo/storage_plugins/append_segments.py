"""Append-only segment packing layer plugin."""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.storage import AppendSegmentSmallFileStorage, StorageConfig


def create_storage(config):
    tuned = StorageConfig(
        segment_size=int(config.options.get("segment_size", config.segment_size)),
        options=config.options,
    )
    return AppendSegmentSmallFileStorage(tuned)
