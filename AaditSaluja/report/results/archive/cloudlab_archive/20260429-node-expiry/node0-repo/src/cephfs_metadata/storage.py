"""Storage-layer plugins for benchmark workloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Lock
from typing import Iterable, Protocol


class StorageBackend(Protocol):
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


class DirPolicy(Protocol):
    def on_dirs_created(self, backend: object, dirs: list[str]) -> None:
        ...


@dataclass(frozen=True)
class StorageConfig:
    segment_size: int = 64 * 1024 * 1024
    options: dict[str, str] = field(default_factory=dict)


class StorageLayer:
    name = "native"

    def prepare(self, backend: StorageBackend, root: str, dirs: list[str], policy: DirPolicy) -> None:
        raise NotImplementedError

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        raise NotImplementedError

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        raise NotImplementedError

    def stat(self, backend: StorageBackend, path: str) -> object:
        raise NotImplementedError

    def unlink(self, backend: StorageBackend, path: str) -> None:
        raise NotImplementedError

    def cleanup(self, backend: StorageBackend, dirs: Iterable[str]) -> None:
        raise NotImplementedError

    def metrics(self) -> dict[str, object]:
        return {}


class NativeFileStorage(StorageLayer):
    name = "native"

    def prepare(self, backend: StorageBackend, root: str, dirs: list[str], policy: DirPolicy) -> None:
        del root
        for directory in dirs:
            backend.mkdirs(directory)
        policy.on_dirs_created(backend, dirs)

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        backend.write_file(path, data)

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        return backend.read_file(path, size)

    def stat(self, backend: StorageBackend, path: str) -> object:
        return backend.stat(path)

    def unlink(self, backend: StorageBackend, path: str) -> None:
        backend.unlink(path)

    def cleanup(self, backend: StorageBackend, dirs: Iterable[str]) -> None:
        for directory in sorted(dirs, key=lambda item: item.count("/"), reverse=True):
            try:
                backend.rmdir(directory)
            except Exception:
                pass


@dataclass
class PackedRecord:
    segment: str
    offset: int
    length: int
    deleted: bool = False


class AppendSegmentSmallFileStorage(StorageLayer):
    """Minimal small-file packing layer.

    Logical files never become physical files. Their bytes are appended to large
    segment files, while a separate append-only JSON-lines index maps logical
    path to segment, offset, and length.
    """

    name = "append_segments"

    def __init__(self, config: StorageConfig) -> None:
        self.segment_size = max(1, config.segment_size)
        self._index: dict[str, PackedRecord] = {}
        self._segments: list[str] = []
        self._segment_offsets: dict[str, int] = {}
        self._root = ""
        self._store_dir = ""
        self._segments_dir = ""
        self._index_log = ""
        self._index_log_offset = 0
        self._tombstones = 0
        self._lock = Lock()

    def prepare(self, backend: StorageBackend, root: str, dirs: list[str], policy: DirPolicy) -> None:
        del dirs
        self._root = root
        self._store_dir = f"{root}/__packed"
        self._segments_dir = f"{self._store_dir}/segments"
        self._index_log = f"{self._store_dir}/index.log"
        backend.mkdirs(self._segments_dir)
        backend.write_file(self._index_log, b"")
        policy.on_dirs_created(backend, [root, self._store_dir, self._segments_dir])

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        with self._lock:
            segment = self._current_segment_locked(backend, len(data))
            offset = self._segment_offsets[segment]
            self._segment_offsets[segment] += len(data)
            self._index[path] = PackedRecord(segment=segment, offset=offset, length=len(data))
            index_record = {
                "op": "put",
                "path": path,
                "segment": segment,
                "offset": offset,
                "length": len(data),
            }
            index_offset = self._index_log_offset
            index_data = (json.dumps(index_record, sort_keys=True) + "\n").encode("utf-8")
            self._index_log_offset += len(index_data)
        if data:
            backend.write_at(segment, data, offset)
        backend.write_at(self._index_log, index_data, index_offset)

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        record = self._record(path)
        read_size = min(size, record.length) if size >= 0 else record.length
        return backend.read_at(record.segment, record.offset, read_size)

    def stat(self, backend: StorageBackend, path: str) -> object:
        del backend
        record = self._record(path)
        return {
            "logical_path": path,
            "size": record.length,
            "segment": record.segment,
            "offset": record.offset,
            "packed": True,
        }

    def unlink(self, backend: StorageBackend, path: str) -> None:
        with self._lock:
            record = self._record(path)
            record.deleted = True
            self._tombstones += 1
            index_record = {"op": "delete", "path": path}
            index_offset = self._index_log_offset
            index_data = (json.dumps(index_record, sort_keys=True) + "\n").encode("utf-8")
            self._index_log_offset += len(index_data)
        backend.write_at(self._index_log, index_data, index_offset)

    def cleanup(self, backend: StorageBackend, dirs: Iterable[str]) -> None:
        del dirs
        for segment in reversed(self._segments):
            try:
                backend.unlink(segment)
            except Exception:
                pass
        try:
            backend.unlink(self._index_log)
        except Exception:
            pass
        for directory in [self._segments_dir, self._store_dir, self._root]:
            try:
                backend.rmdir(directory)
            except Exception:
                pass

    def metrics(self) -> dict[str, object]:
        live_records = [record for record in self._index.values() if not record.deleted]
        return {
            "logical_records": len(self._index),
            "live_records": len(live_records),
            "live_bytes": sum(record.length for record in live_records),
            "segments_created": len(self._segments),
            "segment_size": self.segment_size,
            "index_log_bytes": self._index_log_offset,
            "tombstones": self._tombstones,
        }

    def _record(self, path: str) -> PackedRecord:
        record = self._index.get(path)
        if record is None or record.deleted:
            raise FileNotFoundError(path)
        return record

    def _current_segment_locked(self, backend: StorageBackend, write_size: int) -> str:
        if not self._segments:
            return self._new_segment_locked(backend)
        segment = self._segments[-1]
        if self._segment_offsets[segment] + write_size > self.segment_size:
            return self._new_segment_locked(backend)
        return segment

    def _new_segment_locked(self, backend: StorageBackend) -> str:
        segment = f"{self._segments_dir}/segment-{len(self._segments):08d}.dat"
        backend.write_file(segment, b"")
        self._segments.append(segment)
        self._segment_offsets[segment] = 0
        return segment


@dataclass
class SegmentShard:
    shard_id: str
    root: str
    segments_dir: str
    index_log: str
    segments: list[str] = field(default_factory=list)
    segment_offsets: dict[str, int] = field(default_factory=dict)
    index_log_offset: int = 0
    records: int = 0
    tombstones: int = 0
    lock: Lock = field(default_factory=Lock)


class ShardedAppendSegmentSmallFileStorage(StorageLayer):
    """Append-segment packing with independent segment/index shards.

    Shards can be selected by logical parent directory, or by a fixed hash over
    the logical file path. Directory sharding reduces contention for broad
    namespace workloads; hash sharding also spreads a single hot directory across
    multiple segment/index logs.
    """

    name = "sharded_segments"

    def __init__(self, config: StorageConfig) -> None:
        self.segment_size = max(1, config.segment_size)
        self.shard_mode = config.options.get("shard_mode", "directory")
        self.shard_count = max(1, int(config.options.get("shard_count", "8")))
        if self.shard_mode not in {"directory", "hash"}:
            raise ValueError("shard_mode must be directory or hash")
        self._index: dict[str, PackedRecord] = {}
        self._shards: dict[str, SegmentShard] = {}
        self._root = ""
        self._store_dir = ""
        self._lock = Lock()

    def prepare(self, backend: StorageBackend, root: str, dirs: list[str], policy: DirPolicy) -> None:
        del dirs
        self._root = root
        self._store_dir = f"{root}/__packed_sharded"
        backend.mkdirs(self._store_dir)
        policy.on_dirs_created(backend, [root, self._store_dir])

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        shard = self._shard_for_path(backend, path)
        with shard.lock:
            segment = self._current_segment_locked(backend, shard, len(data))
            offset = shard.segment_offsets[segment]
            shard.segment_offsets[segment] += len(data)
            self._index[path] = PackedRecord(segment=segment, offset=offset, length=len(data))
            shard.records += 1
            index_record = {
                "op": "put",
                "path": path,
                "segment": segment,
                "offset": offset,
                "length": len(data),
                "shard": shard.shard_id,
            }
            index_offset = shard.index_log_offset
            index_data = (json.dumps(index_record, sort_keys=True) + "\n").encode("utf-8")
            shard.index_log_offset += len(index_data)
        if data:
            backend.write_at(segment, data, offset)
        backend.write_at(shard.index_log, index_data, index_offset)

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        record = self._record(path)
        read_size = min(size, record.length) if size >= 0 else record.length
        return backend.read_at(record.segment, record.offset, read_size)

    def stat(self, backend: StorageBackend, path: str) -> object:
        del backend
        record = self._record(path)
        return {
            "logical_path": path,
            "size": record.length,
            "segment": record.segment,
            "offset": record.offset,
            "packed": True,
            "sharded": True,
        }

    def unlink(self, backend: StorageBackend, path: str) -> None:
        shard = self._shard_for_path(backend, path)
        with shard.lock:
            record = self._record(path)
            record.deleted = True
            shard.tombstones += 1
            index_record = {"op": "delete", "path": path, "shard": shard.shard_id}
            index_offset = shard.index_log_offset
            index_data = (json.dumps(index_record, sort_keys=True) + "\n").encode("utf-8")
            shard.index_log_offset += len(index_data)
        backend.write_at(shard.index_log, index_data, index_offset)

    def cleanup(self, backend: StorageBackend, dirs: Iterable[str]) -> None:
        del dirs
        for shard in self._shards.values():
            for segment in reversed(shard.segments):
                try:
                    backend.unlink(segment)
                except Exception:
                    pass
            try:
                backend.unlink(shard.index_log)
            except Exception:
                pass
            for directory in [shard.segments_dir, shard.root]:
                try:
                    backend.rmdir(directory)
                except Exception:
                    pass
        try:
            backend.rmdir(self._store_dir)
        except Exception:
            pass
        try:
            backend.rmdir(self._root)
        except Exception:
            pass

    def metrics(self) -> dict[str, object]:
        live_records = [record for record in self._index.values() if not record.deleted]
        shard_records = {shard_id: shard.records for shard_id, shard in self._shards.items()}
        return {
            "logical_records": len(self._index),
            "live_records": len(live_records),
            "live_bytes": sum(record.length for record in live_records),
            "segments_created": sum(len(shard.segments) for shard in self._shards.values()),
            "segment_size": self.segment_size,
            "index_log_bytes": sum(shard.index_log_offset for shard in self._shards.values()),
            "tombstones": sum(shard.tombstones for shard in self._shards.values()),
            "shard_mode": self.shard_mode,
            "configured_shard_count": self.shard_count,
            "shards_created": len(self._shards),
            "max_records_per_shard": max(shard_records.values(), default=0),
            "min_records_per_shard": min(shard_records.values(), default=0),
        }

    def _record(self, path: str) -> PackedRecord:
        record = self._index.get(path)
        if record is None or record.deleted:
            raise FileNotFoundError(path)
        return record

    def _shard_for_path(self, backend: StorageBackend, path: str) -> SegmentShard:
        shard_id = self._shard_id(path)
        shard = self._shards.get(shard_id)
        if shard is not None:
            return shard
        with self._lock:
            shard = self._shards.get(shard_id)
            if shard is not None:
                return shard
            shard_root = f"{self._store_dir}/shard-{shard_id}"
            segments_dir = f"{shard_root}/segments"
            index_log = f"{shard_root}/index.log"
            backend.mkdirs(segments_dir)
            backend.write_file(index_log, b"")
            shard = SegmentShard(
                shard_id=shard_id,
                root=shard_root,
                segments_dir=segments_dir,
                index_log=index_log,
            )
            self._shards[shard_id] = shard
            return shard

    def _shard_id(self, path: str) -> str:
        if self.shard_mode == "hash":
            value = int(hashlib.sha1(path.encode("utf-8")).hexdigest()[:12], 16)
            return f"h{value % self.shard_count:04d}"
        parent = str(PurePosixPath(path).parent)
        digest = hashlib.sha1(parent.encode("utf-8")).hexdigest()[:16]
        return f"d-{digest}"

    def _current_segment_locked(
        self, backend: StorageBackend, shard: SegmentShard, write_size: int
    ) -> str:
        if not shard.segments:
            return self._new_segment_locked(backend, shard)
        segment = shard.segments[-1]
        if shard.segment_offsets[segment] + write_size > self.segment_size:
            return self._new_segment_locked(backend, shard)
        return segment

    def _new_segment_locked(self, backend: StorageBackend, shard: SegmentShard) -> str:
        segment = f"{shard.segments_dir}/segment-{len(shard.segments):08d}.dat"
        backend.write_file(segment, b"")
        shard.segments.append(segment)
        shard.segment_offsets[segment] = 0
        return segment


def build_storage(name: str, config: StorageConfig) -> StorageLayer:
    if name == "native":
        return NativeFileStorage()
    if name in {"append_segments", "packed"}:
        return AppendSegmentSmallFileStorage(config)
    if name in {"sharded_segments", "sharded_packed"}:
        return ShardedAppendSegmentSmallFileStorage(config)
    raise ValueError(f"unknown storage layer: {name}")
