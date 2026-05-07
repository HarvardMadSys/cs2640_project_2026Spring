# Native CephFS vs Oracle Cold Packing

## Why Native CephFS Stays Competitive

The native baseline is strong because it is not a naive implementation. It is a
mature distributed filesystem path with a kernel/client stack, MDS journaling,
metadata caching, writeback behavior, object placement, and years of tuning
behind normal file operations. Even when we give our packed implementation an
oracle hot/cold split, native CephFS still has several advantages:

- Native file operations are handled by optimized CephFS client and MDS code,
  not by Python benchmark-layer bookkeeping.
- Independent files can be created, written, cached, and flushed through code
  paths CephFS already knows how to parallelize.
- The client and MDS already batch, cache, journal, and recover metadata changes
  below our benchmark interface.
- Hot files remain fast because native CephFS can serve them directly without a
  logical-to-physical lookup layer.
- The native path has a complete framework for consistency, replay, recovery,
  cache invalidation, directory authority, object layout, and failure handling.

This is why native can remain close to oracle cold packing. The oracle tells our
storage layer which files are cold, but it does not automatically give us the
rest of CephFS's machinery. We still pay for every extra userspace decision,
index update, lock, and physical write that our packing layer introduces.

## What The Oracle Workload Does

`oracle_hotcold_mix` constructs a workload where the benchmark already knows the
future hot and cold sets:

- Directories named `hot*` hold the smaller hot working set.
- Directories named `cold*` hold the larger cold bulk set.
- The benchmark creates both sets, then repeatedly runs stat/read/create/delete
  operations against the hot side.
- With `oracle_cold_segments`, files under `hot*` stay as native CephFS files,
  while files under `cold*` are packed into append-only segment files.

We call it oracle-guided because this hot/cold label is injected by the
benchmark. A real filesystem would not know this perfectly in advance. The
result answers a narrower systems question: if hot/cold membership were known,
can a hybrid layout beat native CephFS by keeping hot files native and packing
cold bulk files?

## Current System Design

Before the latest changes, our plugin system sat above CephFS:

```text
benchmark workload
  -> benchmark_runner.py
  -> storage plugin interface
  -> oracle_cold_segments / append_segments / native storage
  -> POSIX or libcephfs calls
  -> CephFS client
  -> MDS + OSDs
```

That means our storage plugins were mostly userspace layout adapters. Native
storage mapped one logical file to one CephFS file. Packed storage mapped many
logical files to segment files plus an index journal, but CephFS only saw normal
file and byte-range operations.

In this design, the plugins did not own any CephFS internals:

- They did not change MDS code.
- They did not change CephFS journaling.
- They did not change client writeback behavior.
- They did not participate in native recovery.
- They only influenced CephFS through ordinary operations such as `mkdir`,
  `write`, `read`, `unlink`, and optional xattrs such as `ceph.dir.pin`.

So the plugin sat outside the real CephFS machinery. It could reduce namespace
pressure by creating fewer physical files/directories, but it could not inherit
all of native CephFS's internal batching, object layout, cache management, or
recovery framework.

## More Embedded Design

The next design moves the packed path one step deeper without requiring a Ceph
source fork:

```text
benchmark workload
  -> benchmark_runner.py
  -> storage plugin interface
  -> oracle_cold_segments
  -> CephFS-aware physical layout hooks
     -> logical policy view translated to real CephFS paths
     -> cold segment data batching
     -> CephFS file-layout xattrs for packed segment files
     -> optional direct RADOS objects for cold packed payloads
  -> POSIX or libcephfs calls
  -> CephFS client
  -> MDS + OSDs
```

This is still not a native CephFS implementation. The MDS does not understand
individual packed logical files. However, the packed layer now uses more of the
framework that CephFS already exposes:

- Policy hooks can operate over logical hot/cold directories while the storage
  layer translates virtual cold directories to real CephFS paths.
- Cold segment writes are batched before they reach CephFS, reducing the number
  of tiny physical writes.
- Packed segment files can request CephFS layout settings through native xattrs
  such as `ceph.file.layout.stripe_unit`, `ceph.file.layout.stripe_count`, and
  `ceph.file.layout.object_size`.
- Cold packed payloads can optionally bypass CephFS files and write directly to
  RADOS-style objects. The CephFS namespace still stores the packed index, while
  cold data moves into the lower object layer.

The source-fork version would sit even deeper:

```text
CephFS client/MDS source
  -> native logical small-file packing
  -> MDS-owned packed-file metadata and journal replay
  -> native cache invalidation and recovery
  -> RADOS objects
```

That would be the cleanest way to access all of CephFS's machinery, but it is a
much larger implementation. The current more-embedded design is the pragmatic
middle ground: use CephFS's exposed layout and placement controls from our
storage layer, then measure whether those hooks plus batching are enough to
make oracle packing beat native more consistently.

## What Native Has That Our Packing Layer Lacks

Native CephFS has a complete framework around the data structure. Our packing
layer started as a benchmark plugin, so it originally only had the core layout
idea: put many small logical files into larger append-only segment files and
record a separate index.

Native has these advantages that our implementation has only partially built:

- **Client-side batching and writeback:** native can combine and schedule work
  below the POSIX/libcephfs call boundary. Our earlier packed path batched the
  index but still issued one small segment write per cold file.
- **Metadata authority and cache management:** native has MDS ownership,
  subtree authority, directory cache behavior, and journal replay. Our packed
  path has an in-memory index plus an append-only journal.
- **Recovery model:** native can recover after crashes using CephFS journals and
  object state. Our packed path has a journal format but does not yet have full
  replay, compaction, and crash-consistency tests.
- **Concurrency framework:** native has internal concurrency control. Our packed
  path has Python locks around segment allocation and index state.
- **Policy integration:** native policies naturally operate on real directories.
  Oracle packing can virtualize cold directories, so policy hooks need a
  translation layer to reason about logical paths while pinning real paths.

The main lesson is that packing reduces namespace pressure, but native's
framework reduces implementation overhead. To confidently beat native, our
packed path has to keep the namespace reduction while removing enough of its own
userspace overhead.

## Bridge We Built

The project now has two bridge pieces that make the oracle packed path closer to
native's practical advantages.

First, the storage layer has a policy backend hook. Native storage passes the
raw filesystem backend through unchanged. Oracle cold packing exposes the
logical hot/cold directory view to policies and translates virtual cold-directory
pin requests onto a real packed namespace path. This lets existing policy
plugins see the same logical workload shape that native sees.

Second, `oracle_cold_segments` now batches cold data writes, not just index
writes. Before this change, every packed cold file still caused a separate
physical `write_at()` into the segment file. That preserved the object-count
benefit, but it left a large number of tiny write calls on the critical path.
The new `cold_data_batch_bytes` option buffers contiguous cold writes per
segment and flushes them on threshold, read, or phase sync.

Important knobs and metrics:

- `cold_data_batch_bytes`: target buffered cold data bytes before a segment data
  flush. The default is `1048576`.
- `ceph_layout_scope`: where to apply CephFS file-layout xattrs. Supported
  values are `off`, `store`, `segments`, and `all`; the default is `segments`.
- `ceph_stripe_unit`, `ceph_stripe_count`, `ceph_object_size`: optional CephFS
  layout controls for packed physical files and/or directories.
- `cold_data_backend`: `cephfs`, `rados`, or `auto`. `rados` writes cold packed
  payloads directly to lower-layer objects instead of CephFS segment files.
- `rados_pool` and `rados_namespace`: target pool and object-name prefix for
  direct cold data objects.
- `data_flushes`: number of physical cold segment data flushes.
- `buffered_data_bytes`: bytes still buffered at the time metrics are collected.
- `layout_xattrs_attempted` and `layout_xattrs_applied`: whether the run
  actually exercised the CephFS layout hook.
- `rados_objects_created`: number of lower-layer cold payload objects created.
- `logical_creates_per_data_flush`: derived metric showing how many logical
  creates were combined into each packed data write.
- `packed_cold_bytes_per_data_flush`: derived metric showing average physical
  cold data write size.

This is the same direction as native's advantage: reduce the number of small
operations visible to CephFS and push larger, more sequential writes into the
physical storage layer.

## Remaining Work

The packed path is still not equivalent to native's framework. The next pieces
needed for a more confident win are:

1. Add recovery tests that rebuild the packed index from `index.bin` and segment
   files.
2. Add compaction for tombstoned packed records so cleanup-heavy workloads do not
   leave unbounded dead data.
3. Run repeated randomized CloudLab trials with `cold_data_batch_bytes` enabled
   and compare against the same native baseline.
4. Sweep `cold_data_batch_bytes`, `index_batch_bytes`, hot/cold split, and hot-op
   count to find where packing consistently wins.
5. Replace the oracle label with a conservative predictor and measure how much
   of the oracle gain remains.
