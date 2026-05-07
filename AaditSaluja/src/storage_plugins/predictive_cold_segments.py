"""Predictive hybrid cold-packing plugin.

Packs new files first, then promotes repeatedly accessed paths back into native
files without using oracle hot/cold path labels for decisions.
"""

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.storage import PredictiveColdSegmentStorage, StorageConfig


def create_storage(config):
    tuned = StorageConfig(
        segment_size=int(config.options.get("segment_size", config.segment_size)),
        options=config.options,
    )
    return PredictiveColdSegmentStorage(tuned)
