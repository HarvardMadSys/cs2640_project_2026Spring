# Project Notes

Last updated: 2026-04-29

## Goal

Build and evaluate metadata-management techniques for CephFS, especially for metadata-heavy small-file workloads.

The proposal has two main implementation tracks:

1. Predictive metadata placement: track recent per-directory metadata activity, identify emerging hot directories, and proactively pin or migrate subtrees before CephFS's reactive balancer becomes a bottleneck.
2. Small-file packing layer: reduce metadata pressure by storing many tiny logical files inside larger append-only segment files, with a separate lookup index.

The project should first demonstrate CephFS metadata bottlenecks clearly, then compare proposed changes against native CephFS and static subtree pinning.

## Source Materials

- Proposal: `Proposal.pdf`
- Proposal feedback: `ProposalFeedback.txt`
- Midway report: `MidwayReport.pdf`
- Midway feedback: `MidwayFeedback.txt`

Important constraint from user on 2026-04-29: none of the midway code is available. Treat this repo as a fresh implementation.

## Proposal Summary

Problem: CephFS is useful for large shared storage, but metadata-heavy workloads remain painful. CephFS distributes directory subtrees across MDS daemons using dynamic subtree partitioning, but this is mostly reactive. Tiny-file workloads can overload metadata paths even when payload I/O is modest.

Planned baselines:

- Native CephFS default balancer.
- CephFS with static subtree pinning.

Planned workloads:

- `mdtest`.
- Custom create/stat/read/delete scripts over large numbers of small files.
- Mixed workloads to check whether metadata improvements hurt normal I/O.

Planned metrics:

- Metadata throughput.
- p95/p99 latency for create/open/stat/delete/read where available.
- MDS CPU and load distribution.
- Metadata-pool write volume versus data-pool write volume.
- Lookup overhead and compaction overhead for the small-file overlay.

Goal tiers:

- 75%: Set up CephFS, reproduce a metadata bottleneck with `mdtest`, implement a working predictive policy.
- 100%: Implement both predictive placement and small-file packing, compare against native CephFS, produce clear throughput, tail-latency, and MDS-load graphs.
- 125%: Train a small neural network to predict emerging metadata hotspots from recent access patterns.

## Feedback To Incorporate

Proposal feedback:

- A first measurement of Ceph performance would strengthen motivation.
- Workload choice matters; public file-system traces are limited, but object-cache traces may be available from the instructor.
- Need an AI-use report describing what worked and did not work with the coding agent.

Midway feedback:

- Larger working sets are important. Small working sets may hit CPU cache and can produce misleading results.
- Tiny files may be stored inline in some cases.
- Always include references.
- Use larger fonts in final figures.
- Figure captions should include a clear takeaway.
- Final presentation should provide more background on the problem.

## Midway Report Summary

Midway work focused on literature review and preliminary CephFS bottleneck experiments.

Literature reviewed:

- Mantle: programmable metadata placement in CephFS; custom placement can outperform default balancer.
- Lunule+ and related work: CephFS subtree partitioning is reactive and migration-heavy.
- IndexFS/Giga+: hash-based distribution can balance load but sacrifices locality. A hash-based distribution baseline may be useful.
- TableFS: stores metadata and tiny files in a key-value store using an LSM-tree to reduce metadata overhead.
- DeltaFS: log-structured ingest and compaction, close to the proposed packing layer.
- HDFS HAR/AHAR: simpler file grouping; useful tradeoff between reduced metadata pressure and lookup overhead.
- HopsFS/HopsFS++ still needs review, especially colocating small-file data with metadata.

Reported CloudLab setup:

- 4 nodes total.
- 1 client-only benchmark driver.
- 3 storage/metadata server nodes.
- Each server node had mon, mds, and osd.
- MDS setup: 2 active, 1 standby.
- Ceph also added a monitor to the client node by default; this should be removed for consistency in final runs.

Preliminary workload dimensions:

- File count varied while holding total data size fixed at 64 MiB.
- Random versus sequential access.
- Read-heavy, write-heavy, and balanced workloads.
- Each workload performed create/write, stat, read, and delete.

Preliminary result:

