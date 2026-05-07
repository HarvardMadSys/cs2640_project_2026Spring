"""Benchmark runner for CephFS metadata experiments.

The runner intentionally keeps dependencies to the Python standard library so it
can run inside the Ceph daemon container used for local development.
"""

from __future__ import annotations

import argparse
import csv
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
from pathlib import Path
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


class PosixBackend:
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


class CephLibBackend:
    def __init__(self, conffile: str) -> None:
        import cephfs  # type: ignore[import-not-found]

        self.fs = cephfs.LibCephFS(conffile=conffile)
        self.fs.mount()

    def close(self) -> None:
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


@dataclass(frozen=True)
class BackendConfig:
    backend: str
    conffile: str


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
            for item in chunk:
                start = time.perf_counter()
                policy.before_operation(backend, operation, item)
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
        phase_start = time.perf_counter()
        for path in create_paths:
            start = time.perf_counter()
            policy.before_operation(backend, "initial_create", path)
            storage.write_file(backend, path, data)
            create_latencies.append((time.perf_counter() - start) * 1000.0)
            created.append(path)
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
                policy.before_operation(backend, "mixed_create", path)
                storage.write_file(backend, path, data)
                live.append(path)
            elif action < 0.80:
                path = live[rng.randrange(len(live))]
                if rng.random() < 0.5:
                    policy.before_operation(backend, "mixed_stat", path)
                    storage.stat(backend, path)
                else:
                    policy.before_operation(backend, "mixed_read", path)
                    storage.read_file(backend, path, config.file_size)
            else:
                index = rng.randrange(len(live))
                path = live.pop(index)
                policy.before_operation(backend, "mixed_delete", path)
                storage.unlink(backend, path)
            mixed_latencies.append((time.perf_counter() - start) * 1000.0)
        mixed_total = time.perf_counter() - phase_start

        if config.keep_data:
            delete_total = 0.0
        else:
            phase_start = time.perf_counter()
            for path in live:
                start = time.perf_counter()
                policy.before_operation(backend, "cleanup_delete", path)
                storage.unlink(backend, path)
                delete_latencies.append((time.perf_counter() - start) * 1000.0)
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


def load_policy(args: argparse.Namespace) -> MetadataPolicy:
    config = PolicyConfig(
        pin_ranks=args.pin_ranks,
        hot_window=args.hot_window,
        hot_threshold=args.hot_threshold,
        hot_min_interval=args.hot_min_interval,
        options=parse_policy_options(args.policy_opt),
    )
    path = Path(args.policy_file) if args.policy_file else Path("policies") / f"{args.policy}.py"
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
    path = (
        Path(args.storage_file)
        if args.storage_file
        else Path("storage_plugins") / f"{args.storage}.py"
    )
    if path.exists():
        create_storage = load_plugin(path, "cephfs_metadata_storage_plugin", "create_storage")
        storage = create_storage(config)
        if not isinstance(storage, StorageLayer):
            for method in ("prepare", "write_file", "read_file", "stat", "unlink", "cleanup", "metrics"):
                if not hasattr(storage, method):
                    raise RuntimeError(f"storage plugin {path} returned object missing {method}()")
        return storage
    return build_storage(args.storage, config)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["posix", "ceph-libcephfs"], default="posix")
    parser.add_argument("--conffile", default="/etc/ceph/ceph.conf")
    parser.add_argument("--root", default="/tmp/cs2640-cephfs-bench")
    parser.add_argument("--suite", choices=["quick", "standard", "custom"], default="quick")
    parser.add_argument(
        "--workload",
        choices=["mdtest_tree", "sprite_lfs_smallfile", "filebench_varmail_like", "hotdirs_zipf"],
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
    backend_config = BackendConfig(args.backend, args.conffile)
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
    for row in rows:
        row.update(
            {
                "run_id": run_id,
                "backend": args.backend,
                "workers": args.workers,
                "policy": getattr(policy, "name", args.policy_file or args.policy),
                "storage": getattr(storage, "name", args.storage_file or args.storage),
            }
        )
    document = {
        "run_id": run_id,
        "host": socket.gethostname(),
        "backend": args.backend,
        "root": root,
        "started_unix": started,
        "ended_unix": ended,
        "elapsed_seconds": round(ended - started, 6),
        "args": vars(args),
        "results": rows,
        "policy_events": [event.to_row() for event in policy.events()],
        "storage_metrics": storage.metrics(),
        "ceph_stats": {
            "before": before_stats,
            "after": after_stats,
            "delta": pool_delta(before_stats, after_stats)
            if before_stats and after_stats
            else {},
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
