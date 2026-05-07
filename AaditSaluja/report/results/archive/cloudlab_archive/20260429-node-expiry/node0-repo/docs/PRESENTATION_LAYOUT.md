# Presentation Layout: CephFS Metadata Management Project

Last updated: 2026-04-29

This document is a draft structure for the final presentation. It is written as
slide content plus the core points each slide should make.

## 1. Title

**Toward Better Metadata Handling for CephFS Small-File Workloads**

Team/project context:

- CS2640 final project.
- Goal: understand and improve CephFS behavior under metadata-heavy workloads.
- Main ideas explored so far:
  - Metadata placement policies using CephFS directory pinning.
  - A small-file packing layer using append-only segment files.

## 2. Problem Background

CephFS separates file data from filesystem metadata:

- Data is stored in RADOS through OSDs.
- Metadata operations are handled by MDS daemons.
- Metadata includes directory lookup, inode creation, stat, unlink, subtree
  ownership, and client capability state.

Why this matters:

- Many real workloads are not limited by bytes transferred.
- They are limited by file and directory operations:
  - create many tiny files,
  - stat many paths,
  - read small payloads,
  - delete or update many entries.
- In these cases, the metadata pool and MDS daemons can become the bottleneck
  even when data volume is modest.

## 3. CephFS Metadata Scaling Model

CephFS can run multiple MDS daemons.

- Active MDS ranks divide directory subtrees.
- CephFS can rebalance subtrees dynamically.
- Users can also set `ceph.dir.pin` to export-pin directories to specific MDS
  ranks.

Important tradeoff:

- Dynamic subtree migration can help balance load, but it is reactive.
- Manual pinning can improve locality if the layout is known, but bad pinning can
  fragment metadata ownership or cause expensive migration.

Project question:

> Can simple workload-aware policies improve metadata throughput or reduce
> metadata pressure compared with default CephFS behavior?

## 4. System Setup

CloudLab topology:

- Four nodes total.
- `node0`: client-only benchmark driver.
- `node1`: monitor, manager, OSD, standby MDS.
- `node2`: OSD, active MDS rank 0.
- `node3`: OSD, active MDS rank 1.

CephFS layout:

- 3 OSDs total, one per storage node.
- 2 active MDS ranks, 1 standby MDS.
- CephFS mounted on node0 at `/mnt/cephfs`.
- Benchmark workspace: `/mnt/cephfs/cs2640-bench`.
- Pools:
  - `cephfs_data`
  - `cephfs_metadata`

Operational notes:

- CloudLab CPUs could not run current Ceph v17/Quincy binaries or containers
  because of old x86 instruction support.
- We used `quay.io/ceph/daemon:latest-nautilus`, which works on this hardware.
- The cluster is currently healthy: 3 OSDs up/in, 2 active MDS ranks, 1 standby.

## 5. Implementation Progress

Completed infrastructure:

- Working 4-node CloudLab CephFS deployment.
- POSIX benchmark runner for mounted CephFS.
- JSON and CSV output for each run.
- Plugin interface for metadata placement policies.
- Plugin interface for storage layers.
- Retained-data benchmark mode with `--keep-data`.

Why `--keep-data` matters:

- Earlier benchmark runs cleaned up files before the after-snapshot.
- That made metadata-pool object deltas look like zero.
- Retained-data runs leave namespaces in place, so pool deltas reflect live
  metadata/data pressure.

## 6. Workloads

We use synthetic workloads designed to isolate metadata behavior.

`mdtest_tree`:

- Creates a tree of directories.
- Distributes files across the tree.
- Measures create/stat/read/delete phases.
- Useful for broad namespace pressure.

`sprite_lfs_smallfile`:

- Inspired by classic small-file metadata benchmarks.
- Creates many small files across many directories.
- Measures create/stat/read phases, and delete unless `--keep-data` is enabled.
- Good for broad small-file pressure.

`hotdirs_zipf`:

- Creates many files with skew toward one hot directory.
- Measures skewed create and stat phases.
- Good for hot-directory placement experiments.

`filebench_varmail_like`:

- Mixed create/stat/read/delete workload.
- Models mailbox-style churn.
- Useful for checking whether a policy hurts mixed behavior.

## 7. Metrics

Primary metrics:

- Throughput: aggregate operations per second.
- Serving time proxy: mean, p50, p95, and p99 operation latency.
- Policy behavior:
  - number of pin events,
  - pin failures,
  - whether pinning happened before or during measured work.