- Metadata-pool writes increased clearly as file count increased while total payload stayed fixed.
- Higher file counts increased metadata-style work and variation across workload mixes.

Important caveat: the 64 MiB total data size was only for quick prototyping. Future runs should use larger datasets to avoid CPU-cache and inline-file artifacts.

## Environment Setup Log

2026-04-29:

- Created local Python virtual environment at `.venv`.
- Installed `pypdf==6.10.2` in the venv for extracting text from proposal/report PDFs.
- Added `requirements.txt`.
- Added project package stub at `src/cephfs_metadata`.
- Docker Desktop was started with `open -a Docker`.
- Verified Docker Compose version: v2.40.3-desktop.1.
- Local Docker engine version: 29.0.1.
- First CephFS container startup pulled `quay.io/ceph/daemon:latest-nautilus`; Docker warned that the image is `linux/amd64` on an Apple Silicon `linux/arm64/v8` host, so it runs under emulation.
- First status check reached mon/mgr and created the `cephfs` filesystem, but OSD/MDS exited. Fixes applied: export bootstrap keyrings from the monitor before starting OSD/MDS, and set `MDS_NAME=mds-local` because the daemon-generated hostname started with a digit and Ceph rejects MDS ids that start with numeric digits.
- After fixes, local CephFS reached `HEALTH_WARN` with one active MDS (`mds-local`), one OSD up/in, and 16 PGs `active+clean`. The remaining warning is expected for this dev-only setup: pools have replica size 1.
- `ceph-fuse /mnt/cephfs` inside the monitor container failed with `fuse: device not found`; Docker Desktop is not exposing FUSE in that container. Treat local Docker as a Ceph service/control-plane setup. Run actual POSIX-mounted workload tests on CloudLab/Linux, or add a privileged client container on a Linux host with `/dev/fuse`.
- Functional smoke test using the container's Python `libcephfs` binding succeeded. It created `/smoke/hello.txt`, read back `cephfs-ok`, and removed the file/directory. Reusable command: `./scripts/cephfs_smoke.sh`.
- Added benchmark runner documentation at `docs/BENCHMARKS.md`.
- Added a standard-library Python benchmark runner at `src/cephfs_metadata/benchmark_runner.py` for POSIX-mounted runs.
- Added a Python-2-compatible local `libcephfs` runner at `src/cephfs_metadata/cephfs_py2_benchmark.py` because the Nautilus container exposes the `cephfs` binding only to Python 2.
- Added benchmark entry points:
  - `./scripts/run_posix_bench.sh`
  - `./scripts/run_cephfs_bench.sh`
- Verified a local quick CephFS benchmark run through `libcephfs`; it wrote `results/cephfs-20260429-013536.json` and `results/cephfs-20260429-013536.csv`.
- Added metadata placement policies:
  - `none`: native behavior.
  - `static`: explicit `ceph.dir.pin` baseline across `--pin-ranks`.
  - `predictive`: sliding-window hot-directory tracker that pins directories when they cross a configurable operation threshold.
- Added Python 3 policy plugin support through `--policy-file`, plus an example at `policies/example_predictive.py`.
- Verified local CephFS policy runs:
  - Static policy result: `results/cephfs-20260429-015218-78100.json`.
  - Predictive policy result: `results/cephfs-20260429-015218-78101.json`.
- Converted Python 3 policy selection to named plugin files under `policies/`.
  Current files: `none.py`, `static.py`, `predictive.py`, `LLM_policy.py`, and
  `example_predictive.py`.
- Added `LLM_policy` as a separate policy plugin. It does not call an LLM at
  runtime; it is a hand-coded heuristic combining sliding-window hotness, parent
  fanout, and cooldown before setting `ceph.dir.pin`.
- Added a storage plugin interface and minimal small-file packing layer:
  - Core storage code: `src/cephfs_metadata/storage.py`.
  - Plugin file: `storage_plugins/append_segments.py`.
  - The packed layer stores logical files in append-only segment files and writes
    a separate JSON-lines index log.
- Verified Python 3 POSIX smoke runs for:
  - Named static policy plugin with native storage.
  - Named `LLM_policy`.
  - Packed `append_segments` storage.
  - Explicit `--policy-file` plus `--storage-file`.
