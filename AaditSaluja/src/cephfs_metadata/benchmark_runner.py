"""Benchmark runner for CephFS metadata experiments.

The runner intentionally keeps dependencies to the Python standard library so it
can run inside the Ceph daemon container used for local development.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Protocol

from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.policies import MetadataPolicy, PolicyConfig, build_policy
from cs2640_project_2026Spring.AaditSaluja.src.cephfs_metadata.storage import StorageConfig, StorageLayer, build_storage


class FsBackend(Protocol):
    def close(self) -> None:
        ...

    def mkdirs(self, path: str) -> None:
        ...

    def rmdir(self, path: str) -> None:
        ...

    def write_file(self, path: str, data: bytes) -> None:
        ...

    def write_at(self, path: str, data: bytes, offset: int) -> None:
        ...

    def read_file(self, path: str, size: int) -> bytes:
        ...

    def read_at(self, path: str, offset: int, size: int) -> bytes:
        ...

    def stat(self, path: str) -> object:
        ...

    def unlink(self, path: str) -> None:
        ...

    def set_pin(self, path: str, rank: int) -> None:
        ...

    def set_xattr(self, path: str, name: str, value: bytes) -> None:
        ...

    def write_object(self, pool: str, object_id: str, data: bytes, offset: int) -> None:
        ...

    def read_object(self, pool: str, object_id: str, offset: int, size: int) -> bytes:
        ...

    def remove_object(self, pool: str, object_id: str) -> None:
        ...

    def object_backend_available(self) -> bool:
        ...


class PosixBackend:
    _object_root = Path("/tmp/cs2640-rados-sim")

    def close(self) -> None:
        return None

    def mkdirs(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def rmdir(self, path: str) -> None:
        Path(path).rmdir()

    def write_file(self, path: str, data: bytes) -> None:
        with open(path, "wb") as handle:
            handle.write(data)

    def write_at(self, path: str, data: bytes, offset: int) -> None:
        mode = "r+b" if Path(path).exists() else "w+b"
        with open(path, mode) as handle:
            handle.seek(offset)
            handle.write(data)

    def read_file(self, path: str, size: int) -> bytes:
        with open(path, "rb") as handle:
            return handle.read(size)

    def read_at(self, path: str, offset: int, size: int) -> bytes:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read(size)

    def stat(self, path: str) -> object:
        return os.stat(path)

    def unlink(self, path: str) -> None:
        os.unlink(path)

    def set_pin(self, path: str, rank: int) -> None:
        try:
            os.setxattr(path, b"ceph.dir.pin", str(rank).encode("ascii"))
        except OSError as exc:
            raise RuntimeError(f"could not set ceph.dir.pin on {path}: {exc}") from exc

    def set_xattr(self, path: str, name: str, value: bytes) -> None:
        attr_name = name.encode("ascii")
        try:
            os.setxattr(path, attr_name, value)
        except OSError as exc:
            raise RuntimeError(f"could not set {name} on {path}: {exc}") from exc

    def write_object(self, pool: str, object_id: str, data: bytes, offset: int) -> None:
        path = self._object_path(pool, object_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "r+b" if path.exists() else "w+b"
        with path.open(mode) as handle:
            handle.seek(offset)
            handle.write(data)

    def read_object(self, pool: str, object_id: str, offset: int, size: int) -> bytes:
        with self._object_path(pool, object_id).open("rb") as handle:
            handle.seek(offset)
            return handle.read(size)

    def remove_object(self, pool: str, object_id: str) -> None:
        try:
            self._object_path(pool, object_id).unlink()
        except FileNotFoundError:
            pass

    def object_backend_available(self) -> bool:
        return True

    def _object_path(self, pool: str, object_id: str) -> Path:
        digest = hashlib.sha1(object_id.encode("utf-8")).hexdigest()
        return self._object_root / pool / digest


class SimulatedCephBackend(PosixBackend):
    """POSIX correctness backend with configurable Ceph-like operation costs."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config

    def mkdirs(self, path: str) -> None:
        self._metadata_delay()
        super().mkdirs(path)

    def rmdir(self, path: str) -> None:
        self._metadata_delay()
        super().rmdir(path)

    def write_file(self, path: str, data: bytes) -> None:
        self._delay(self.config.sim_file_create_us + self._data_us(len(data)))
        super().write_file(path, data)

    def write_at(self, path: str, data: bytes, offset: int) -> None:
        self._delay(self.config.sim_file_write_us + self._data_us(len(data)))
        super().write_at(path, data, offset)

    def read_file(self, path: str, size: int) -> bytes:
        self._delay(self.config.sim_file_read_us + self._data_us(size))
        return super().read_file(path, size)

    def read_at(self, path: str, offset: int, size: int) -> bytes:
        self._delay(self.config.sim_file_read_us + self._data_us(size))
        return super().read_at(path, offset, size)

    def stat(self, path: str) -> object:
        self._delay(self.config.sim_stat_us)
        return super().stat(path)

    def unlink(self, path: str) -> None:
        self._metadata_delay()
        super().unlink(path)

    def set_pin(self, path: str, rank: int) -> None:
        self._delay(self.config.sim_xattr_us)
        super().set_pin(path, rank)

    def set_xattr(self, path: str, name: str, value: bytes) -> None:
        self._delay(self.config.sim_xattr_us)
        super().set_xattr(path, name, value)

    def write_object(self, pool: str, object_id: str, data: bytes, offset: int) -> None:
        self._delay(self.config.sim_object_write_us / self._osd_scale() + self._data_us(len(data)))
        super().write_object(pool, object_id, data, offset)

    def read_object(self, pool: str, object_id: str, offset: int, size: int) -> bytes:
        self._delay(self.config.sim_object_read_us / self._osd_scale() + self._data_us(size))
        return super().read_object(pool, object_id, offset, size)

    def remove_object(self, pool: str, object_id: str) -> None:
        self._delay(self.config.sim_object_remove_us / self._osd_scale())
        super().remove_object(pool, object_id)

    def _metadata_delay(self) -> None:
        self._delay(self.config.sim_metadata_us / self._mds_scale())

    def _data_us(self, byte_count: int) -> float:
        if byte_count <= 0 or self.config.sim_data_mib_per_sec <= 0:
            return 0.0
        return (byte_count / (1024 * 1024)) / self.config.sim_data_mib_per_sec * 1_000_000

    def _mds_scale(self) -> int:
        return max(1, self.config.sim_mds_ranks)

    def _osd_scale(self) -> int:
        return max(1, self.config.sim_osd_ranks)

    def _delay(self, microseconds: float) -> None:
        if microseconds > 0:
            time.sleep(microseconds / 1_000_000)


