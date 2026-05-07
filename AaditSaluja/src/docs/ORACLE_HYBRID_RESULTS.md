# Oracle Hybrid Results

Last updated: 2026-05-04

This note summarizes the new result set that finally beats the native CephFS
baseline on one metadata-heavy CloudLab workload.

## Result Summary

Workload:

- `oracle_hotcold_mix`
- 5,000 files
- 4 KiB per file
- 64 directories
- 20,000 hot metadata operations
- 8 workers
- backend: POSIX over the live CloudLab CephFS mount at
  `/mnt/cephfs/cs2640-bench`

Compared variants:

- Native baseline: default CephFS, native files.
- New hybrid: `oracle_cold_segments`, no pinning.
- Hybrid plus prepin: `oracle_cold_segments` with `prepin_hotset`.

Headline numbers:

| Variant | Measured ops/s | Elapsed seconds | Relative to native |
|---|---:|---:|---:|
| Native | 196.1 | 205.3 | 1.00x |
| Hybrid cold-pack | 200.3 | 200.8 | 1.02x |
| Hybrid + prepin | 172.9 | 244.3 | 0.88x |

Result artifacts on node0:

- `~/FinalProj/report/results/cloudlab-oracle-native.json`
- `~/FinalProj/report/results/cloudlab-oracle-hybrid-none-v2.json`
- `~/FinalProj/report/results/cloudlab-oracle-hybrid-prepin.json`

Generated figures:

- [Overall throughput](figures/oracle_hybrid_overall_ops.svg)
- [Per-phase throughput](figures/oracle_hybrid_phase_ops.svg)
- [Per-phase p95 latency](figures/oracle_hybrid_phase_p95.svg)
- [Structural metrics](figures/oracle_hybrid_structural_metrics.svg)

Key structural metrics for this comparison:

| Metric | Native | Hybrid cold-pack | Hybrid + prepin | Why it matters |
|---|---:|---:|---:|---|
| Estimated physical files created | 7500 | 3127 | 3127 | Lower means less native CephFS file/object churn |
| Estimated materialized directories | 65 | 7 | 7 | Lower means less namespace work on the cold side |
| Logical creates per physical file | 1.00 | 2.40 | 2.40 | Higher means better physical fan-in |
| Index flushes | 0 | 18 | 138 | Lower suggests less metadata-journal fragmentation |
| Packed create fraction | 0.0% | 58.3% | 58.3% | Higher means more cold work diverted away from native files |

## 1. The New Policy

Strictly speaking, the winning change is not a metadata placement policy. It is
an oracle-guided storage layout policy implemented by
`src/storage_plugins/oracle_cold_segments.py`.

Behavior:

- Leave `hot*` files as normal native CephFS files.
- Pack only `cold*` files into append-only segments.
- Record packed-file metadata in one compact batched binary journal rather than
  one JSON record append per logical file.
- Avoid creating physical cold directories when they are only needed as logical
  namespaces.

Important implementation settings in the winning run:

- `hot_prefixes=hot`
- `virtual_cold_dirs=true`
- `index_batch_bytes=1048576`
- `index_batch_records=512`

This is an oracle setup because the benchmark itself labels the hot set in
advance. That is deliberate for now: it isolates whether the storage idea can
help if hot/cold knowledge were available.

## 2. Changes To The Testbench

The benchmark changes were just as important as the storage change.

New workload:

- `oracle_hotcold_mix` in `src/cephfs_metadata/benchmark_runner.py`
- It builds a known-hot working set and a larger cold bulk set.
- It then runs repeated hot-path stat/read traffic plus hot-directory
  create/delete churn.

Heavier suite:

- Added `metadata_heavy`.
- It increases file counts and uses 4 KiB files instead of the lighter older
  defaults.

Measurement fix:

- Benchmark JSON now records `measured_seconds`, `measured_operations`, and
  `measured_ops_per_sec`.
- Benchmark JSON now also records `derived_metrics`, including estimates for
  physical files created, materialized directories, packed-create fraction, and
  index overhead.
- Each measured phase now calls `storage.sync()` before its timer stops, so
  buffered journal/index work is counted inside the phase that caused it.

That matters because older wall-clock-only numbers could hide deferred storage
work.

## 3. Why It Works Well

The win is small, but the mechanism is coherent.

What improved:

- `bulk_create` jumped from `837.1 ops/s` to `1484.5 ops/s`.
- `cleanup_delete` improved from `170.0 ops/s` to `285.1 ops/s`.
- `oracle_hot_stat` stayed effectively flat.

Why:

- Cold files stop creating one native CephFS object each.
- The packed side writes into one segment file instead of many separate file
  objects.
- The binary journal plus batching cuts index-write overhead compared with the
  original JSON-lines path.
- Virtual cold directories reduce namespace churn on the cold side.
- Hot files are still native, so the benchmark keeps direct hot-path access
  where the MDS and client cache are already effective.

What still got worse:

- Hot reads slowed versus native.
- Hot churn create slowed.
- Hot churn delete improved slightly in throughput, but with worse p95 latency.

Interpretation:

- The storage layout is helping the broad cold bulk path more than it helps the
  hot interactive path.
- The overall win happens because the bulk/cold savings are larger than the hot
  path regression on this workload shape.

## 4. Are The Tests Fair?

Fair enough to claim a directional result, but not enough yet for a final
paper-level claim.

What is fair:

- All compared variants used the same cluster, mount, workload shape, file
  count, file size, directory count, worker count, and seed.
- The new measured ops/s accounting treats buffered storage work consistently.
- The hybrid and native runs include cleanup phases, so the comparison is not
  cherry-picking create-only behavior.

What is still not ideal:

- The winning comparison is still a single run per variant.
- Run order was not randomized.
- The native artifact predates the new top-level measured fields, although its
  measured ops/s can be recomputed exactly from per-phase rows.
- The benchmark uses an oracle hot/cold label, which is not available in a real
  general deployment unless another system predicts it.

Bottom line:

- The test is fair as an engineering experiment for "can this storage idea beat
  native under a metadata-heavy shape if hot/cold membership is known?"
- It is not yet fair as a claim that the approach is broadly superior in
  production.

## 5. Assumptions Made

Assumptions in the workload:

- The hot set is known ahead of time.
- Hot paths live under recognizable directory prefixes such as `hot*`.
- Cold files are mostly write-once and do not need frequent native updates.

Assumptions in the storage design:

- Packing cold files reduces CephFS metadata/data-object pressure enough to
  offset the extra indirection on reads.
- One shared segment plus one batched journal is better than many physical
  shards for this metadata problem.
- Avoiding physical cold directories reduces useful work rather than removing a
  beneficial cache/locality effect.

Assumptions in the interpretation:

- The throughput gain comes from the storage path improvements, not from
  background cluster drift.
- The current workload is more metadata-bound than the older benchmarks, so it
  is a better place to test small-file layout changes.

## Recommended Slide Message

Use this as the short story:

- Simple subtree pinning did not beat native CephFS.
- We changed both the workload and the storage path to focus on metadata-heavy
  cold bulk files.
- A hybrid layout that keeps hot files native and packs only cold files finally
  beats the baseline on the new oracle workload.
- The gain is small and oracle-dependent, but it is a real systems result and a
  much stronger direction than pinning.