- CloudLab access/topology inventory:
  - node0/client: `node0.ceph.cs2640.emulab.net`, public `pc318.emulab.net`, private `10.10.1.1`.
  - node1/server: `node1.ceph.cs2640.emulab.net`, public `pc258.emulab.net`, private `10.10.1.2`.
  - node2/server: `node2.ceph.cs2640.emulab.net`, public `pc252.emulab.net`, private `10.10.1.3`.
  - node3/server: `node3.ceph.cs2640.emulab.net`, public `pc208.emulab.net`, private `10.10.1.4`.
  - All nodes run Ubuntu 22.04.2 LTS with Python 3.10.12 and passwordless sudo for `aadits`.
  - No Ceph or Docker command was initially present in PATH.
  - Each node has root on `sda3`; each node has an unused `sdb` disk of 136.7G. Plan: use `sdb` only on node1-node3 as OSD devices. node0 remains client-only.
  - Direct SSH works to node1-node3 using `/Users/aadit/.ssh/id_ed25519_cs2640`.
- CloudLab Ceph deployment progress:
  - Installed `ceph-common`, `cephadm`, `podman`, and `lvm2` on node1-node3.
  - Installed `ceph-common` and `ceph-fuse` on node0.
  - `cephadm bootstrap` on node1 failed because the official `quay.io/ceph/ceph:v17` container requires x86-64-v2 CPU support: `Fatal glibc error: CPU does not support x86-64-v2`.
  - Native Ubuntu Ceph packages were also not viable on this CloudLab hardware: `ceph-mon --mkfs` failed with `Illegal instruction`.
  - Plan adjusted to the older `quay.io/ceph/daemon:latest-nautilus` image, which runs on these CPUs.
  - Formatted `/dev/sdb` as XFS on node1-node3 and mounted it at `/opt/cs2640-ceph/lib/osd` for directory-backed OSD state.
  - Brought up a working CloudLab CephFS cluster:
    - Monitor: node1.
    - Manager: node1.
    - OSDs: node1, node2, node3, one OSD per node.
    - MDS: node2 and node3 active, node1 standby.
    - Pools: `cephfs_data` with 32 PGs and `cephfs_metadata` with 16 PGs.
  - Mounted CephFS on node0 at `/mnt/cephfs` using `ceph-fuse`.
  - Created benchmark workspace `/mnt/cephfs/cs2640-bench` owned by `aadits:cs2640`.
  - Synced the repo to node0 at `~/FinalProj`.
  - Verified a node0 POSIX benchmark smoke run against CephFS:
    - JSON: `~/FinalProj/results/cloudlab-smoke.json`.
    - CSV: `~/FinalProj/results/cloudlab-smoke.csv`.
  - Verified policy plugin smoke runs on the real CephFS mount:
    - Static pinning: `~/FinalProj/results/cloudlab-static-smoke.json`, with 3 pin events.
    - Predictive hotness: `~/FinalProj/results/cloudlab-predictive-smoke.json`, with 1 hot-directory pin event.
    - Note: `hotdirs_zipf` currently uses `--file-count`; `--ops` is only used by the varmail-like workload.
  - Current cluster status after smoke run:
    - `HEALTH_OK` after setting `auth_allow_insecure_global_id_reclaim=false`.
    - `3 osds: 3 up, 3 in`.
    - `cephfs:2 {0=node2=up:active,1=node3=up:active} 1 up:standby`.
  - Persisted the node1-node3 `/dev/sdb` OSD mounts in `/etc/fstab`.
  - Important operational note: the Ceph daemons are running as Podman containers rather than systemd-managed services, so a reboot still requires manual container restart unless we add units.