- Metadata/data pressure:
  - metadata pool bytes/object deltas,
  - data pool object deltas,
  - MDS cache scale during runs.

Important measurement caveats:

- Ceph background compaction/reclaim can make metadata byte deltas noisy.
- Data object counts are more stable for comparing packing versus native files.
- Run-order/cache variance is real; later runs should use randomization and
  repeated trials.

## 8. Policy and Storage Variants

Default:

- Native CephFS behavior.
- No explicit directory pins.

`static`:

- Pins every benchmark-created directory round-robin across MDS ranks.
- Baseline for explicit subtree pinning.

`static_top`:

- Pins only top-level benchmark subtrees.
- Intended to reduce fragmentation versus pinning every directory.

`prepin_hotset`:

- Pins a small declared hot set before measured operations.
- Models a production case where a scheduler/operator knows the hot directory.

`predictive`:

- Sliding-window hot-directory tracker.
- Pins a directory once recent operation count crosses a threshold.

`predictive_safe`:

- Conservative version of predictive placement.
- Only reacts to create-heavy signals.
- Uses higher thresholds to avoid pinning during stat/read/delete phases.

`LLM_policy`:

- Hand-coded heuristic, not a runtime LLM call.
- Combines hotness, fanout, and cooldown signals.
- Kept as a separate file to test policy iteration.

`append_segments` storage:

- Small-file packing layer.
- Logical files are stored inside append-only segment files.
- A JSON-lines index maps logical paths to `(segment, offset, length)`.
- Goal: reduce object and metadata pressure from many tiny files.

## 9. First Policy Matrix: What We Learned

Initial matrix:

- Policies: default, static, predictive, `LLM_policy`.
- Workloads: `mdtest_tree`, `sprite_lfs_smallfile`, `hotdirs_zipf`,
  `filebench_varmail_like`.

Headline findings:

- Static pinning consistently hurt performance.
- Dynamic pinning hurt badly when it triggered during the hot-directory workload.
- Some predictive runs looked faster but recorded zero pin events, so those
  apparent wins are likely run-order/cache variance rather than policy benefit.

Representative results:

| Workload | Default ops/s | Static ops/s | Predictive ops/s | LLM_policy ops/s |
|---|---:|---:|---:|---:|
| `mdtest_tree` | 166.8 | 115.7 | 227.4 | 205.6 |
| `sprite_lfs_smallfile` | 126.6 | 63.4 | 188.6 | 89.0 |
| `hotdirs_zipf` | 336.6 | 41.5 | 29.0 | 35.1 |
| `filebench_varmail_like` | 56.0 | 17.2 | 74.8 | 34.6 |

Interpretation:

- Strongest reliable signal: naive pinning is harmful.
- Reactive migration during a measured workload can dominate any placement
  benefit.
- Need retained-data runs and repeated randomized trials before claiming wins.

## 10. Are The Workloads Stressful Enough?

We checked this explicitly.

Changes made:

- Added `--keep-data`.
- Fixed node0 Ceph CLI access so `ceph df` snapshots work.
- Reran 5,000-file workloads with 8 workers.

Stress probe results:

- `sprite_lfs_smallfile`:
  - about 214.9 ops/s,
  - metadata delta about 11.5 MB used,
  - data delta 5,000 objects.
- `hotdirs_zipf`:
  - about 312.3 ops/s,
  - metadata delta about 7.5 MB used,
  - data delta 5,000 objects.

Conclusion:

- The retained-data workloads are large enough for a first pressure pass.
- MDS cache grew into tens of thousands of dentries/inodes during the larger
  retained runs.
- For final results, we should still add repeated trials and randomized order.

## 11. Improvement Matrix

Focused matrix:

- Workloads:
  - `sprite_lfs_smallfile`, 5,000 files, 8 workers, retained data.
  - `hotdirs_zipf`, 5,000 files, 8 workers, retained data.
- Variants:
  - default,
  - `static_top`,
  - `prepin_hotset`,
  - `predictive_safe`,
  - `append_segments`.

Summary:

| Workload | Variant | Ops/s | Speedup | p95 ms | Pins | Data objects |
|---|---:|---:|---:|---:|---:|---:|
| smallfile | default | 259.7 | 1.00x | 47.6 | 0 | 5000 |
| smallfile | `static_top` | 217.3 | 0.84x | 74.0 | 32 | 5000 |
| smallfile | `prepin_hotset` | 228.8 | 0.88x | 76.4 | 1 | 5000 |
| smallfile | `predictive_safe` | 293.2 | 1.13x | 46.4 | 0 | 5000 |
| smallfile | `append_segments` | 175.3 | 0.68x | 62.2 | 0 | 3 |
| hotdirs | default | 288.7 | 1.00x | 46.6 | 0 | 5000 |
| hotdirs | `static_top` | 90.9 | 0.32x | 178.4 | 64 | 5000 |
| hotdirs | `prepin_hotset` | 104.8 | 0.36x | 188.1 | 1 | 5000 |
| hotdirs | `predictive_safe` | 139.0 | 0.48x | 170.4 | 1 | 5000 |
| hotdirs | `append_segments` | 189.9 | 0.66x | 60.3 | 0 | 2 |

## 12. What Worked Well

Infrastructure:

- The 4-node CephFS setup is working and repeatable enough for experiments.
- The plugin architecture makes policy iteration fast.
- The runner captures throughput, latency, policy events, storage metrics, and
  Ceph pool snapshots.
- `--keep-data` makes metadata pressure visible.

Benchmarking:

- Retained 5,000-file workloads generate measurable metadata and data-pool
  pressure.
- `hotdirs_zipf` is useful because it creates a clear hot-subtree scenario.
- The phase breakdown helps identify where cost appears: most pinning overhead
  shows up in create throughput, not stat/read.

Implementation:

- The packing layer works functionally.
- It reduces physical data objects dramatically:
  - from about 5,000 objects to 2-3 objects in the latest runs.

## 13. What Did Not Work Well

Static pinning:

- Pinning every directory is too aggressive.
- Even `static_top` hurts.
- Pin events appear to add overhead and/or force metadata ownership patterns that
  are worse than default for these workloads.

Reactive predictive pinning:

- When predictive policies pin during measured work, create latency gets worse.
- This suggests subtree migration costs are too high for mid-workload reaction.
- `predictive_safe` avoided pins on the broad small-file workload and looked
  faster, but that is not a policy win because no placement action occurred.

Packing layer:

- Packing reduced object count, but current create throughput is slower.
- Cause is likely serialized segment allocation and JSON index appends.
- Current implementation optimizes metadata footprint, not ingest throughput.

Measurement:

- Run-to-run variance is significant.
- Some metadata byte deltas are noisy because Ceph background compaction/reclaim
  can happen during a run.

## 14. Current Interpretation

Placement-policy conclusion so far:

- Simple explicit pinning is not enough.
- The default CephFS balancer is hard to beat with naive user-level pins.
- Runtime migration during hot writes is especially expensive.

Small-file packing conclusion so far:

- Packing is the more promising direction.
- It clearly reduces physical object count and should reduce metadata pressure at
  larger scale.
- It needs a better write path before it can improve throughput.

Where metadata-pool writes look most improvable:

- Broad small-file workloads with thousands of tiny logical files.
- Hot-directory workloads where many file creates land in one subtree.
- Scenarios where logical file count is high but payload bytes are small.

## 15. Next Steps

Benchmark methodology:

- Add randomized run order.
- Run at least 3 repeats per variant/workload.
- Add warmup phases before measured phases.
- Preserve raw Ceph counters and MDS perf dumps alongside JSON/CSV results.

Policy improvements:

- Avoid mid-workload migration.
- Prefer pre-placement only when the hot layout is known before writes begin.
- Test coarser placement only: root/top-level subtrees, not every directory.
- Consider a "do nothing unless extremely confident" predictive policy.

Packing-layer improvements:

- Shard segments by worker or directory to reduce lock contention.
- Shard index logs instead of one global JSON-lines log.
- Batch index writes.
- Add recovery/compaction tests.
- Compare native files versus packed logical files at larger file counts.

Expected near-term goal:

- Build a faster packed-storage prototype.
- Show that object-count reduction can translate into throughput or tail-latency
  improvement once the index path is less serialized.

## 16. Final Presentation Storyline

Suggested narrative:

1. CephFS metadata can bottleneck small-file workloads.
2. We built a realistic 4-node testbed with multiple active MDS ranks.
3. We implemented a benchmark runner and policy/storage plugin system.
4. We tested default behavior, static pinning, predictive pinning, and packing.
5. Naive pinning was consistently harmful.
6. Reactive migration is too expensive during active writes.
7. Packing is functionally promising because it collapses thousands of files into
   a few physical objects, but the current index/segment implementation needs
   throughput work.
8. Next step: improve packing write path and run repeated randomized experiments.

