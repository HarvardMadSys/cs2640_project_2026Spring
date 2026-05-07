# CS2640 Final Project

Metadata management experiments for CephFS, focused on metadata-heavy small-file workloads.

## Environment

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Local CephFS

Docker Desktop must be running.

```sh
./scripts/cephfs_up.sh
./scripts/cephfs_status.sh
./scripts/cephfs_smoke.sh
```

The local setup is a single-node, containerized development cluster intended for quick functional work. Final performance experiments should run on CloudLab or another Linux cluster with multiple MDS daemons and larger working sets.

## Benchmarks

Local POSIX smoke run:

```sh
./scripts/run_posix_bench.sh --suite quick
```

Local CephFS run through the container's `libcephfs` binding:

```sh
./scripts/run_cephfs_bench.sh --suite quick
```

Results are written under `results/` as JSON and CSV. See `docs/BENCHMARKS.md`
for the benchmark choices, references, and caveats.

Policy baselines:

```sh
./scripts/run_cephfs_bench.sh --suite custom --workload hotdirs_zipf \
  --policy static --pin-ranks 0

./scripts/run_cephfs_bench.sh --suite custom --workload hotdirs_zipf \
  --policy predictive --pin-ranks 0 --hot-window 128 --hot-threshold 64
```

Python 3 plugin examples for the later mounted Linux path:

```sh
./scripts/run_posix_bench.sh --suite custom --workload hotdirs_zipf \
  --policy LLM_policy --pin-ranks 0,1

./scripts/run_posix_bench.sh --suite custom --workload sprite_lfs_smallfile \
  --storage append_segments --segment-size 67108864
```