- CloudLab policy benchmark matrix:
  - Ran 16 benchmark cases on node0 against `/mnt/cephfs`:
    - Policies: `none`, `static`, `predictive`, `LLM_policy`.
    - Workloads: `mdtest_tree`, `sprite_lfs_smallfile`, `hotdirs_zipf`, `filebench_varmail_like`.
    - Results directory on node0: `~/FinalProj/results/cloudlab-policy-matrix-20260429-065248/`.
    - Local copied summaries:
      - `results/cloudlab-policy-matrix-20260429-065248/analysis_summary.md`.
      - `results/cloudlab-policy-matrix-20260429-065248/analysis_summary.csv`.
      - `results/cloudlab-policy-matrix-20260429-065248/analysis_phases.csv`.
  - Overall ops/sec from first matrix:
    - `mdtest_tree`: none 166.8, static 115.7, predictive 227.4, `LLM_policy` 205.6.
    - `sprite_lfs_smallfile`: none 126.6, static 63.4, predictive 188.6, `LLM_policy` 89.0.
    - `hotdirs_zipf`: none 336.6, static 41.5, predictive 29.0, `LLM_policy` 35.1.
    - `filebench_varmail_like`: none 56.0, static 17.2, predictive 74.8, `LLM_policy` 34.6.
  - Important interpretation caveat:
    - Predictive recorded zero pin events on `mdtest_tree`, `sprite_lfs_smallfile`, and `filebench_varmail_like`, so apparent speedups there are not placement-policy wins.
    - A late default repeat showed large run-order/cache variance:
      - `mdtest_tree`: first default 166.8 ops/s, late default 276.4 ops/s.
      - `sprite_lfs_smallfile`: first default 126.6 ops/s, late default 114.0 ops/s.
      - `hotdirs_zipf`: first default 336.6 ops/s, late default 208.2 ops/s.
      - `filebench_varmail_like`: first default 56.0 ops/s, late default 62.4 ops/s.
    - Strongest signal so far is negative: static pinning consistently hurts, and dynamic pinning on `hotdirs_zipf` hurts badly when it triggers during the measured workload.
    - Next benchmark iteration should randomize run order, run at least 3 repeats per policy/workload, and add a warmup phase before measured phases.
- Benchmark pressure/resume notes:
  - Existing matrix metadata-pool deltas were not trustworthy because the runner
    cleaned up files/directories before the after-snapshot, and node0's user-level
    `ceph` CLI temporarily lacked access to the admin keyring.
  - Restored node0 CLI access by copying `ceph.conf` and
    `ceph.client.admin.keyring` back into `/etc/ceph`, then setting the keyring
    group to `cs2640` and mode `640`.
  - Added `--keep-data` to `benchmark_runner.py`. In this mode the runner skips
    cleanup delete phases and leaves the benchmark namespace intact, so Ceph pool
    deltas reflect live metadata/data pressure.
  - Added simple policy variants:
    - `policies/static_top.py`: pins only top-level benchmark subtrees.
    - `policies/prepin_hotset.py`: pre-pins a declared small hot set.
    - `policies/predictive_safe.py`: create-only hotness tracker with higher
      thresholds to reduce reactive migration during read/stat/delete phases.
  - Retained-data stress probes with 5,000 files and 8 workers showed the
    workloads are large enough for a first pressure pass:
    - `sprite_lfs_smallfile`: ~214.9 ops/s, metadata delta ~11.5 MB used, data
      delta 5,000 objects.
    - `hotdirs_zipf`: ~312.3 ops/s, metadata delta ~7.5 MB used, data delta
      5,000 objects.
  - Started the improvement matrix, then stopped at the user's request after the
    first operation completed. No further variants were launched.
    - Partial run directory: `~/FinalProj/results/cloudlab-improvement-matrix-20260429-075053/`.
    - Completed artifact: `sprite_lfs_smallfile__default.json` and `.csv`.
    - Result: elapsed 65.0s, ~231.2 ops/s, metadata delta ~4.3 MB used and 48
      objects, data delta 5,001 objects.
  - Resume command shape:
    - Continue from the same idea, but use a new output directory to avoid
      appending to the partial run.
    - Compare `default`, `static_top`, `prepin_hotset`, `predictive_safe`, and
      `append_segments` on `sprite_lfs_smallfile` and `hotdirs_zipf` with
      `--keep-data`, 5,000 files, 8 workers.
