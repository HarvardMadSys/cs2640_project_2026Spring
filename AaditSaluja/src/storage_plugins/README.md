# Storage Plugins

Storage plugins let benchmark workloads choose between native files and
alternative logical-file layouts.

Use a named storage plugin:

```sh
./src/scripts/run_posix_bench.sh --storage append_segments
./src/scripts/run_posix_bench.sh --storage sharded_segments --storage-opt shard_mode=directory
./src/scripts/run_posix_bench.sh --storage oracle_cold_segments --storage-opt hot_prefixes=hot
```

Or pass a plugin file:

```sh
./src/scripts/run_posix_bench.sh --storage-file src/storage_plugins/append_segments.py
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
- `oracle_cold_segments.py`: keep oracle-hot files native while packing only
  cold/bulk files. The packed side uses a compact batched binary index journal.
  The benchmark workload should name hot directories with a `hot*` prefix, or
  you can override with `--storage-opt hot_prefixes=hot,active`.

Final report result:

- On the paper-ready hot/cold 90/10 workload, native CephFS reached `490.49`
  ops/s, oracle cold packing reached `1098.20` ops/s, and predictive cold
  packing reached `1316.19` ops/s.
- The false-hot follow-up is the main cautionary case: a bad predictor profile
  fell to `188.96` ops/s while no-learning cold packing reached `29632.48`
  ops/s. The report frames prediction as useful only when hot classification is
  conservative enough.
