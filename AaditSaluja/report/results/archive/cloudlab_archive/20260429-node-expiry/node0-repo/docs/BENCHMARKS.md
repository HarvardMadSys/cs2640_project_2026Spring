# Benchmark Plan

Last updated: 2026-04-29

This project uses a small in-repo benchmark runner for local development and
repeatable experiment orchestration. It is not meant to replace established
benchmarks in the final report. Instead, it mirrors the workload shapes we need
now and leaves room to add external `mdtest`, IOR, and Filebench runs on the
later Linux/CloudLab setup.

## Benchmarks and Baselines

Primary baselines:

- Native CephFS with its default metadata behavior.
- Native CephFS with explicit static subtree pins using `ceph.dir.pin`.
- Native CephFS with a simple predictive hot-directory pinning policy.
- Native CephFS through a minimal append-only small-file packing layer.

Primary workload families:

- `mdtest_tree`: an `mdtest`-style directory tree benchmark with create, stat,
  read, and delete phases. `mdtest` is the standard metadata benchmark bundled
  with IOR and is widely used for parallel file-system metadata testing.
- `sprite_lfs_smallfile`: a classic small-file create/read/delete shape based on
  the Sprite LFS small-file benchmark: many 1 KiB files, then read, then delete.
- `filebench_varmail_like`: a Filebench-inspired mail workload with small-file
  create, read/stat, and delete operations. This gives a macro-style workload
  that is less phase-separated than `mdtest`.
- `hotdirs_zipf`: a skewed hot-directory workload for predictive placement. Most
  operations target one directory, with a tail across other directories.

Important external benchmark references:

- IOR/mdtest documentation: https://ior.readthedocs.io/en/3.3/
- IOR/mdtest repository: https://github.com/hpc/ior
- VI4IO mdtest summary: https://www.vi4io.org/tools/benchmarks/mdtest
- CephFS multi-MDS and pinning docs: https://docs.ceph.com/en/squid/cephfs/multimds/
- CephFS subtree pinning blog: https://ceph.io/en/news/blog/2017/new-luminous-cephfs-subtree-pinning/
- Filebench overview: https://www.usenix.org/publications/login/spring2016/tarasov
- File-system benchmark survey, including Sprite LFS small-file benchmark:
  https://www.filesystems.org/docs/fsbench/

## Local Runner

Run the POSIX backend on any local path:

```sh
./scripts/run_posix_bench.sh --suite quick
```

Run the local Docker/CephFS backend through `libcephfs` inside the monitor
container:

```sh
./scripts/cephfs_up.sh
./scripts/run_cephfs_bench.sh --suite quick
```

Outputs are written as JSON and CSV under `results/` by default. The JSON file
contains:

- run metadata: host, backend, root, timestamps, arguments.
- phase rows: operation count, elapsed time, throughput, mean/p50/p95/p99
  operation latency.
- Ceph snapshots from `ceph df` and `ceph fs status` when available.

The container image exposes `cephfs` only through Python 2, so
`scripts/run_cephfs_bench.sh` uses a small Python-2-compatible runner. The main
runner in `src/cephfs_metadata/benchmark_runner.py` is the preferred path for
POSIX-mounted CephFS on Linux.

## Placement Policies

The Python 3 runner resolves policy names to plugin files under `policies/`.
Use `--policy <name>` for `policies/<name>.py`, or `--policy-file <path>` for an
explicit file.

- `none`: native CephFS behavior. This is the default baseline.
- `static`: explicit static subtree pinning. After benchmark directories are
  created, the policy sets `ceph.dir.pin` round-robin across `--pin-ranks`.
- `predictive`: a simple sliding-window hot-directory tracker. It observes the
  parent directory of each metadata operation and pins a directory once it has at
  least `--hot-threshold` operations inside the last `--hot-window` operations.
- `LLM_policy`: a separate heuristic policy file. It does not call an LLM at
  runtime; it encodes a candidate policy that combines hotness, parent fanout,
  and cooldown to avoid over-pinning noisy directories.