- CloudLab improvement matrix completed:
  - Results directory on node0:
    `~/FinalProj/results/cloudlab-improvement-matrix-20260429-081452/`.
  - Local copied summaries:
    - `results/cloudlab-improvement-matrix-20260429-081452/analysis_summary.md`.
    - `results/cloudlab-improvement-matrix-20260429-081452/analysis_summary.csv`.
    - `results/cloudlab-improvement-matrix-20260429-081452/analysis_phases.csv`.
  - `sprite_lfs_smallfile` at 5,000 files / 8 workers / retained data:
    - default: 259.7 ops/s, p95 47.6 ms.
    - `static_top`: 217.3 ops/s, 0.84x default, 32 pin events.
    - `prepin_hotset`: 228.8 ops/s, 0.88x default, 1 pin event.
    - `predictive_safe`: 293.2 ops/s, 1.13x default, 0 pin events.
    - `append_segments`: 175.3 ops/s, 0.68x default, 1 physical segment and
      3 data objects instead of 5,000 data objects.
  - `hotdirs_zipf` at 5,000 files / 8 workers / retained data:
    - default: 288.7 ops/s, p95 46.6 ms.
    - `static_top`: 90.9 ops/s, 0.32x default, 64 pin events.
    - `prepin_hotset`: 104.8 ops/s, 0.36x default, 1 pin event.
    - `predictive_safe`: 139.0 ops/s, 0.48x default, 1 pin event.
    - `append_segments`: 189.9 ops/s, 0.66x default, 1 physical segment and
      2 data objects instead of 5,000 data objects.
  - Interpretation:
    - Simple pinning variants still do not improve performance on this setup.
      They primarily hurt create throughput; stat/read phases are close to
      default.
    - `predictive_safe` looked faster than default on broad small files, but it
      recorded zero pin events, so treat that as run variance or cache effects,
      not a policy win.
    - `append_segments` is interesting for metadata/data-object reduction: it
      collapses 5,000 logical files into 1 segment plus an index log. However,
      create throughput is currently worse because writes serialize through the
      in-memory segment allocator and append-only JSON index.
    - Best next engineering target is the packing layer, not subtree pinning:
      shard segment/index logs per worker or per directory, batch index writes,
      and then rerun. For placement policies, avoid runtime migration; only
      pre-place coarse subtrees when the workload layout is known before writes.
- Sharded packing follow-up:
  - Added `ShardedAppendSegmentSmallFileStorage` to
    `src/cephfs_metadata/storage.py`.
  - Added plugin wrapper `storage_plugins/sharded_segments.py`.
  - Supported modes:
    - `shard_mode=directory`: one segment/index shard per logical parent
      directory.
    - `shard_mode=hash`: fixed number of path-hash shards, e.g.
      `shard_count=8`, which also spreads one hot directory.
  - Local smoke tests passed for both directory and hash sharding.
  - CloudLab matrix directory:
    `~/FinalProj/results/cloudlab-sharded-storage-20260429-083822/`.
  - Local summary:
    `results/cloudlab-sharded-storage-20260429-083822/analysis_summary.md`.
  - Results at 5,000 files / 8 workers / retained data:
    - `sprite_lfs_smallfile`:
      - native: 272.0 ops/s, p95 47.0 ms, 5,000 data objects.
      - `append_segments`: 197.0 ops/s, p95 62.0 ms, 3 data objects.
      - `sharded_directory`: 111.9 ops/s, p95 159.0 ms, 64 data objects,
        32 shards.
      - `sharded_hash8`: 115.0 ops/s, p95 174.2 ms, 16 data objects, 8 shards.
    - `hotdirs_zipf`:
      - native: 267.6 ops/s, p95 47.9 ms, 5,000 data objects.
      - `append_segments`: 187.4 ops/s, p95 60.6 ms, 2 data objects.
      - `sharded_directory`: 119.7 ops/s, p95 96.5 ms, 128 data objects,
        64 shards.
      - `sharded_hash8`: 89.9 ops/s, p95 261.4 ms, 16 data objects, 8 shards.
  - Interpretation:
    - Sharding segment/index logs did not improve performance. It made create
      throughput substantially worse.
    - The original single-stream `append_segments` remains the fastest packed
      variant despite its global allocator/index.
    - Phase data shows packed stat operations are effectively free, but create
      operations dominate and are slower than native:
      - Native small-file create: 171.6 ops/s.
      - Single append-segment create: 96.2 ops/s.
      - Directory-sharded create: 42.5 ops/s.
      - Hash-sharded create: 43.6 ops/s.
    - Likely cause: extra physical files/directories and index logs add CephFS
      metadata work faster than they reduce Python-side lock contention. The
      next packing improvement should not add more CephFS files; it should batch
      index writes or use fewer/larger write operations inside one or a small
      number of physical files.
