"""Storage-layer plugins for benchmark workloads."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Lock, RLock
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

    def policy_backend(self, backend: StorageBackend) -> object:
        return backend

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

    def sync(self, backend: StorageBackend) -> None:
        del backend

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


class OracleColdSegmentStorage(StorageLayer):
    """Pack only oracle-cold files and keep oracle-hot files native.

    The current benchmark suite can name known-hot directories with a `hot*`
    prefix. Files under those directories stay as normal physical files so
    CephFS can pin and serve them directly. Bulk/cold files are packed into
    append-only segment files, and their metadata is recorded in a compact
    batched binary journal instead of JSON-lines.
    """

    name = "oracle_cold_segments"

    _INDEX_MAGIC = b"CSIDX2\0"
    _INDEX_RECORD = struct.Struct("<BHIQI")
    _OP_PUT = 1
    _OP_DELETE = 2

    def __init__(self, config: StorageConfig) -> None:
        self.segment_size = max(1, config.segment_size)
        self.hot_prefixes = tuple(
            prefix.strip()
            for prefix in config.options.get("hot_prefixes", "hot").split(",")
            if prefix.strip()
        )
        self.virtual_cold_dirs = config.options.get("virtual_cold_dirs", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        self.index_batch_bytes = max(
            256, int(config.options.get("index_batch_bytes", str(1024 * 1024)))
        )
        self.index_batch_records = max(
            1, int(config.options.get("index_batch_records", "512"))
        )
        self.cold_data_batch_bytes = max(
            0, int(config.options.get("cold_data_batch_bytes", str(1024 * 1024)))
        )
        self.cold_data_backend = config.options.get("cold_data_backend", "cephfs")
        if self.cold_data_backend not in {"cephfs", "rados", "auto"}:
            raise ValueError("cold_data_backend must be cephfs, rados, or auto")
        self.rados_pool = config.options.get("rados_pool", "cephfs_data")
        self.rados_namespace = config.options.get("rados_namespace", "cs2640-packed")
        self.ceph_layout_scope = config.options.get("ceph_layout_scope", "segments")
        if self.ceph_layout_scope not in {"off", "store", "segments", "all"}:
            raise ValueError("ceph_layout_scope must be off, store, segments, or all")
        self.ceph_layout = {
            key: value
            for key, value in {
                "ceph.file.layout.stripe_unit": config.options.get("ceph_stripe_unit"),
                "ceph.file.layout.stripe_count": config.options.get("ceph_stripe_count"),
                "ceph.file.layout.object_size": config.options.get("ceph_object_size"),
            }.items()
            if value is not None
        }
        self.policy_dir_view = config.options.get("policy_dir_view", "logical")
        if self.policy_dir_view not in {"logical", "physical"}:
            raise ValueError("policy_dir_view must be logical or physical")
        self.cold_policy_pin_target = config.options.get("cold_policy_pin_target", "store")
        if self.cold_policy_pin_target not in {"store", "segments", "root"}:
            raise ValueError("cold_policy_pin_target must be store, segments, or root")
        self.packed_stat_mode = config.options.get("packed_stat_mode", "memory")
        if self.packed_stat_mode not in {"memory", "index"}:
            raise ValueError("packed_stat_mode must be memory or index")
        self._index: dict[str, PackedRecord] = {}
        self._native_paths: set[str] = set()
        self._segment_paths: list[str] = []
        self._segment_offsets: dict[str, int] = {}
        self._root = ""
        self._store_dir = ""
        self._segments_dir = ""
        self._index_log = ""
        self._active_cold_data_backend = "cephfs"
        self._index_log_offset = 0
        self._index_buffer = bytearray()
        self._buffered_records = 0
        self._data_buffers: dict[str, bytearray] = {}
        self._data_buffer_offsets: dict[str, int] = {}
        self._tombstones = 0
        self._flushes = 0
        self._data_flushes = 0
        self._data_buffered_bytes = 0
        self._cold_bytes = 0
        self._cold_writes = 0
        self._layout_xattrs_attempted = 0
        self._layout_xattrs_applied = 0
        self._layout_xattr_failures: list[str] = []
        self._rados_objects_created = 0
        self._native_bytes = 0
        self._native_writes = 0
        self._policy_pin_translations = 0
        self._policy_pin_deduplications = 0
        self._translated_pin_ranks: dict[str, int] = {}
        self._lock = RLock()

    def prepare(
        self, backend: StorageBackend, root: str, dirs: list[str], policy: DirPolicy
    ) -> None:
        unique_dirs = list(dict.fromkeys([root] + dirs))
        physical_dirs = [root]
        if self.virtual_cold_dirs:
            physical_dirs.extend(
                directory
                for directory in unique_dirs[1:]
                if self._is_hot_path(f"{directory}/__dir_marker__")
            )
        else:
            physical_dirs.extend(unique_dirs[1:])
        for directory in physical_dirs:
            backend.mkdirs(directory)
        self._root = root
        self._store_dir = f"{root}/__cold_packed"
        self._segments_dir = f"{self._store_dir}/segments"
        self._index_log = f"{self._store_dir}/index.bin"
        self._active_cold_data_backend = self._choose_cold_data_backend(backend)
        backend.mkdirs(self._segments_dir)
        self._apply_layout_xattrs(backend, self._store_dir, "store", target_type="dir")
        self._apply_layout_xattrs(
            backend, self._segments_dir, "segments", target_type="dir"
        )
        backend.write_file(self._index_log, b"")
        self._apply_layout_xattrs(backend, self._index_log, "store")
        backend.write_at(self._index_log, self._INDEX_MAGIC, 0)
        self._index_log_offset = len(self._INDEX_MAGIC)
        if self.policy_dir_view == "logical":
            policy.on_dirs_created(self.policy_backend(backend), unique_dirs)
        else:
            policy.on_dirs_created(backend, physical_dirs)

    def policy_backend(self, backend: StorageBackend) -> object:
        return OraclePolicyBackend(self, backend)

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        if self._is_hot_path(path):
            backend.write_file(path, data)
            self._native_paths.add(path)
            self._native_bytes += len(data)
            self._native_writes += 1
            return

        with self._lock:
            segment_id, segment = self._current_segment_locked(backend, len(data))
            offset = self._segment_offsets[segment]
            self._segment_offsets[segment] += len(data)
            self._cold_writes += 1
            self._cold_bytes += len(data)
            self._index[path] = PackedRecord(
                segment=segment, offset=offset, length=len(data)
            )
            data_flush = self._append_data_locked(segment, offset, data)
            index_offset, index_data = self._append_index_record_locked(
                self._OP_PUT, path, segment_id, offset, len(data)
            )
        if data_flush:
            data_segment, data_offset, data_bytes = data_flush
            self._write_segment_at(backend, data_segment, data_bytes, data_offset)
        if index_data:
            backend.write_at(self._index_log, index_data, index_offset)

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        record = self._index.get(path)
        if record is None or record.deleted:
            return backend.read_file(path, size)
        self._flush_data_for_segment(backend, record.segment)
        read_size = min(size, record.length) if size >= 0 else record.length
        return self._read_segment_at(backend, record.segment, record.offset, read_size)

    def stat(self, backend: StorageBackend, path: str) -> object:
        record = self._index.get(path)
        if record is None or record.deleted:
            return backend.stat(path)
        if self.packed_stat_mode == "index":
            backend.stat(self._index_log)
        return {
            "logical_path": path,
            "size": record.length,
            "segment": record.segment,
            "offset": record.offset,
            "packed": True,
            "oracle_cold": True,
        }

    def unlink(self, backend: StorageBackend, path: str) -> None:
        record = self._index.get(path)
        if record is None or record.deleted:
            backend.unlink(path)
            self._native_paths.discard(path)
            return

        with self._lock:
            current = self._record(path)
            current.deleted = True
            self._tombstones += 1
            index_offset, index_data = self._append_index_record_locked(
                self._OP_DELETE, path, 0, 0, 0
            )
        if index_data:
            backend.write_at(self._index_log, index_data, index_offset)

    def cleanup(self, backend: StorageBackend, dirs: Iterable[str]) -> None:
        self.sync(backend)
        for path in list(self._native_paths):
            try:
                backend.unlink(path)
            except Exception:
                pass
        for segment in reversed(self._segment_paths):
            try:
                if self._is_rados_segment(segment):
                    pool, object_id = self._parse_rados_segment(segment)
                    backend.remove_object(pool, object_id)
                else:
                    backend.unlink(segment)
            except Exception:
                pass
        try:
            backend.unlink(self._index_log)
        except Exception:
            pass
        for directory in [self._segments_dir, self._store_dir]:
            try:
                backend.rmdir(directory)
            except Exception:
                pass
        for directory in sorted(
            set(dirs), key=lambda item: item.count("/"), reverse=True
        ):
            try:
                backend.rmdir(directory)
            except Exception:
                pass

    def sync(self, backend: StorageBackend) -> None:
        for segment, offset, data in self._drain_data_buffers():
            self._write_segment_at(backend, segment, data, offset)
        with self._lock:
            if not self._index_buffer:
                return
            index_offset = self._index_log_offset
            index_data = bytes(self._index_buffer)
            self._index_log_offset += len(index_data)
            self._index_buffer.clear()
            self._buffered_records = 0
            self._flushes += 1
        backend.write_at(self._index_log, index_data, index_offset)

    def metrics(self) -> dict[str, object]:
        live_records = [record for record in self._index.values() if not record.deleted]
        return {
            "hot_prefixes": list(self.hot_prefixes),
            "virtual_cold_dirs": self.virtual_cold_dirs,
            "live_native_hot_records": len(self._native_paths),
            "total_native_hot_writes": self._native_writes,
            "native_hot_bytes": self._native_bytes,
            "packed_cold_records": len(self._index),
            "live_packed_cold_records": len(live_records),
            "live_packed_cold_bytes": sum(record.length for record in live_records),
            "segments_created": len(self._segment_paths),
            "segment_size": self.segment_size,
            "index_journal_bytes": self._index_log_offset + len(self._index_buffer),
            "index_batch_bytes": self.index_batch_bytes,
            "index_batch_records": self.index_batch_records,
            "cold_data_batch_bytes": self.cold_data_batch_bytes,
            "cold_data_backend_requested": self.cold_data_backend,
            "cold_data_backend": self._active_cold_data_backend,
            "rados_pool": self.rados_pool if self._active_cold_data_backend == "rados" else "",
            "rados_namespace": self.rados_namespace if self._active_cold_data_backend == "rados" else "",
            "rados_objects_created": self._rados_objects_created,
            "ceph_layout_scope": self.ceph_layout_scope,
            "ceph_layout": dict(self.ceph_layout),
            "layout_xattrs_attempted": self._layout_xattrs_attempted,
            "layout_xattrs_applied": self._layout_xattrs_applied,
            "layout_xattr_failures": list(self._layout_xattr_failures[:8]),
            "index_flushes": self._flushes,
            "data_flushes": self._data_flushes,
            "buffered_index_bytes": len(self._index_buffer),
            "buffered_data_bytes": self._data_buffered_bytes,
            "tombstones": self._tombstones,
            "total_packed_cold_writes": self._cold_writes,
            "packed_cold_bytes": self._cold_bytes,
            "policy_dir_view": self.policy_dir_view,
            "cold_policy_pin_target": self.cold_policy_pin_target,
            "packed_stat_mode": self.packed_stat_mode,
            "policy_pin_translations": self._policy_pin_translations,
            "policy_pin_deduplications": self._policy_pin_deduplications,
        }

    def _record(self, path: str) -> PackedRecord:
        record = self._index.get(path)
        if record is None or record.deleted:
            raise FileNotFoundError(path)
        return record

    def _is_hot_path(self, path: str) -> bool:
        if not self.hot_prefixes:
            return False
        return any(
            part.startswith(prefix)
            for part in PurePosixPath(path).parts
            for prefix in self.hot_prefixes
        )

    def _policy_pin_path(self, path: str, rank: int) -> tuple[str, bool]:
        if path == self._root or path.startswith(f"{self._store_dir}/") or path == self._store_dir:
            return path, False
        if self._is_hot_path(path):
            return path, False
        if self.cold_policy_pin_target == "root":
            target = self._root
        elif self.cold_policy_pin_target == "segments":
            target = self._segments_dir
        else:
            target = self._store_dir
        with self._lock:
            self._policy_pin_translations += 1
            if self._translated_pin_ranks.get(target) == rank:
                self._policy_pin_deduplications += 1
                return target, True
        return target, False

    def _record_translated_pin_success(self, path: str, rank: int) -> None:
        with self._lock:
            self._translated_pin_ranks[path] = rank

    def _apply_layout_xattrs(
        self,
        backend: StorageBackend,
        path: str,
        scope: str,
        target_type: str = "file",
    ) -> None:
        if not self.ceph_layout or self.ceph_layout_scope == "off":
            return
        if self.ceph_layout_scope != "all" and self.ceph_layout_scope != scope:
            return
        layout = self._layout_xattrs_for_target(target_type)
        for name, value in layout.items():
            with self._lock:
                self._layout_xattrs_attempted += 1
            try:
                backend.set_xattr(path, name, value.encode("ascii"))
            except Exception as exc:
                with self._lock:
                    self._layout_xattr_failures.append(f"{path} {name}: {exc}")
            else:
                with self._lock:
                    self._layout_xattrs_applied += 1

    def _layout_xattrs_for_target(self, target_type: str) -> dict[str, str]:
        if target_type == "dir":
            return {
                name.replace("ceph.file.layout.", "ceph.dir.layout.", 1): value
                for name, value in self.ceph_layout.items()
            }
        return self.ceph_layout

    def _choose_cold_data_backend(self, backend: StorageBackend) -> str:
        has_objects = all(
            callable(getattr(backend, method, None))
            for method in ("write_object", "read_object", "remove_object")
        )
        available = getattr(backend, "object_backend_available", None)
        if callable(available):
            has_objects = has_objects and bool(available())
        if self.cold_data_backend == "auto":
            return "rados" if has_objects else "cephfs"
        if self.cold_data_backend == "rados" and not has_objects:
            raise RuntimeError("cold_data_backend=rados requires object backend methods")
        return self.cold_data_backend

    def _append_data_locked(
        self, segment: str, offset: int, data: bytes
    ) -> tuple[str, int, bytes] | None:
        if not data:
            return None
        if self.cold_data_batch_bytes <= 0:
            self._data_flushes += 1
            return segment, offset, data

        buffer = self._data_buffers.get(segment)
        if buffer is None:
            self._data_buffers[segment] = bytearray(data)
            self._data_buffer_offsets[segment] = offset
            self._data_buffered_bytes += len(data)
            return self._flush_data_locked(segment) if len(data) >= self.cold_data_batch_bytes else None

        expected_offset = self._data_buffer_offsets[segment] + len(buffer)
        if offset != expected_offset:
            flush = self._flush_data_locked(segment)
            self._data_buffers[segment] = bytearray(data)
            self._data_buffer_offsets[segment] = offset
            self._data_buffered_bytes += len(data)
            return flush

        buffer.extend(data)
        self._data_buffered_bytes += len(data)
        if len(buffer) >= self.cold_data_batch_bytes:
            return self._flush_data_locked(segment)
        return None

    def _flush_data_for_segment(self, backend: StorageBackend, segment: str) -> None:
        with self._lock:
            flush = self._flush_data_locked(segment)
        if flush:
            flush_segment, offset, data = flush
            self._write_segment_at(backend, flush_segment, data, offset)

    def _drain_data_buffers(self) -> list[tuple[str, int, bytes]]:
        with self._lock:
            return [
                flush
                for segment in list(self._data_buffers)
                if (flush := self._flush_data_locked(segment)) is not None
            ]

    def _flush_data_locked(self, segment: str) -> tuple[str, int, bytes] | None:
        buffer = self._data_buffers.pop(segment, None)
        if buffer is None:
            return None
        offset = self._data_buffer_offsets.pop(segment)
        self._data_buffered_bytes -= len(buffer)
        self._data_flushes += 1
        return segment, offset, bytes(buffer)

    def _append_index_record_locked(
        self, op: int, path: str, segment_id: int, offset: int, length: int
    ) -> tuple[int, bytes]:
        path_bytes = path.encode("utf-8")
        if len(path_bytes) > 65535:
            raise ValueError(f"path too long for packed index: {path}")
        self._index_buffer.extend(
            self._INDEX_RECORD.pack(op, len(path_bytes), segment_id, offset, length)
        )
        self._index_buffer.extend(path_bytes)
        self._buffered_records += 1
        if (
            len(self._index_buffer) < self.index_batch_bytes
            and self._buffered_records < self.index_batch_records
        ):
            return 0, b""
        index_offset = self._index_log_offset
        index_data = bytes(self._index_buffer)
        self._index_log_offset += len(index_data)
        self._index_buffer.clear()
        self._buffered_records = 0
        self._flushes += 1
        return index_offset, index_data

    def _current_segment_locked(
        self, backend: StorageBackend, write_size: int
    ) -> tuple[int, str]:
        if not self._segment_paths:
            return self._new_segment_locked(backend)
        segment = self._segment_paths[-1]
        if self._segment_offsets[segment] + write_size > self.segment_size:
            return self._new_segment_locked(backend)
        return len(self._segment_paths) - 1, segment

    def _new_segment_locked(self, backend: StorageBackend) -> tuple[int, str]:
        segment_id = len(self._segment_paths)
        if self._active_cold_data_backend == "rados":
            segment = self._rados_segment(segment_id)
            self._rados_objects_created += 1
            self._segment_paths.append(segment)
            self._segment_offsets[segment] = 0
            return segment_id, segment
        segment = f"{self._segments_dir}/segment-{segment_id:08d}.dat"
        backend.write_file(segment, b"")
        self._apply_layout_xattrs(backend, segment, "segments")
        self._segment_paths.append(segment)
        self._segment_offsets[segment] = 0
        return segment_id, segment

    def _write_segment_at(
        self, backend: StorageBackend, segment: str, data: bytes, offset: int
    ) -> None:
        if self._is_rados_segment(segment):
            pool, object_id = self._parse_rados_segment(segment)
            backend.write_object(pool, object_id, data, offset)
            return
        backend.write_at(segment, data, offset)

    def _read_segment_at(
        self, backend: StorageBackend, segment: str, offset: int, size: int
    ) -> bytes:
        if self._is_rados_segment(segment):
            pool, object_id = self._parse_rados_segment(segment)
            return backend.read_object(pool, object_id, offset, size)
        return backend.read_at(segment, offset, size)

    def _rados_segment(self, segment_id: int) -> str:
        root_hash = hashlib.sha1(self._root.encode("utf-8")).hexdigest()[:16]
        object_id = f"{self.rados_namespace}.{root_hash}.{segment_id:08d}"
        return f"rados://{self.rados_pool}/{object_id}"

    def _is_rados_segment(self, segment: str) -> bool:
        return segment.startswith("rados://")

    def _parse_rados_segment(self, segment: str) -> tuple[str, str]:
        if not self._is_rados_segment(segment):
            raise ValueError(f"not a rados segment: {segment}")
        pool_and_object = segment[len("rados://") :]
        pool, object_id = pool_and_object.split("/", 1)
        return pool, object_id


class PredictiveColdSegmentStorage(OracleColdSegmentStorage):
    """Online hot/cold packing without oracle labels.

    New files are packed first. The predictor observes stat/read traffic and
    promotes paths that cross an access threshold back into native files. This
    keeps the cold bulk path compact while letting repeatedly touched files
    recover the native CephFS fast path.
    """

    name = "predictive_cold_segments"

    def __init__(self, config: StorageConfig) -> None:
        super().__init__(config)
        self.predictor_strategy = config.options.get("predictor_strategy", "promote_on_access")
        if self.predictor_strategy not in {
            "promote_on_access",
            "directory_hotset",
            "never_promote",
        }:
            raise ValueError(
                "predictor_strategy must be promote_on_access, directory_hotset, or never_promote"
            )
        self.predictor_directory_promotion = (
            config.options.get("predictor_directory_promotion", "true").lower()
            not in {"0", "false", "no"}
        )
        self.predictor_promote_existing = (
            config.options.get("predictor_promote_existing", "true").lower()
            not in {"0", "false", "no"}
        )
        self.promotion_threshold = max(
            1, int(config.options.get("promotion_threshold", "1"))
        )
        self.predictor_dir_event_threshold = max(
            1, int(config.options.get("predictor_dir_event_threshold", "8"))
        )
        self.predictor_dir_distinct_threshold = max(
            1, int(config.options.get("predictor_dir_distinct_threshold", "2"))
        )
        self.promotion_triggers = {
            item.strip()
            for item in config.options.get("promotion_triggers", "read,stat").split(",")
            if item.strip()
        }
        unknown_triggers = self.promotion_triggers - {"read", "stat"}
        if unknown_triggers:
            raise ValueError(
                "promotion_triggers may only contain read and stat: "
                + ",".join(sorted(unknown_triggers))
            )
        self.predictor_eval_hot_prefixes = tuple(
            prefix.strip()
            for prefix in config.options.get("predictor_eval_hot_prefixes", "hot").split(",")
            if prefix.strip()
        )
        self._access_counts: dict[str, int] = {}
        self._dir_access_counts: dict[str, int] = {}
        self._dir_accessed_paths: dict[str, set[str]] = {}
        self._promoted_paths: set[str] = set()
        self._promotion_attempts = 0
        self._promotion_failures = 0
        self._total_promotions = 0
        self._promoted_eval_hot_total = 0
        self._promoted_eval_cold_total = 0
        self._promotion_bytes = 0
        self._materialized_native_dirs: set[str] = set()
        self._predicted_hot_dirs: set[str] = set()

    def write_file(self, backend: StorageBackend, path: str, data: bytes) -> None:
        if self._is_hot_path(path):
            parent = str(PurePosixPath(path).parent)
            self._ensure_native_parent(backend, parent)
            backend.write_file(path, data)
            self._native_paths.add(path)
            self._native_bytes += len(data)
            self._native_writes += 1
            return
        super().write_file(backend, path, data)

    def read_file(self, backend: StorageBackend, path: str, size: int) -> bytes:
        if path in self._native_paths:
            return backend.read_file(path, size)
        data = super().read_file(backend, path, size)
        self._record_predictive_access(backend, path, "read", data=data)
        return data

    def stat(self, backend: StorageBackend, path: str) -> object:
        if path in self._native_paths:
            return backend.stat(path)
        result = super().stat(backend, path)
        promoted = self._record_predictive_access(backend, path, "stat")
        if promoted and path in self._native_paths:
            return backend.stat(path)
        return result

    def unlink(self, backend: StorageBackend, path: str) -> None:
        if path in self._native_paths:
            try:
                backend.unlink(path)
            finally:
                self._native_paths.discard(path)
                self._promoted_paths.discard(path)
            with self._lock:
                record = self._index.get(path)
                if record is not None and not record.deleted:
                    record.deleted = True
                    self._tombstones += 1
                    index_offset, index_data = self._append_index_record_locked(
                        self._OP_DELETE, path, 0, 0, 0
                    )
                else:
                    index_offset, index_data = 0, b""
            if index_data:
                backend.write_at(self._index_log, index_data, index_offset)
            return
        super().unlink(backend, path)

    def metrics(self) -> dict[str, object]:
        metrics = super().metrics()
        promoted_hot = sum(1 for path in self._promoted_paths if self._matches_eval_hot(path))
        promoted_cold = len(self._promoted_paths) - promoted_hot
        predicted_hot_dirs = sum(
            1
            for directory in self._predicted_hot_dirs
            if self._matches_eval_hot(f"{directory}/__dir_marker__")
        )
        predicted_cold_dirs = len(self._predicted_hot_dirs) - predicted_hot_dirs
        live_packed_hot = sum(
            1
            for path, record in self._index.items()
            if not record.deleted and self._matches_eval_hot(path)
        )
        live_packed_cold = sum(
            1
            for path, record in self._index.items()
            if not record.deleted and not self._matches_eval_hot(path)
        )
        metrics.update(
            {
                "predictor_strategy": self.predictor_strategy,
                "promotion_threshold": self.promotion_threshold,
                "promotion_triggers": sorted(self.promotion_triggers),
                "predictor_directory_promotion": self.predictor_directory_promotion,
                "predictor_promote_existing": self.predictor_promote_existing,
                "predictor_dir_event_threshold": self.predictor_dir_event_threshold,
                "predictor_dir_distinct_threshold": self.predictor_dir_distinct_threshold,
                "predicted_hot_dirs": len(self._predicted_hot_dirs),
                "predictor_accessed_paths": len(self._access_counts),
                "predictor_access_events": sum(self._access_counts.values()),
                "predictor_accessed_dirs": len(self._dir_access_counts),
                "predicted_eval_hot_dirs": predicted_hot_dirs,
                "predicted_eval_cold_dirs": predicted_cold_dirs,
                "predicted_hot_dir_precision": round(
                    predicted_hot_dirs / len(self._predicted_hot_dirs), 6
                )
                if self._predicted_hot_dirs
                else 0.0,
                "promotion_attempts": self._promotion_attempts,
                "live_promotions": len(self._promoted_paths),
                "total_promotions": self._total_promotions,
                "promotion_failures": self._promotion_failures,
                "promotion_bytes": self._promotion_bytes,
                "materialized_native_dirs": len(self._materialized_native_dirs),
                "predictor_eval_hot_prefixes": list(self.predictor_eval_hot_prefixes),
                "live_promoted_eval_hot_paths": promoted_hot,
                "live_promoted_eval_cold_paths": promoted_cold,
                "total_promoted_eval_hot_paths": self._promoted_eval_hot_total,
                "total_promoted_eval_cold_paths": self._promoted_eval_cold_total,
                "live_packed_eval_hot_paths": live_packed_hot,
                "live_packed_eval_cold_paths": live_packed_cold,
            }
        )
        return metrics

    def _is_hot_path(self, path: str) -> bool:
        return path in self._native_paths or str(PurePosixPath(path).parent) in self._predicted_hot_dirs

    def _record_predictive_access(
        self,
        backend: StorageBackend,
        path: str,
        trigger: str,
        data: bytes | None = None,
    ) -> bool:
        if self.predictor_strategy == "never_promote" or trigger not in self.promotion_triggers:
            return False
        with self._lock:
            count = self._access_counts.get(path, 0) + 1
            self._access_counts[path] = count
            parent = str(PurePosixPath(path).parent)
            if self.predictor_strategy == "directory_hotset":
                dir_count = self._dir_access_counts.get(parent, 0) + 1
                self._dir_access_counts[parent] = dir_count
                self._dir_accessed_paths.setdefault(parent, set()).add(path)
                distinct_paths = len(self._dir_accessed_paths[parent])
                if (
                    self.predictor_directory_promotion
                    and parent not in self._predicted_hot_dirs
                    and dir_count >= self.predictor_dir_event_threshold
                    and distinct_paths >= self.predictor_dir_distinct_threshold
                ):
                    self._predicted_hot_dirs.add(parent)
                should_promote = (
                    parent in self._predicted_hot_dirs
                    and count >= self.promotion_threshold
                )
            else:
                should_promote = count >= self.promotion_threshold
        if not should_promote:
            return False
        if not self.predictor_promote_existing:
            return False
        return self._promote_path(backend, path, data=data)

    def _promote_path(
        self, backend: StorageBackend, path: str, data: bytes | None = None
    ) -> bool:
        with self._lock:
            if path in self._native_paths:
                return False
            record = self._index.get(path)
            if record is None or record.deleted:
                return False
            self._promotion_attempts += 1
            segment = record.segment
            offset = record.offset
            length = record.length

        try:
            if data is None or len(data) < length:
                self._flush_data_for_segment(backend, segment)
                data = self._read_segment_at(backend, segment, offset, length)
            parent = str(PurePosixPath(path).parent)
            self._ensure_native_parent(backend, parent)
            backend.write_file(path, data)
        except Exception:
            with self._lock:
                self._promotion_failures += 1
            raise

        with self._lock:
            current = self._index.get(path)
            if current is not None and not current.deleted:
                current.deleted = True
                self._tombstones += 1
                index_offset, index_data = self._append_index_record_locked(
                    self._OP_DELETE, path, 0, 0, 0
                )
            else:
                index_offset, index_data = 0, b""
            self._native_paths.add(path)
            self._promoted_paths.add(path)
            parent = str(PurePosixPath(path).parent)
            if self.predictor_directory_promotion:
                self._predicted_hot_dirs.add(parent)
            self._native_bytes += len(data)
            self._native_writes += 1
            self._total_promotions += 1
            if self._matches_eval_hot(path):
                self._promoted_eval_hot_total += 1
            else:
                self._promoted_eval_cold_total += 1
            self._promotion_bytes += len(data)
        if index_data:
            backend.write_at(self._index_log, index_data, index_offset)
        return True

    def _ensure_native_parent(self, backend: StorageBackend, parent: str) -> None:
        with self._lock:
            if parent in self._materialized_native_dirs:
                return
        backend.mkdirs(parent)
        with self._lock:
            self._materialized_native_dirs.add(parent)

    def _matches_eval_hot(self, path: str) -> bool:
        if not self.predictor_eval_hot_prefixes:
            return False
        return any(
            part.startswith(prefix)
            for part in PurePosixPath(path).parts
            for prefix in self.predictor_eval_hot_prefixes
        )


class OraclePolicyBackend:
    """Translate policy pins from logical oracle paths to physical CephFS paths."""

    def __init__(self, storage: OracleColdSegmentStorage, backend: StorageBackend) -> None:
        self._storage = storage
        self._backend = backend

    def set_pin(self, path: str, rank: int) -> None:
        target, deduplicated = self._storage._policy_pin_path(path, rank)
        if deduplicated:
            return
        self._backend.set_pin(target, rank)
        if target != path:
            self._storage._record_translated_pin_success(target, rank)


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
    if name in {"oracle_cold_segments", "hybrid_cold_segments", "hybrid_packed"}:
        return OracleColdSegmentStorage(config)
    if name in {"predictive_cold_segments", "predictive_hybrid", "learned_cold_segments"}:
        return PredictiveColdSegmentStorage(config)
    if name in {"sharded_segments", "sharded_packed"}:
        return ShardedAppendSegmentSmallFileStorage(config)
    raise ValueError(f"unknown storage layer: {name}")