Policy decisions are stored in each JSON result under `policy_events`. CSV result
rows include the active policy name.

Static baseline:

```sh
./scripts/run_cephfs_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --file-count 10000 \
  --file-size 512 \
  --dirs 64 \
  --policy static \
  --pin-ranks 0,1
```

Predictive baseline:

```sh
./scripts/run_cephfs_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --file-count 10000 \
  --file-size 512 \
  --dirs 64 \
  --policy predictive \
  --pin-ranks 0,1 \
  --hot-window 256 \
  --hot-threshold 96
```

Explicit policy file:

```sh
./scripts/run_posix_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --root /mnt/cephfs/bench \
  --policy-file policies/example_predictive.py \
  --pin-ranks 0,1 \
  --policy-opt hot_window=256 \
  --policy-opt hot_threshold=96
```

Policy plugin files expose `create_policy(config)` and return an object with
`on_dirs_created(backend, dirs)`, `before_operation(backend, operation, path)`,
and `events()`. See `policies/README.md` and `policies/example_predictive.py`.

LLM-style heuristic policy:

```sh
./scripts/run_posix_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --root /mnt/cephfs/bench \
  --policy LLM_policy \
  --pin-ranks 0,1 \
  --policy-opt hot_threshold=96 \
  --policy-opt window=256 \
  --policy-opt fanout_threshold=8
```

## Small-File Packing Layer

The Python 3 runner supports storage plugins with `--storage <name>` or
`--storage-file <path>`. The minimal packing layer is
`storage_plugins/append_segments.py`.

Native file layout:

```sh
./scripts/run_posix_bench.sh \
  --suite custom \
  --workload sprite_lfs_smallfile \
  --root /mnt/cephfs/bench \
  --storage native
```

Packed logical-file layout:

```sh
./scripts/run_posix_bench.sh \
  --suite custom \
  --workload sprite_lfs_smallfile \
  --root /mnt/cephfs/bench \
  --storage append_segments \
  --segment-size 67108864
```

The packing layer stores logical file payloads in append-only segment files under
`<workload-root>/__packed/segments/` and records logical path mappings in a
separate append-only JSON-lines index at `<workload-root>/__packed/index.log`.
The JSON result includes `storage_metrics`, including segment count, index-log
bytes, logical records, live records, live bytes, and tombstones.

## Example Commands

Quick smoke-sized local run:

```sh
./scripts/run_cephfs_bench.sh --suite quick
```

Custom mdtest-style zero-byte metadata run:

```sh
./scripts/run_cephfs_bench.sh \
  --suite custom \
  --workload mdtest_tree \
  --file-count 5000 \
  --file-size 0 \
  --depth 3 \
  --branching 8
```

Custom skewed hot-directory run:

```sh
./scripts/run_cephfs_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --file-count 10000 \
  --file-size 512 \
  --dirs 64
```

Static subtree pinning baseline, useful once multiple active MDS ranks exist:

```sh
./scripts/run_posix_bench.sh \
  --suite custom \
  --workload hotdirs_zipf \
  --root /mnt/cephfs/bench \
  --policy static \
  --pin-ranks 0,1
```

## Caveats

- The local Docker setup is single-node, single-MDS, amd64 emulated on Apple
  Silicon. Use it for correctness and instrumentation only.
- The local container cannot run POSIX mounted benchmarks because FUSE is not
  available. The `libcephfs` backend is the local workaround.
- The local container runner supports the built-in policies, but Python 3 policy
  and storage plugins are intended for the POSIX/Linux path.
- `ceph df` pool occupancy can return to its starting value after cleanup. For
  final write-volume graphs, collect cumulative OSD/perf counters or time-series
  pool I/O samples during the run on the Linux cluster.
- Large final runs should use working sets that exceed CPU cache effects and
  avoid inline-file artifacts. The `standard` suite is still modest; final
  experiments should scale counts and clients significantly.