- Pinning diagnosis follow-up:
  - Question tested: why do pinning strategies perform worse than default even
    on skewed hot-directory workloads?
  - Diagnosis:
    - `ceph.dir.pin` chooses which MDS rank owns a subtree; it does not split one
      hot directory across ranks.
    - A single hot directory is still a single metadata authority bottleneck.
    - Explicit pinning adds xattr/control work and can trigger subtree migration
      or cross-rank forwarding.
    - Default CephFS already has an active authority/cache placement for the
      directory. For short create-heavy workloads, forcing a pin can be worse
      than leaving that authority in place.
  - Rank-choice diagnosis run:
    `~/FinalProj/results/cloudlab-pin-rank-diagnosis-20260429-092515/`.
    - default: 244.2 ops/s.
    - `prepin_hotset` rank 0: 205.5 ops/s.
    - `prepin_hotset` rank 1: 104.5 ops/s.
    - `predictive_safe` rank 0: 82.2 ops/s.
    - `predictive_safe` rank 1: 103.8 ops/s.
    - Takeaway: rank choice matters, but both fixed-rank prepinning and
      reactive pinning were slower than default.
  - Implemented `policies/prepin_colocated_hotset.py` to pin both workload root
    and hot directory to the same rank, testing whether parent/child co-location
    would reduce forwarding.
  - Co-location diagnosis run:
    `~/FinalProj/results/cloudlab-colocated-fix-20260429-093126/`.
    - default: 321.9 ops/s.
    - hot-dir-only prepin rank 0: 103.9 ops/s.
    - root+hot co-located rank 0: 86.3 ops/s.
    - root+hot co-located rank 1: 114.5 ops/s.
    - Takeaway: co-location did not fix the issue. It likely over-constrained
      CephFS and made create authority/cache behavior worse.
  - Current conclusion:
    - For our `hotdirs_zipf` workload, pinning is not an improvement mechanism.
      The workload is hot within one directory, and export-pinning can only move
      that hot directory to a rank, not parallelize it.
    - More promising placement experiments would need multiple hot top-level
      directories that can be pre-placed before writes begin, or a workload where
      the default balancer demonstrably places hot subtrees poorly.

## CephFS Setup Notes

Local development setup is under `docker-compose.cephfs.yml`.

It uses `quay.io/ceph/daemon:latest-nautilus` because the old Docker Hub `ceph/daemon` image is no longer updated and the Docker Hub documentation states that newer daemon images moved to Quay after August 2021. This is a development convenience, not the final evaluation target.

Services:

- `mon`: monitor at `172.28.0.10`.
- `mgr`: manager at `172.28.0.11`.
- `osd`: directory-backed OSD at `172.28.0.12`.
- `mds`: metadata server at `172.28.0.13`, with `CEPHFS_CREATE=1`.

Commands:

```sh
./scripts/cephfs_up.sh
./scripts/cephfs_status.sh
docker compose -f docker-compose.cephfs.yml logs -f
```

Runtime state is stored in `runtime/ceph/` and ignored by git.

Important: final performance experiments should not use this single-node Docker setup. Use CloudLab or another Linux cluster with multiple active MDS daemons and large enough working sets.

## Next Technical Steps

1. Bring the local CephFS stack to healthy state.
2. Reproduce a local metadata-pressure trend with the new benchmark runner.
3. Refine the predictive policy and compare thresholds/windows locally.
4. Expand CloudLab benchmark workflow beyond the smoke run.
5. Add compaction and recovery tests for the small-file packing layer.
6. Build final plots with larger fonts and captions that state the takeaway.
7. Maintain an AI-use report as work progresses.
