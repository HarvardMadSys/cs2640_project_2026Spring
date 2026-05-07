# Small-Dirs vs Hot-Dirs Results

Last updated: 2026-05-04

This note summarizes the difference between the two main retained-data workloads
and links the generated performance graphs.

## Workload Difference

`sprite_lfs_smallfile` is the broad small-file case.

- Files are spread across many directories.
- It stresses namespace breadth: many parent directories, many independent file
  creates, stats, and reads.
- It is a good proxy for workloads where metadata pressure comes from many tiny
  files across a wide directory tree.
- In this workload, static pinning has no obvious hot subtree to exploit.

`hotdirs_zipf` is the skewed hot-directory case.

- Most files are created under one hot directory, with the rest spread across
  colder directories.
- It stresses hotspot behavior: one subtree receives a disproportionate amount
  of metadata traffic.
- It is a good proxy for workloads like one active job/output directory or one
  busy mailbox/log directory.
- In theory, this is where placement policies should help most. In practice,
  our current pinning policies often hurt because pinning/migration cost lands
  during the write-heavy measured phase.

## Current Interpretation

Default CephFS remains the strongest throughput baseline overall.

- `static_top` is our current conservative static baseline. It is less aggressive
  than pinning every directory, but still slower than default on both workloads.
- `prepin_hotset` is simple and plausible when the hot directory is known, but
  it still underperforms default in the current measurements.
- `predictive_safe` looks faster on the broad small-file run, but it recorded
  zero pin events there, so that result should be treated as run variance rather
  than a real placement-policy improvement.
- On `hotdirs_zipf`, `predictive_safe` did pin once and still ran slower than
  default, which suggests runtime subtree movement remains too expensive.
- `append_segments` reduces physical data objects sharply, but create throughput
  is worse than native because the current segment/index write path is costly.

## Generated Graphs

Policy performance against default and static-style baselines:

- [Throughput: policies vs default/static](figures/policy_throughput_smallfiles_hotdirs.svg)
- [p95 latency: policies vs default/static](figures/policy_p95_smallfiles_hotdirs.svg)
- [Speedup relative to default](figures/policy_speedup_smallfiles_hotdirs.svg)

Packing/storage tradeoffs:

- [Storage throughput: native vs packed/sharded](figures/storage_throughput_sharded.svg)
- [Data object reduction: native vs packed/sharded](figures/storage_data_objects_sharded.svg)

Oracle-guided hybrid cold packing:

- [Overall throughput: native vs hybrid cold-pack](figures/oracle_hybrid_overall_ops.svg)
- [Per-phase throughput: native vs hybrid cold-pack](figures/oracle_hybrid_phase_ops.svg)
- [Per-phase p95 latency: native vs hybrid cold-pack](figures/oracle_hybrid_phase_p95.svg)
- [Structural metrics: namespace compression and batching](figures/oracle_hybrid_structural_metrics.svg)

## New Oracle Result

The older small-dirs and hot-dirs graphs still matter because they show why
simple placement policies were not enough. The new oracle result adds a more
focused message:

- If the benchmark knows which files are hot ahead of time, then leaving those
  files native and packing only the cold bulk set can beat native CephFS.
- The current winning result is modest: about `200.3` measured ops/s for the
  hybrid layout versus `196.1` for native.
- The gain comes mainly from faster cold bulk create and cleanup behavior, not
  from better hot-path latency.
- Adding prepinning still loses, which is further evidence that pinning is not
  the main improvement path for this project.

## Key Takeaways For Slides

- Small-dirs and hot-dirs stress different metadata problems:
  - small-dirs: breadth and many independent namespace entries,
  - hot-dirs: concentrated metadata pressure in one subtree.
- Current placement policies are not beating default CephFS.
- Static and pre-pin approaches hurt especially on hotdirs.
- Packing is the most promising architectural direction because it collapses
  thousands of physical objects into a few, and after batching the journal plus
  virtualizing cold directories it now has a small end-to-end win on the oracle
  workload.
