# Storage Plugins

Storage plugins let benchmark workloads choose between native files and
alternative logical-file layouts.

Use a named storage plugin:

```sh
./scripts/run_posix_bench.sh --storage append_segments
./scripts/run_posix_bench.sh --storage sharded_segments --storage-opt shard_mode=directory
```

Or pass a plugin file:

```sh
./scripts/run_posix_bench.sh --storage-file storage_plugins/append_segments.py
```

Plugin files expose:

```py
def create_storage(config):
    ...
```

`config` is a `cephfs_metadata.storage.StorageConfig`. The returned object must
implement the `StorageLayer` methods.

Current plugins:

- `append_segments.py`: single append-only segment stream plus one JSON-lines
  index log.
- `sharded_segments.py`: independent segment/index shards. Use
  `--storage-opt shard_mode=directory` for one shard per logical parent
  directory, or `--storage-opt shard_mode=hash --storage-opt shard_count=8` to
  spread even a single hot directory across fixed hash shards.
