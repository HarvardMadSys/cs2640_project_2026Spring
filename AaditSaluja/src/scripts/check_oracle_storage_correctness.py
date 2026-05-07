#!/usr/bin/env python3
"""Smoke-check hybrid storage read/write/delete correctness on a local backend."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO = SCRIPT_PATH.parents[2] if SCRIPT_PATH.parent.parent.name == "src" else SCRIPT_PATH.parents[1]
sys.path.insert(0, str(REPO / "src"))

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.benchmark_runner import BackendConfig, open_backend  # noqa: E402
from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import build_policy, PolicyConfig  # noqa: E402
from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.storage import StorageConfig, build_storage  # noqa: E402


def check(cold_data_backend: str, storage_name: str = "oracle_cold_segments") -> None:
    root = Path(tempfile.mkdtemp(prefix=f"cs2640-{storage_name}-{cold_data_backend}-"))
    backend_config = BackendConfig(backend="simulated-ceph", conffile="/etc/ceph/ceph.conf")
    options = {
        "hot_prefixes": "hot",
        "virtual_cold_dirs": "true",
        "cold_data_backend": cold_data_backend,
        "cold_data_batch_bytes": "1024",
        "index_batch_records": "2",
    }
    if storage_name == "predictive_cold_segments":
        options.update(
            {
                "promotion_threshold": "1",
                "promotion_triggers": "read,stat",
            }
        )
    storage = build_storage(
        storage_name,
        StorageConfig(
            segment_size=1024 * 1024,
            options=options,
        ),
    )
    policy = build_policy("none", PolicyConfig())
    hot_path = str(root / "hot0000" / "hot.dat")
    cold_path = str(root / "cold0000" / "cold.dat")
    hot_payload = b"hot-native-payload"
    cold_payload = b"cold-packed-payload"

    backend = open_backend(backend_config)
    try:
        storage.prepare(
            backend,
            str(root),
            [str(root), str(root / "hot0000"), str(root / "cold0000")],
            policy,
        )
        storage.write_file(backend, hot_path, hot_payload)
        storage.write_file(backend, cold_path, cold_payload)
        storage.sync(backend)
        assert storage.read_file(backend, hot_path, len(hot_payload)) == hot_payload
        assert storage.read_file(backend, cold_path, len(cold_payload)) == cold_payload
        cold_stat = storage.stat(backend, cold_path)
        cold_size = cold_stat["size"] if isinstance(cold_stat, dict) else cold_stat.st_size
        assert cold_size == len(cold_payload)
        storage.unlink(backend, hot_path)
        storage.unlink(backend, cold_path)
        storage.sync(backend)
        storage.cleanup(backend, [str(root / "hot0000"), str(root / "cold0000"), str(root)])
    finally:
        backend.close()
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    check("cephfs")
    check("rados")
    check("cephfs", "predictive_cold_segments")
    print("hybrid storage correctness smoke passed for oracle and predictive backends")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