class CephLibBackend:
    def __init__(self, conffile: str) -> None:
        import cephfs  # type: ignore[import-not-found]

        self.fs = cephfs.LibCephFS(conffile=conffile)
        self.fs.mount()
        self._ioctxs: dict[str, object] = {}
        try:
            import rados  # type: ignore[import-not-found]
        except Exception:
            self._rados = None
        else:
            cluster = rados.Rados(conffile=conffile)
            cluster.connect()
            self._rados = cluster

    def close(self) -> None:
        for ioctx in self._ioctxs.values():
            try:
                ioctx.close()
            except Exception:
                pass
        if self._rados is not None:
            self._rados.shutdown()
        self.fs.shutdown()

    def mkdirs(self, path: str) -> None:
        self.fs.mkdirs(path, 0o755)

    def rmdir(self, path: str) -> None:
        self.fs.rmdir(path)

    def write_file(self, path: str, data: bytes) -> None:
        fd = self.fs.open(path, "w", 0o644)
        try:
            if data:
                self.fs.write(fd, data, 0)
        finally:
            self.fs.close(fd)

    def write_at(self, path: str, data: bytes, offset: int) -> None:
        fd = self.fs.open(path, "r+", 0o644)
        try:
            if data:
                self.fs.write(fd, data, offset)
        finally:
            self.fs.close(fd)

    def read_file(self, path: str, size: int) -> bytes:
        fd = self.fs.open(path, "r", 0)
        try:
            return self.fs.read(fd, 0, size)
        finally:
            self.fs.close(fd)

    def read_at(self, path: str, offset: int, size: int) -> bytes:
        fd = self.fs.open(path, "r", 0)
        try:
            return self.fs.read(fd, offset, size)
        finally:
            self.fs.close(fd)

    def stat(self, path: str) -> object:
        return self.fs.stat(path)

    def unlink(self, path: str) -> None:
        self.fs.unlink(path)

    def set_pin(self, path: str, rank: int) -> None:
        self.fs.setxattr(path, "ceph.dir.pin", str(rank).encode("ascii"), 0)

    def set_xattr(self, path: str, name: str, value: bytes) -> None:
        self.fs.setxattr(path, name, value, 0)

    def write_object(self, pool: str, object_id: str, data: bytes, offset: int) -> None:
        ioctx = self._ioctx(pool)
        ioctx.write(object_id, data, offset=offset)

    def read_object(self, pool: str, object_id: str, offset: int, size: int) -> bytes:
        return self._ioctx(pool).read(object_id, length=size, offset=offset)

    def remove_object(self, pool: str, object_id: str) -> None:
        try:
            self._ioctx(pool).remove_object(object_id)
        except Exception:
            pass

    def object_backend_available(self) -> bool:
        return self._rados is not None

    def _ioctx(self, pool: str) -> object:
        if self._rados is None:
            raise RuntimeError("python rados binding is not available")
        ioctx = self._ioctxs.get(pool)
        if ioctx is None:
            ioctx = self._rados.open_ioctx(pool)
            self._ioctxs[pool] = ioctx
        return ioctx


@dataclass(frozen=True)
class BackendConfig:
    backend: str
    conffile: str
    sim_metadata_us: float = 700.0
    sim_file_create_us: float = 1200.0
    sim_file_write_us: float = 250.0
    sim_file_read_us: float = 180.0
    sim_stat_us: float = 250.0
    sim_xattr_us: float = 800.0
    sim_object_write_us: float = 180.0
    sim_object_read_us: float = 160.0
    sim_object_remove_us: float = 160.0
    sim_data_mib_per_sec: float = 300.0
    sim_mds_ranks: int = 1
    sim_osd_ranks: int = 1


@dataclass
class PhaseResult:
    phase: str
    operation: str
    count: int
    total_seconds: float
    latencies_ms: list[float]

    def to_row(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "operation": self.operation,
            "count": self.count,
            "total_seconds": round(self.total_seconds, 6),
            "ops_per_sec": round(self.count / self.total_seconds, 3)
            if self.total_seconds > 0
            else 0,
            "latency_mean_ms": round(mean(self.latencies_ms), 6),
            "latency_p50_ms": round(percentile(self.latencies_ms, 50), 6),
            "latency_p95_ms": round(percentile(self.latencies_ms, 95), 6),
            "latency_p99_ms": round(percentile(self.latencies_ms, 99), 6),
        }


@dataclass(frozen=True)
class WorkloadConfig:
    name: str
    file_count: int
    file_size: int
    workers: int
    depth: int
    branching: int
    dirs: int
    ops: int
    seed: int
    pin_ranks: tuple[int, ...]
    keep_data: bool = False
    oracle_cold_fraction: float = 0.875
    oracle_cold_access_fraction: float = 0.0
    ycsb_distribution: str = "zipfian"
    ycsb_update_fraction: float = 0.2
    ycsb_hot_fraction: float = 0.2
    ycsb_hot_op_fraction: float = 0.8
    ycsb_zipf_alpha: float = 0.99


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[int(index)]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def open_backend(config: BackendConfig) -> FsBackend:
    if config.backend == "posix":
        return PosixBackend()
    if config.backend == "simulated-ceph":
        return SimulatedCephBackend(config)
    if config.backend == "ceph-libcephfs":
        return CephLibBackend(config.conffile)
    raise ValueError(f"unknown backend: {config.backend}")


def timed_phase(
    phase: str,
    operation: str,
    workers: int,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    items: list[str],
    func: Callable[[FsBackend, str], None],
) -> PhaseResult:
    def run_chunk(chunk: list[str]) -> list[float]:
        backend = open_backend(backend_config)
        latencies: list[float] = []
        try:
            policy_backend = storage.policy_backend(backend)
            for item in chunk:
                start = time.perf_counter()
                policy.before_operation(policy_backend, operation, item)
                func(backend, item)
                latencies.append((time.perf_counter() - start) * 1000.0)
        finally:
            backend.close()
        return latencies

    chunks = split_evenly(items, max(1, workers))
    start = time.perf_counter()
    if workers <= 1:
        latencies = run_chunk(items)
    else:
        latencies = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for chunk_latencies in executor.map(run_chunk, chunks):
                latencies.extend(chunk_latencies)
    sync_backend = open_backend(backend_config)
    try:
        storage.sync(sync_backend)
    finally:
        sync_backend.close()
    total = time.perf_counter() - start
    return PhaseResult(
        phase=phase,
        operation=operation,
        count=len(items),
        total_seconds=total,
        latencies_ms=latencies,
    )


def split_evenly(items: list[str], chunks: int) -> list[list[str]]:
    if chunks <= 1 or len(items) <= 1:
        return [items]
    return [items[index::chunks] for index in range(chunks) if items[index::chunks]]


def payload(size: int) -> bytes:
    if size <= 0:
        return b""
    pattern = b"cs2640-cephfs-metadata-benchmark\n"
    repeats = (size // len(pattern)) + 1
    return (pattern * repeats)[:size]


def mdtest_dirs(root: str, depth: int, branching: int) -> list[str]:
    if depth <= 0:
        return [root]
    current = [root]
    all_dirs: list[str] = []
    for level in range(depth):
        next_level = []
        for parent in current:
            for branch in range(branching):
                child = f"{parent}/d{level}_{branch}"
                all_dirs.append(child)
                next_level.append(child)
        current = next_level
    return all_dirs or [root]


def files_across_dirs(dirs: list[str], count: int, prefix: str = "f") -> list[str]:
    return [f"{dirs[index % len(dirs)]}/{prefix}{index:08d}.dat" for index in range(count)]


def choose_paths_with_cold_fraction(
    rng: random.Random,
    hot_files: list[str],
    cold_files: list[str],
    count: int,
    cold_access_fraction: float,
) -> list[str]:
    paths: list[str] = []
    cold_fraction = max(0.0, min(1.0, cold_access_fraction))
    for _ in range(count):
        use_cold = cold_files and rng.random() < cold_fraction
        candidates = cold_files if use_cold else hot_files
        paths.append(candidates[rng.randrange(len(candidates))])
    return paths


def weighted_cdf(weights: list[float]) -> list[float]:
    total = 0.0
    cdf = []
    for weight in weights:
        total += max(0.0, weight)
        cdf.append(total)
    if total <= 0:
        return [float(index + 1) for index in range(len(weights))]
    return cdf


def choose_ycsb_paths(
    rng: random.Random,
    hot_files: list[str],
    cold_files: list[str],
    count: int,
    distribution: str,
    hot_op_fraction: float,
    zipf_alpha: float,
) -> list[str]:
    hot = hot_files or cold_files
    cold = cold_files or hot_files
    if not hot:
        return []
    distribution = distribution.lower()
    if distribution == "hotspot":
        paths = []
        hot_probability = max(0.0, min(1.0, hot_op_fraction))
        for _ in range(count):
            candidates = hot if rng.random() < hot_probability else cold
            paths.append(candidates[rng.randrange(len(candidates))])
        return paths
    if distribution != "zipfian":
        raise ValueError(f"unknown ycsb distribution: {distribution}")

    ranked_hot = list(hot)
    ranked_cold = list(cold)
    rng.shuffle(ranked_hot)
    rng.shuffle(ranked_cold)
    ranked_files = ranked_hot + ranked_cold
    weights = [
        1.0 / ((rank + 1) ** max(0.0, zipf_alpha))
        for rank in range(len(ranked_files))
    ]
    cdf = weighted_cdf(weights)
    total = cdf[-1]
    paths = []
    for _ in range(count):
        index = bisect.bisect_left(cdf, rng.random() * total)
        paths.append(ranked_files[min(index, len(ranked_files) - 1)])
    return paths


def prepare_storage(
    backend_config: BackendConfig,
    root: str,
    dirs: list[str],
    policy: MetadataPolicy,
    storage: StorageLayer,
) -> None:
    backend = open_backend(backend_config)
    try:
        storage.prepare(backend, root, dirs, policy)
    finally:
        backend.close()


def cleanup_storage(
    backend_config: BackendConfig, dirs: Iterable[str], storage: StorageLayer
) -> None:
    backend = open_backend(backend_config)
    try:
        storage.cleanup(backend, dirs)
    finally:
        backend.close()


def run_mdtest_like(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    dirs = mdtest_dirs(root, config.depth, config.branching)
    files = files_across_dirs(dirs, config.file_count)
    data = payload(config.file_size)
    prepare_storage(backend_config, root, [root] + dirs, policy, storage)
    results = [
        timed_phase(
            config.name,
            "create",
            config.workers,
            backend_config,
            policy,
            storage,
            files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
        timed_phase(
            config.name,
            "stat",
            config.workers,
            backend_config,
            policy,
            storage,
            files,
            lambda backend, path: storage.stat(backend, path),
        ),
        timed_phase(
            config.name,
            "read",
            config.workers,
            backend_config,
            policy,
            storage,
            files,
            lambda backend, path: storage.read_file(backend, path, config.file_size),
        ),
    ]
    if not config.keep_data:
        results.append(
            timed_phase(
                config.name,
                "delete",
                config.workers,
                backend_config,
                policy,
                storage,
                files,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        cleanup_storage(backend_config, dirs + [root], storage)
    return results


def run_hotdirs(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    dirs = [f"{root}/dir{index:04d}" for index in range(config.dirs)]
    prepare_storage(backend_config, root, [root] + dirs, policy, storage)
    rng = random.Random(config.seed)
    hot_files = []
    for index in range(config.file_count):
        if rng.random() < 0.8:
            directory = dirs[0]
        else:
            directory = dirs[1 + rng.randrange(max(1, len(dirs) - 1))]
        hot_files.append(f"{directory}/f{index:08d}.dat")
    data = payload(config.file_size)
    results = [
        timed_phase(
            config.name,
            "skewed_create",
            config.workers,
            backend_config,
            policy,
            storage,
            hot_files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
        timed_phase(
            config.name,
            "skewed_stat",
            config.workers,
            backend_config,
            policy,
            storage,
            hot_files,
            lambda backend, path: storage.stat(backend, path),
        ),
    ]
    if not config.keep_data:
        results.append(
            timed_phase(
                config.name,
                "skewed_delete",
                config.workers,
                backend_config,
                policy,
                storage,
                hot_files,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        cleanup_storage(backend_config, dirs + [root], storage)
    return results


def run_ycsb_file_skew(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    hot_dir_count = max(1, min(4, max(1, config.dirs // 8)))
    cold_dir_count = max(1, config.dirs - hot_dir_count)
    hot_dirs = [f"{root}/hot{index:04d}" for index in range(hot_dir_count)]
    cold_dirs = [f"{root}/cold{index:04d}" for index in range(cold_dir_count)]
    all_dirs = hot_dirs + cold_dirs
    prepare_storage(backend_config, root, [root] + all_dirs, policy, storage)

    if config.file_count <= 1:
        hot_file_count = max(1, config.file_count)
        cold_file_count = 0
    else:
        hot_fraction = max(0.0, min(1.0, config.ycsb_hot_fraction))
        hot_file_count = int(round(config.file_count * hot_fraction))
        hot_file_count = max(1, min(config.file_count - 1, hot_file_count))
        cold_file_count = config.file_count - hot_file_count
    hot_files = files_across_dirs(hot_dirs, hot_file_count, prefix="hot")
    cold_files = files_across_dirs(cold_dirs, cold_file_count, prefix="cold")

    rng = random.Random(config.seed)
    create_files = hot_files + cold_files
    rng.shuffle(create_files)
    read_count = max(1, int(round(config.ops * (1.0 - config.ycsb_update_fraction))))
    update_count = max(1, config.ops - read_count)
    read_paths = choose_ycsb_paths(
        rng,
        hot_files,
        cold_files,
        read_count,
        config.ycsb_distribution,
        config.ycsb_hot_op_fraction,
        config.ycsb_zipf_alpha,
    )
    update_paths = choose_ycsb_paths(
        rng,
        hot_files,
        cold_files,
        update_count,
        config.ycsb_distribution,
        config.ycsb_hot_op_fraction,
        config.ycsb_zipf_alpha,
    )
    data = payload(config.file_size)
    update_data = payload(config.file_size)

    results = [
        timed_phase(
            config.name,
            "ycsb_load_create",
            config.workers,
            backend_config,
            policy,
            storage,
            create_files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
        timed_phase(
            config.name,
            f"ycsb_{config.ycsb_distribution}_read",
            config.workers,
            backend_config,
            policy,
            storage,
            read_paths,
            lambda backend, path: storage.read_file(backend, path, config.file_size),
        ),
        timed_phase(
            config.name,
            f"ycsb_{config.ycsb_distribution}_update",
            config.workers,
            backend_config,
            policy,
            storage,
            update_paths,
            lambda backend, path: storage.write_file(backend, path, update_data),
        ),
    ]
    if not config.keep_data:
        results.append(
            timed_phase(
                config.name,
                "cleanup_delete",
                config.workers,
                backend_config,
                policy,
                storage,
                hot_files + cold_files,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        cleanup_storage(backend_config, all_dirs + [root], storage)
    return results


def run_predictor_false_hot_churn(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    """Stress the cost of false hot-directory classification.

    The workload first creates and repeatedly reads files in generator-cold
    directories, causing the directory-hotset predictor to mark those cold
    directories hot. It then creates a large wave of new files in those same
    cold directories. A correct oracle still packs them; the predictor writes
    them natively after the false-hot classification.
    """

    hot_dir_count = max(1, min(4, max(1, config.dirs // 8)))
    cold_dir_count = max(1, config.dirs - hot_dir_count)
    hot_dirs = [f"{root}/hot{index:04d}" for index in range(hot_dir_count)]
    cold_dirs = [f"{root}/cold{index:04d}" for index in range(cold_dir_count)]
    all_dirs = hot_dirs + cold_dirs
    prepare_storage(backend_config, root, [root] + all_dirs, policy, storage)

    data = payload(config.file_size)
    probe_count = max(config.file_count, len(cold_dirs) * 4)
    probe_files = files_across_dirs(cold_dirs, probe_count, prefix="probe")

    rng = random.Random(config.seed)
    trigger_paths: list[str] = []
    for directory in cold_dirs:
        directory_files = [
            path
            for path in probe_files
            if str(PurePosixPath(path).parent) == directory
        ]
        if len(directory_files) < 3:
            continue
        rng.shuffle(directory_files)
        chosen = directory_files[:3]
        for access_index in range(8):
            trigger_paths.append(chosen[access_index % len(chosen)])
    rng.shuffle(trigger_paths)

    churn_count = max(config.ops, config.file_count)
    churn_files = [
        f"{cold_dirs[index % len(cold_dirs)]}/falsehot{index:08d}.dat"
        for index in range(churn_count)
    ]

    results = [
        timed_phase(
            config.name,
            "false_hot_probe_create",
            config.workers,
            backend_config,
            policy,
            storage,
            probe_files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
        timed_phase(
            config.name,
            "false_hot_probe_read",
            config.workers,
            backend_config,
            policy,
            storage,
            trigger_paths,
            lambda backend, path: storage.read_file(backend, path, config.file_size),
        ),
        timed_phase(
            config.name,
            "false_hot_churn_create",
            config.workers,
            backend_config,
            policy,
            storage,
            churn_files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
    ]
    if not config.keep_data:
        results.append(
            timed_phase(
                config.name,
                "cleanup_delete",
                config.workers,
                backend_config,
                policy,
                storage,
                probe_files + churn_files,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        cleanup_storage(backend_config, all_dirs + [root], storage)
    return results


def run_oracle_hotcold_mix(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    hot_dir_count = max(1, min(4, max(1, config.dirs // 8)))
    cold_dir_count = max(1, max(0, config.dirs - hot_dir_count))
    hot_dirs = [f"{root}/hot{index:04d}" for index in range(hot_dir_count)]
    cold_dirs = [f"{root}/cold{index:04d}" for index in range(cold_dir_count)]
    all_dirs = hot_dirs + cold_dirs
    prepare_storage(backend_config, root, [root] + all_dirs, policy, storage)

    if config.file_count <= 1:
        hot_file_count = max(1, config.file_count)
        cold_file_count = 0
    else:
        cold_fraction = max(0.0, min(1.0, config.oracle_cold_fraction))
        cold_file_count = int(round(config.file_count * cold_fraction))
        cold_file_count = max(1, min(config.file_count - 1, cold_file_count))
        hot_file_count = config.file_count - cold_file_count
    hot_files = files_across_dirs(hot_dirs, hot_file_count, prefix="hot")
    cold_files = files_across_dirs(cold_dirs, cold_file_count, prefix="cold")

    rng = random.Random(config.seed)
    create_files = hot_files + cold_files
    rng.shuffle(create_files)
    data = payload(config.file_size)

    hot_meta_ops = max(config.ops, hot_file_count * 8)
    hot_read_ops = max(1, hot_meta_ops // 4)
    hot_churn_count = max(1, hot_meta_ops // 8)
    access_stat_paths = choose_paths_with_cold_fraction(
        rng,
        hot_files,
        cold_files,
        hot_meta_ops,
        config.oracle_cold_access_fraction,
    )
    access_read_paths = choose_paths_with_cold_fraction(
        rng,
        hot_files,
        cold_files,
        hot_read_ops,
        config.oracle_cold_access_fraction,
    )
    hot_churn_paths = [
        f"{hot_dirs[rng.randrange(len(hot_dirs))]}/burst{index:08d}.dat"
        for index in range(hot_churn_count)
    ]

    results = [
        timed_phase(
            config.name,
            "bulk_create",
            config.workers,
            backend_config,
            policy,
            storage,
            create_files,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
        timed_phase(
            config.name,
            "oracle_access_stat",
            config.workers,
            backend_config,
            policy,
            storage,
            access_stat_paths,
            lambda backend, path: storage.stat(backend, path),
        ),
        timed_phase(
            config.name,
            "oracle_access_read",
            config.workers,
            backend_config,
            policy,
            storage,
            access_read_paths,
            lambda backend, path: storage.read_file(backend, path, config.file_size),
        ),
        timed_phase(
            config.name,
            "oracle_hot_churn_create",
            config.workers,
            backend_config,
            policy,
            storage,
            hot_churn_paths,
            lambda backend, path: storage.write_file(backend, path, data),
        ),
    ]

    if not config.keep_data:
        results.append(
            timed_phase(
                config.name,
                "oracle_hot_churn_delete",
                config.workers,
                backend_config,
                policy,
                storage,
                hot_churn_paths,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        results.append(
            timed_phase(
                config.name,
                "cleanup_delete",
                config.workers,
                backend_config,
                policy,
                storage,
                hot_files + cold_files,
                lambda backend, path: storage.unlink(backend, path),
            )
        )
        cleanup_storage(backend_config, all_dirs + [root], storage)
    return results


def run_varmail_like(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    dirs = [f"{root}/mailbox{index:04d}" for index in range(config.dirs)]
    prepare_storage(backend_config, root, [root] + dirs, policy, storage)
    rng = random.Random(config.seed)
    data = payload(config.file_size)
    create_paths = [
        f"{dirs[index % len(dirs)]}/msg{index:08d}.eml"
        for index in range(max(1, config.file_count // 2))
    ]
    created: list[str] = []
    backend = open_backend(backend_config)
    create_latencies: list[float] = []
    mixed_latencies: list[float] = []
    delete_latencies: list[float] = []
    try:
        policy_backend = storage.policy_backend(backend)
        phase_start = time.perf_counter()
        for path in create_paths:
            start = time.perf_counter()
            policy.before_operation(policy_backend, "initial_create", path)
            storage.write_file(backend, path, data)
            create_latencies.append((time.perf_counter() - start) * 1000.0)
            created.append(path)
        storage.sync(backend)
        create_total = time.perf_counter() - phase_start

        phase_start = time.perf_counter()
        next_id = len(created)
        live = created[:]
        for _ in range(config.ops):
            action = rng.random()
            start = time.perf_counter()
            if action < 0.45 or not live:
                directory = dirs[rng.randrange(len(dirs))]
                path = f"{directory}/msg{next_id:08d}.eml"
                next_id += 1
                policy.before_operation(policy_backend, "mixed_create", path)
                storage.write_file(backend, path, data)
                live.append(path)
            elif action < 0.80:
                path = live[rng.randrange(len(live))]
                if rng.random() < 0.5:
                    policy.before_operation(policy_backend, "mixed_stat", path)
                    storage.stat(backend, path)
                else:
                    policy.before_operation(policy_backend, "mixed_read", path)
                    storage.read_file(backend, path, config.file_size)
            else:
                index = rng.randrange(len(live))
                path = live.pop(index)
                policy.before_operation(policy_backend, "mixed_delete", path)
                storage.unlink(backend, path)
            mixed_latencies.append((time.perf_counter() - start) * 1000.0)
        storage.sync(backend)
        mixed_total = time.perf_counter() - phase_start

        if config.keep_data:
            delete_total = 0.0
        else:
            phase_start = time.perf_counter()
            for path in live:
                start = time.perf_counter()
                policy.before_operation(policy_backend, "cleanup_delete", path)
                storage.unlink(backend, path)
                delete_latencies.append((time.perf_counter() - start) * 1000.0)
            storage.sync(backend)
            delete_total = time.perf_counter() - phase_start
    finally:
        backend.close()
        if not config.keep_data:
            cleanup_storage(backend_config, dirs + [root], storage)

    results = [
        PhaseResult(config.name, "initial_create", len(create_latencies), create_total, create_latencies),
        PhaseResult(config.name, "mixed_mail_ops", len(mixed_latencies), mixed_total, mixed_latencies),
    ]
    if not config.keep_data:
        results.append(
            PhaseResult(config.name, "cleanup_delete", len(delete_latencies), delete_total, delete_latencies)
        )
    return results


def collect_ceph_stats() -> dict[str, object]:
    if shutil.which("ceph") is None:
        return {"available": False, "reason": "ceph command not found"}
    snapshots: dict[str, object] = {"available": True}
    commands = {
        "df": ["ceph", "df", "--format", "json"],
        "fs_status": ["ceph", "fs", "status", "--format", "json"],
    }
    for name, command in commands.items():
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode == 0:
                snapshots[name] = json.loads(completed.stdout)
            else:
                snapshots[name] = {
                    "error": completed.stderr.strip() or f"exit {completed.returncode}"
                }
        except Exception as exc:
            snapshots[name] = {"error": str(exc)}
    return snapshots


def pool_summary(stats: dict[str, object]) -> dict[str, dict[str, int]]:
    df = stats.get("df")
    if not isinstance(df, dict):
        return {}
    pools = df.get("pools")
    if not isinstance(pools, list):
        return {}
    summary: dict[str, dict[str, int]] = {}
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        name = pool.get("name")
        pool_stats = pool.get("stats")
        if isinstance(name, str) and isinstance(pool_stats, dict):
            summary[name] = {
                "stored": int(pool_stats.get("stored", 0)),
                "objects": int(pool_stats.get("objects", 0)),
                "bytes_used": int(pool_stats.get("bytes_used", 0)),
            }
    return summary


def pool_delta(before: dict[str, object], after: dict[str, object]) -> dict[str, dict[str, int]]:
    before_summary = pool_summary(before)
    after_summary = pool_summary(after)
    delta: dict[str, dict[str, int]] = {}
    for name in sorted(set(before_summary) | set(after_summary)):
        delta[name] = {
            key: after_summary.get(name, {}).get(key, 0)
            - before_summary.get(name, {}).get(key, 0)
            for key in ("stored", "objects", "bytes_used")
        }
    return delta


def hot_dir_count_for_workload(workload: str, dirs: int) -> int:
    if workload in {"oracle_hotcold_mix", "ycsb_file_skew", "predictor_false_hot_churn"}:
        return max(1, min(4, max(1, dirs // 8)))
    return max(1, dirs)


def logical_create_operations(rows: list[dict[str, object]]) -> int:
    return sum(
        int(row["count"])
        for row in rows
        if isinstance(row.get("operation"), str) and "create" in str(row["operation"])
    )


def derive_result_metrics(
    args: argparse.Namespace,
    rows: list[dict[str, object]],
    storage_metrics: dict[str, object],
    ceph_delta: dict[str, dict[str, int]],
) -> dict[str, object]:
    creates = logical_create_operations(rows)
    deletes = sum(
        int(row["count"])
        for row in rows
        if isinstance(row.get("operation"), str) and "delete" in str(row["operation"])
    )
    storage_name = getattr(args, "storage", "native")
    index_bytes = int(
        storage_metrics.get("index_journal_bytes", storage_metrics.get("index_log_bytes", 0))
    )
    segments_created = int(storage_metrics.get("segments_created", 0))
    rados_objects_created = int(storage_metrics.get("rados_objects_created", 0))
    shards_created = int(storage_metrics.get("shards_created", 0))
    total_native_hot_writes = int(storage_metrics.get("total_native_hot_writes", 0))
    packed_records = int(
        storage_metrics.get("packed_cold_records", storage_metrics.get("logical_records", 0))
    )
    hot_dirs = hot_dir_count_for_workload(args.workload, args.dirs)
    uses_index = index_bytes > 0
    index_files = shards_created if shards_created > 0 else (1 if uses_index else 0)

    if storage_name == "native":
        physical_files_created = creates
        materialized_dirs = 1 + int(args.dirs)
    elif storage_name in {"append_segments", "packed"}:
        physical_files_created = segments_created + index_files
        materialized_dirs = 3
    elif storage_name in {"sharded_segments", "sharded_packed"}:
        physical_files_created = segments_created + index_files
        materialized_dirs = 2 + (2 * shards_created)
    elif storage_name in {
        "oracle_cold_segments",
        "hybrid_cold_segments",
        "hybrid_packed",
        "predictive_cold_segments",
        "predictive_hybrid",
        "learned_cold_segments",
    }:
        if storage_metrics.get("cold_data_backend") == "rados":
            physical_files_created = index_files + total_native_hot_writes
        else:
            physical_files_created = segments_created + index_files + total_native_hot_writes
        materialized_native_dirs = int(storage_metrics.get("materialized_native_dirs", 0))
        if materialized_native_dirs > 0:
            materialized_dirs = 1 + materialized_native_dirs + 2
        elif bool(storage_metrics.get("virtual_cold_dirs", False)):
            materialized_dirs = 1 + hot_dirs + 2
        else:
            materialized_dirs = 1 + int(args.dirs) + 2
    else:
        physical_files_created = max(creates, segments_created + index_files)
        materialized_dirs = 1 + int(args.dirs)

    namespace_entries = physical_files_created + materialized_dirs
    derived: dict[str, object] = {
        "logical_create_operations": creates,
        "logical_delete_operations": deletes,
        "physical_files_created_estimate": physical_files_created,
        "materialized_directories_estimate": materialized_dirs,
        "physical_namespace_entries_estimate": namespace_entries,
        "logical_creates_per_physical_file": round(creates / physical_files_created, 6)
        if physical_files_created > 0
        else 0.0,
        "namespace_entries_per_logical_create": round(namespace_entries / creates, 6)
        if creates > 0
        else 0.0,
        "packed_records": packed_records,
        "packed_create_fraction": round(packed_records / creates, 6) if creates > 0 else 0.0,
        "index_bytes": index_bytes,
        "index_bytes_per_packed_record": round(index_bytes / packed_records, 6)
        if packed_records > 0
        else 0.0,
        "segments_created": segments_created,
        "rados_objects_created": rados_objects_created,
        "index_files_estimate": index_files,
    }
    if "index_flushes" in storage_metrics:
        derived["index_flushes"] = int(storage_metrics["index_flushes"])
    if "data_flushes" in storage_metrics:
        data_flushes = int(storage_metrics["data_flushes"])
        derived["data_flushes"] = data_flushes
        derived["logical_creates_per_data_flush"] = (
            round(creates / data_flushes, 6) if data_flushes > 0 else 0.0
        )
        packed_cold_bytes = int(storage_metrics.get("packed_cold_bytes", 0))
        derived["packed_cold_bytes_per_data_flush"] = (
            round(packed_cold_bytes / data_flushes, 6)
            if data_flushes > 0
            else 0.0
        )
    for pool_name, pool_stats in ceph_delta.items():
        for key in ("objects", "bytes_used", "stored"):
            value = int(pool_stats.get(key, 0))
            derived[f"ceph_delta_{pool_name}_{key}"] = value
            if creates > 0:
                derived[f"ceph_delta_{pool_name}_{key}_per_logical_create"] = round(
                    value / creates, 6
                )
    return derived


def scenario_configs(args: argparse.Namespace) -> list[WorkloadConfig]:
    if args.suite == "quick":
        return [
            WorkloadConfig("mdtest_tree", 200, 0, args.workers, 2, 4, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("sprite_lfs_smallfile", 200, 1024, args.workers, 1, 8, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("hotdirs_zipf", 250, 512, args.workers, args.depth, args.branching, 8, args.ops, args.seed, args.pin_ranks, args.keep_data),
        ]
    if args.suite == "standard":
        return [
            WorkloadConfig("mdtest_tree", 5000, 0, args.workers, 3, 8, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("sprite_lfs_smallfile", 10000, 1024, args.workers, 1, 16, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("filebench_varmail_like", 5000, 4096, args.workers, args.depth, args.branching, 64, 10000, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("hotdirs_zipf", 10000, 512, args.workers, args.depth, args.branching, 64, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig(
                "ycsb_file_skew",
                10000,
                4096,
                args.workers,
                args.depth,
                args.branching,
                64,
                args.ops,
                args.seed,
                args.pin_ranks,
                args.keep_data,
                args.oracle_cold_fraction,
                args.oracle_cold_access_fraction,
                args.ycsb_distribution,
                args.ycsb_update_fraction,
                args.ycsb_hot_fraction,
                args.ycsb_hot_op_fraction,
                args.ycsb_zipf_alpha,
            ),
        ]
    if args.suite == "metadata_heavy":
        return [
            WorkloadConfig("mdtest_tree", 12000, 0, args.workers, 3, 8, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("sprite_lfs_smallfile", 20000, 4096, args.workers, 1, 32, args.dirs, args.ops, args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig("hotdirs_zipf", 20000, 4096, args.workers, args.depth, args.branching, 128, max(args.ops, 40000), args.seed, args.pin_ranks, args.keep_data),
            WorkloadConfig(
                "oracle_hotcold_mix",
                20000,
                4096,
                args.workers,
                args.depth,
                args.branching,
                128,
                max(args.ops, 40000),
                args.seed,
                args.pin_ranks,
                args.keep_data,
                args.oracle_cold_fraction,
                args.oracle_cold_access_fraction,
            ),
        ]
    return [
        WorkloadConfig(
            args.workload,
            args.file_count,
            args.file_size,
            args.workers,
            args.depth,
            args.branching,
            args.dirs,
            args.ops,
            args.seed,
            args.pin_ranks,
            args.keep_data,
            args.oracle_cold_fraction,
            args.oracle_cold_access_fraction,
            args.ycsb_distribution,
            args.ycsb_update_fraction,
            args.ycsb_hot_fraction,
            args.ycsb_hot_op_fraction,
            args.ycsb_zipf_alpha,
        )
    ]


def run_workload(
    root: str,
    backend_config: BackendConfig,
    policy: MetadataPolicy,
    storage: StorageLayer,
    config: WorkloadConfig,
) -> list[PhaseResult]:
    workload_root = f"{root}/{config.name}-{uuid.uuid4().hex[:8]}"
    if config.name in {"mdtest_tree", "sprite_lfs_smallfile"}:
        return run_mdtest_like(workload_root, backend_config, policy, storage, config)
    if config.name == "hotdirs_zipf":
        return run_hotdirs(workload_root, backend_config, policy, storage, config)
    if config.name == "ycsb_file_skew":
        return run_ycsb_file_skew(workload_root, backend_config, policy, storage, config)
    if config.name == "predictor_false_hot_churn":
        return run_predictor_false_hot_churn(
            workload_root, backend_config, policy, storage, config
        )
    if config.name == "oracle_hotcold_mix":
        return run_oracle_hotcold_mix(workload_root, backend_config, policy, storage, config)
    if config.name == "filebench_varmail_like":
        return run_varmail_like(workload_root, backend_config, policy, storage, config)
    raise ValueError(f"unknown workload: {config.name}")


def write_outputs(
    output: str | None,
    csv_output: str | None,
    document: dict[str, object],
    rows: list[dict[str, object]],
) -> None:
    if output == "-":
        print(json.dumps(document, indent=2, sort_keys=True))
    elif output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    if csv_output:
        csv_path = Path(csv_output)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
            if rows:
                writer.writeheader()
                writer.writerows(rows)


def parse_pin_ranks(value: str) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def parse_fraction(value: str) -> float:
    number = float(value)
    if number < 0.0 or number > 1.0:
        raise argparse.ArgumentTypeError(f"fraction must be between 0 and 1: {value}")
    return number


def parse_policy_options(values: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError(f"policy option must be key=value: {value}")
        key, option_value = value.split("=", 1)
        options[key.strip()] = option_value.strip()
    return options


def load_plugin(path: Path, module_name: str, factory_name: str) -> object:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, factory_name, None)
    if factory is None:
        raise RuntimeError(f"plugin {path} must define {factory_name}(config)")
    return factory


def default_plugin_path(directory: str, name: str) -> Path:
    filename = f"{name}.py"
    package_root = Path(__file__).resolve().parents[1]
    for candidate in (
        Path(directory) / filename,
        Path("src") / directory / filename,
        package_root / directory / filename,
    ):
        if candidate.exists():
            return candidate
    return package_root / directory / filename


def load_policy(args: argparse.Namespace) -> MetadataPolicy:
    config = PolicyConfig(
        pin_ranks=args.pin_ranks,
        hot_window=args.hot_window,
        hot_threshold=args.hot_threshold,
        hot_min_interval=args.hot_min_interval,
        options=parse_policy_options(args.policy_opt),
    )
    path = Path(args.policy_file) if args.policy_file else default_plugin_path("policies", args.policy)
    if path.exists():
        create_policy = load_plugin(path, "cephfs_metadata_policy_plugin", "create_policy")
        policy = create_policy(config)
        if not isinstance(policy, MetadataPolicy):
            for method in ("on_dirs_created", "before_operation", "events"):
                if not hasattr(policy, method):
                    raise RuntimeError(f"policy plugin {path} returned object missing {method}()")
        return policy
    return build_policy(args.policy, config)


def parse_storage_options(values: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError(f"storage option must be key=value: {value}")
        key, option_value = value.split("=", 1)
        options[key.strip()] = option_value.strip()
    return options


def load_storage(args: argparse.Namespace) -> StorageLayer:
    config = StorageConfig(
        segment_size=args.segment_size,
        options=parse_storage_options(args.storage_opt),
    )
    path = Path(args.storage_file) if args.storage_file else default_plugin_path("storage_plugins", args.storage)
    if path.exists():
        create_storage = load_plugin(path, "cephfs_metadata_storage_plugin", "create_storage")
        storage = create_storage(config)
        if not isinstance(storage, StorageLayer):
            for method in ("prepare", "write_file", "read_file", "stat", "unlink", "cleanup", "sync", "metrics"):
                if not hasattr(storage, method):
                    raise RuntimeError(f"storage plugin {path} returned object missing {method}()")
        return storage
    return build_storage(args.storage, config)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["posix", "simulated-ceph", "ceph-libcephfs"], default="posix")
    parser.add_argument("--conffile", default="/etc/ceph/ceph.conf")
    parser.add_argument("--sim-metadata-us", type=float, default=700.0)
    parser.add_argument("--sim-file-create-us", type=float, default=1200.0)
    parser.add_argument("--sim-file-write-us", type=float, default=250.0)
    parser.add_argument("--sim-file-read-us", type=float, default=180.0)
    parser.add_argument("--sim-stat-us", type=float, default=250.0)
    parser.add_argument("--sim-xattr-us", type=float, default=800.0)
    parser.add_argument("--sim-object-write-us", type=float, default=180.0)
    parser.add_argument("--sim-object-read-us", type=float, default=160.0)
    parser.add_argument("--sim-object-remove-us", type=float, default=160.0)
    parser.add_argument("--sim-data-mib-per-sec", type=float, default=300.0)
    parser.add_argument("--sim-mds-ranks", type=int, default=1)
    parser.add_argument("--sim-osd-ranks", type=int, default=1)
    parser.add_argument("--root", default="/tmp/cs2640-cephfs-bench")
    parser.add_argument("--suite", choices=["quick", "standard", "metadata_heavy", "custom"], default="quick")
    parser.add_argument(
        "--workload",
        choices=[
            "mdtest_tree",
            "sprite_lfs_smallfile",
            "filebench_varmail_like",
            "hotdirs_zipf",
            "ycsb_file_skew",
            "predictor_false_hot_churn",
            "oracle_hotcold_mix",
        ],
        default="mdtest_tree",
    )
    parser.add_argument("--file-count", type=int, default=1000)
    parser.add_argument("--file-size", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--branching", type=int, default=8)
    parser.add_argument("--dirs", type=int, default=32)
    parser.add_argument("--ops", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--oracle-cold-fraction",
        type=parse_fraction,
        default=0.875,
        help="fraction of oracle_hotcold_mix files placed in cold directories",
    )
    parser.add_argument(
        "--oracle-cold-access-fraction",
        type=parse_fraction,
        default=0.0,
        help="fraction of oracle_hotcold_mix stat/read operations targeting cold files",
    )
    parser.add_argument(
        "--ycsb-distribution",
        choices=["zipfian", "hotspot"],
        default="zipfian",
        help="file-level access distribution for ycsb_file_skew",
    )
    parser.add_argument(
        "--ycsb-update-fraction",
        type=parse_fraction,
        default=0.2,
        help="fraction of ycsb_file_skew access operations that are updates",
    )
    parser.add_argument(
        "--ycsb-hot-fraction",
        type=parse_fraction,
        default=0.2,
        help="fraction of ycsb_file_skew files placed in generator-hot directories",
    )
    parser.add_argument(
        "--ycsb-hot-op-fraction",
        type=parse_fraction,
        default=0.8,
        help="hotspot distribution probability of choosing generator-hot files",
    )
    parser.add_argument(
        "--ycsb-zipf-alpha",
        type=float,
        default=0.99,
        help="zipfian skew parameter for ycsb_file_skew",
    )
    parser.add_argument("--pin-ranks", type=parse_pin_ranks, default=())
    parser.add_argument("--policy", default="none")
    parser.add_argument("--policy-file")
    parser.add_argument("--policy-opt", action="append", default=[])
    parser.add_argument("--hot-window", type=int, default=128)
    parser.add_argument("--hot-threshold", type=int, default=64)
    parser.add_argument("--hot-min-interval", type=int, default=32)
    parser.add_argument("--storage", default="native")
    parser.add_argument("--storage-file")
    parser.add_argument("--storage-opt", action="append", default=[])
    parser.add_argument("--segment-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--output", default="results/latest.json")
    parser.add_argument("--csv", default="results/latest.csv")
    parser.add_argument("--no-ceph-stats", action="store_true")
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="leave benchmark files/directories in place and skip cleanup delete phases",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    backend_config = BackendConfig(
        backend=args.backend,
        conffile=args.conffile,
        sim_metadata_us=args.sim_metadata_us,
        sim_file_create_us=args.sim_file_create_us,
        sim_file_write_us=args.sim_file_write_us,
        sim_file_read_us=args.sim_file_read_us,
        sim_stat_us=args.sim_stat_us,
        sim_xattr_us=args.sim_xattr_us,
        sim_object_write_us=args.sim_object_write_us,
        sim_object_read_us=args.sim_object_read_us,
        sim_object_remove_us=args.sim_object_remove_us,
        sim_data_mib_per_sec=args.sim_data_mib_per_sec,
        sim_mds_ranks=args.sim_mds_ranks,
        sim_osd_ranks=args.sim_osd_ranks,
    )
    policy = load_policy(args)
    storage = load_storage(args)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = f"{args.root.rstrip('/')}/{run_id}"

    before_stats = {} if args.no_ceph_stats else collect_ceph_stats()
    started = time.time()
    results: list[PhaseResult] = []
    for config in scenario_configs(args):
        results.extend(run_workload(root, backend_config, policy, storage, config))
    ended = time.time()
    after_stats = {} if args.no_ceph_stats else collect_ceph_stats()

    rows = [result.to_row() for result in results]
    measured_seconds = sum(result.total_seconds for result in results)
    measured_operations = sum(result.count for result in results)
    for row in rows:
        row.update(
            {
                "run_id": run_id,
                "backend": args.backend,
                "workers": args.workers,
                "policy": getattr(policy, "name", args.policy_file or args.policy),
                "storage": getattr(storage, "name", args.storage_file or args.storage),
                "file_count": args.file_count,
                "file_size": args.file_size,
                "dirs": args.dirs,
                "ops": args.ops,
                "seed": args.seed,
                "oracle_cold_fraction": args.oracle_cold_fraction,
                "oracle_cold_access_fraction": args.oracle_cold_access_fraction,
                "ycsb_distribution": args.ycsb_distribution,
                "ycsb_update_fraction": args.ycsb_update_fraction,
                "ycsb_hot_fraction": args.ycsb_hot_fraction,
                "ycsb_hot_op_fraction": args.ycsb_hot_op_fraction,
                "ycsb_zipf_alpha": args.ycsb_zipf_alpha,
            }
        )
    ceph_delta = pool_delta(before_stats, after_stats) if before_stats and after_stats else {}
    storage_metrics = storage.metrics()
    document = {
        "run_id": run_id,
        "host": socket.gethostname(),
        "backend": args.backend,
        "root": root,
        "started_unix": started,
        "ended_unix": ended,
        "elapsed_seconds": round(ended - started, 6),
        "measured_seconds": round(measured_seconds, 6),
        "measured_operations": measured_operations,
        "measured_ops_per_sec": round(measured_operations / measured_seconds, 6)
        if measured_seconds > 0
        else 0.0,
        "args": vars(args),
        "results": rows,
        "policy_events": [event.to_row() for event in policy.events()],
        "storage_metrics": storage_metrics,
        "derived_metrics": derive_result_metrics(args, rows, storage_metrics, ceph_delta),
        "ceph_stats": {
            "before": before_stats,
            "after": after_stats,
            "delta": ceph_delta,
        },
    }
    write_outputs(args.output, args.csv, document, rows)
    if args.output != "-":
        print(f"wrote {args.output}")
    if args.csv:
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
