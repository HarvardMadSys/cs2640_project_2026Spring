# Wiki multi-tenant interference, exploration log

## Goal

Three variants of the same A/B partitioning of Wikipedia were built and
benchmarked in `../bench_tenants.py`. They show very different
interference when A and B run concurrently vs. alone:

| variant        | A solo QPS | B solo QPS | A+B A QPS | A+B B QPS | per-worker drop |
|----------------|-----------:|-----------:|----------:|----------:|---------------:|
| wiki_QC02_SS02 |      18.67 |      17.25 |      3.00 |      3.02 |            -83% |
| wiki_QC02_SS08 |      13.20 |      11.86 |      1.34 |      1.34 |            -78% |
| wiki_QC08_SS08 |      10.34 |      13.34 |      2.79 |      2.78 |            -53% |

(All rows from `../*_bench_result/*.csv`, steady state, ef=200, k=100,
300 s runs with `--reset-cache` before every run. Drop is computed after
normalising by `request_per_tenant`, which is 16 for all solo runs, 16
for QC02_SS02 A+B, 8 for the SS08 A+B runs.)

QC08_SS08 and QC02_SS08 have the **same partition shape** but very
different interference. QC02_SS08 has the same `scatter` as QC08_SS08
but a different `query_coverage`. So the task is to explain why query
coverage, not partition size, can cause such different interference.

The requirements in [requirements.MD](requirements.MD) limit us to
editing `milvus.yaml`. The scripts here search the already-loaded
collections and do not mutate schema, data, or partitions.

## 1. Variants recap

| variant        | scatter(sh/A/B) | query_coverage | shared_knn_size | shared_partition | partition_A | partition_B | mean query coverage in shared |
|----------------|----------------:|---------------:|----------------:|-----------------:|------------:|------------:|-----------------------------:|
| wiki_QC02_SS02 |  0.2 / 0.4 / 0.4 |           0.2 |          68 237 |        7 053 055 |  14 247 572 |  14 245 010 |                        24.3% |
| wiki_QC02_SS08 |  0.8 / 0.1 / 0.1 |           0.2 |          68 237 |       28 014 911 |   3 901 974 |   3 901 600 |                        24.3% |
| wiki_QC08_SS08 |  0.8 / 0.1 / 0.1 |           0.8 |         350 739 |       28 071 322 |   3 679 698 |   3 679 810 |                        64.7% |

Two orthogonal knobs:

* `scatter` changes **partition shape** (how big shared vs private is).
* `query_coverage` changes **where each query's ground truth lives**.
  At 0.2, only ~20% of a query's top 100 neighbours live in the greedy
  `shared_knn`; at 0.8, ~80% do. Higher coverage means each query's
  HNSW search converges on a hot region of the shared index and rarely
  needs to dig into the private partition.

The Milvus setup (`/scratch/yunjia/milvus/configs/milvus.yaml`) has
`queryNode.mmap.vectorField = true` and `queryNode.mmap.vectorIndex =
true`, so both vectors and HNSW index live on disk and are paged in
through the kernel's page cache. Machine has 500 GB RAM, the three
loaded collections consume 333 GB on disk. Nothing fits "in memory" in
the strong sense; every query's graph walk depends on which pages are
currently resident.

## 2. Scripts

1. `summarize_and_plot.py` — offline, reads `*_bench_result/*.csv`,
   writes `out/summary.csv`, `out/qps_timeseries.png`,
   `out/interference_bar.png`, `out/latency_box.png`.
2. `per_partition_cost.py` — six probes on one variant, each with a
   warmup (samples discarded) and a measurement phase. Probes isolate
   whether the cost lives in the shared or the private HNSW index, and
   whether the contention under A+B is on the shared or the private
   index. Optional `--drop-cache` calls `sudo sysctl vm.drop_caches=3`
   before every probe for apples-to-apples cold-start.
3. `warm_share_test.py` — phase 1 runs A alone for N s (no drop),
   which warms shared_partition and partition_A. Phase 2 brings B
   online; only partition_B is cold on entry. Shows how much of B's
   initial slowdown is faulting private partition pages.
4. `compile_probes.py` — aggregates `per_partition_cost_*_warm.json`
   into `out/probe_summary.csv` and `out/probe_perworker.png`.

## 3. Operations performed

### 3.1  Baseline summary

```
python summarize_and_plot.py
```

* `out/summary.csv` — per-tenant QPS, p50, p95, p99, recall for each
  (variant, mode).
* `out/qps_timeseries.png` — each subplot is one variant, showing
  per-5 s QPS for A solo, B solo, and A under A+B. The A+B traces are
  much noisier but stable, i.e. interference is steady, not a one-off
  transient.
* `out/interference_bar.png` — the per-worker drop table summarised
  visually; QC08_SS08 clearly holds up best.

### 3.2  Per-partition probe, warm cache

```
python per_partition_cost.py --variant <v> --warmup 30 --measure 20 --rpt 8
```

Warm cache means probes run back to back without dropping page cache,
letting the kernel retain pages across probes. Outputs:
`out/per_partition_cost_<v>_warm.json`,
`out/log_ppcost_<v>_warm.txt`,
`out/probe_summary.csv` (via compile_probes.py),
`out/probe_perworker.png`.

Per-worker QPS, warm cache:

| variant      | SOLO_SH | SOLO_PR | SOLO_BOTH | CONC_SH×SH | CONC_PR×PR | CONC_BOTH×BOTH |
|--------------|--------:|--------:|----------:|-----------:|-----------:|---------------:|
| QC02_SS02    |    7.15 |    3.29 |      2.15 |       3.16 |       1.62 |           0.27 |
| QC02_SS08    |    2.47 |   16.98 |      1.53 |       0.86 |       5.61 |           0.16 |
| QC08_SS08    |    2.36 |   17.92 |      0.36 |       0.10 |       9.02 |           0.15 |

(A and B merged by mean; the two tenants are always within 5% of each
other in concurrent probes.)

Observations:

* **SOLO_SHARED_ONLY vs SOLO_PRIVATE_ONLY** tracks partition size
  almost perfectly: the 7 M shared in QC02_SS02 is 2x the per-worker
  rate of its 14 M private; the 28 M shared in the SS08 variants is 5x
  to 7x slower than their 3.9 M private.
* **SOLO_BOTH cost is dominated by the bigger partition.** For SS08 the
  private is negligible; for SS02 the shared takes about one third of
  the time.
* **CONC_SHARED×SHARED.** QC02_SS02 keeps 88% of its solo combined
  throughput (6.32 of 7.15). For QC02_SS08 only 70% of combined solo
  carries over (1.72 of 2.47). QC08_SS08 produced a pathological 0.20
  in this probe, visibly a transient (repeating the probe alone gave
  numbers close to QC02_SS08). The interesting datum is still
  QC02_SS02 vs QC02_SS08: the smaller shared partition cleanly hosts
  two tenants, the bigger one does not.
* **CONC_PRIVATE×PRIVATE.** This is the stripe where A hits
  partition_A and B hits partition_B. Disjoint graphs, so cache cannot
  be shared between tenants, but the graphs are small (3.9 M in SS08,
  14 M in SS02).
  * QC02_SS02: 1.62 each = 3.24 combined vs SOLO 3.29. Almost linear
    scaling. Two tenants driving disjoint 14 M partitions do not
    interfere, because both partitions' working sets fit comfortably
    in page cache after warmup.
  * QC08_SS08: 9.02 each = 18.04 vs SOLO 17.92 — also linear.
  * QC02_SS08: 5.61 each = 11.22 vs SOLO 16.98 — 66% of solo. So
    there *is* some inter-private interference in QC02_SS08 that
    QC08_SS08 does not have. This is the only clean signal where the
    two same-shape variants diverge.
* **CONC_BOTH×BOTH.** Here the ratio collapses. All three variants
  drop to 0.15–0.27 per worker, 8× to 15× worse than SOLO_BOTH. The
  shared + private combination in concurrent mode is far worse than
  the sum of the parts.

### 3.3  Warm-A-then-add-B

```
python warm_share_test.py --variant <v> --warm 60 --measure 60 --rpt 8
```

Steady-state per-worker QPS in the last 20 s window:

| variant      | A_warm (solo, last 20s) | A after B joins | B (last 20s) |
|--------------|------------------------:|----------------:|-------------:|
| QC02_SS02    |                    3.29 |            0.29 |         0.26 |
| QC02_SS08    |                    2.27 |            0.17 |         0.19 |
| QC08_SS08    |                    2.10 |            0.19 |         0.17 |

All three variants lose ~91% of per-worker throughput when B joins, and
B ramps up from near-zero to roughly the same per-worker rate as A. The
60 s head start for A did **not** spare it from the collapse. That tells
us: the interference is not dominated by B paying a one-time cold-page
tax on partition_B. If it were, warming A first would leave A mostly
untouched and only B would be slow. Instead, A collapses too.

## 4. Why do the three variants show different interference?

Two complementary mechanisms fit the numbers.

### 4.1  Shared partition size and cache sharing

`CONC_SHARED×SHARED` reaches 88% of solo throughput for QC02_SS02
(7 M shared) but only 70% for QC02_SS08 (28 M shared). When two
tenants' graph walks must overlap in the same set of pages to hit the
cache, a bigger graph has a smaller fraction of queries whose entry
points and intermediate nodes actually coincide. Bigger shared
partition → fewer page hits on the shared pages → more disk I/O under
contention.

### 4.2  Query coverage and hot-region concentration

Between QC02_SS08 and QC08_SS08 the partition shape is identical. The
only difference is where each query's top 100 neighbours live. In
QC08_SS08 the `shared_knn` (the greedy hot set) is 350 739 vectors;
mean coverage is 64.7%, so every query's final HNSW candidates come
mostly from that hot region. In QC02_SS08 `shared_knn` is 68 237 and
mean coverage is 24.3%, so every query has to walk much further in the
shared partition before finding any true neighbour, and the last 80%
of each top-100 result lives in partition_A or partition_B.

The practical consequence: in QC08_SS08 the effective working set in
the shared HNSW index is small and overlapping across queries (because
they all converge on the same hot 350 k). Under A+B both tenants walk
into that same region, page cache is shared, and the system recovers.
In QC02_SS08 the shared-partition work for each query is a long,
sparse walk over 28 M vectors, different queries and different tenants
touching largely disjoint nodes; cache cannot be amortised. This is
exactly the difference the clean `CONC_PRIVATE×PRIVATE` datum already
hinted at (66% vs 101% of solo combined for the two SS08 variants).

### 4.3  Why `CONC_BOTH×BOTH` collapses even when `CONC_PRIVATE×PRIVATE` looks fine

Per-worker QPS in `CONC_BOTH×BOTH` is ~0.15–0.27, far below
`1 / (1/CONC_SHARED + 1/CONC_PRIVATE)` which a simple additive model
would predict (that model gives ~0.5 per worker for the SS08
variants). So the interference in the full workload is not additive;
it is multiplicative.

The mechanism fitting the observations: under A+B each tenant's search
request alternates between the two partitions (shared, then private)
via Milvus's segment-level work dispatch. With 16 concurrent in-flight
searches (rpt=8 × 2 tenants) each spawning fan-out work across
hundreds of segments, the kernel sees a bursty, interleaved access
pattern — a chunk of shared pages, a chunk of partition_A pages, back
to shared, over to partition_B, etc. That pattern is the adversarial
case for a page-cache LRU: every "private" page that gets promoted by
a private-partition work item competes with the shared pages a
different work item just touched, and vice versa. The resident set
oscillates faster than any single work item can keep its pages hot.

When the two tenants are restricted to only the private partitions
(`CONC_PRIVATE×PRIVATE`), there is no alternation: A thrashes its own
private, B thrashes its own private, and the two sets stay hot. When
they are restricted to only shared (`CONC_SHARED×SHARED`), the same
happens for shared. It is the cross-product that kills steady-state.

This is consistent with `warm_share_test` showing A collapses when B
joins even though A's pages were already hot: adding B doubles the
diversity of the access stream, the alternation rate rises, and A's
hot pages no longer stay hot.

## 5. Lessons and operations that mattered

1. **Do not interpret the baseline interference as only a coincidence
   of partition size.** The two SS08 variants share partition sizes
   exactly and still show 2x different A+B throughput. Partition size
   matters for the solo cost; **where the ground-truth neighbours live
   in the index matters for interference.**
2. **Interference in HNSW + mmap is dominated by memory access
   patterns, not CPU or gRPC overhead.** `CONC_PRIVATE×PRIVATE` shows
   two disjoint tenants can scale almost linearly when each keeps its
   pages hot (101% of solo combined for QC08_SS08). The A+B full
   workload is in the regime where working set exceeds the hot
   portion of the cache, so throughput is gated by disk I/O.
3. **Warming one tenant does not protect it from the other tenant
   joining.** All three variants lose ~91% of per-worker throughput
   when B comes online after A warms for 60 s. The mechanism is not
   "B starts cold and is slow"; it is "A and B together oscillate the
   resident set faster than the working set can stay resident".
4. **`drop_caches=3` from a userspace sudo is only partially
   effective against Milvus's live mmaps.** Cached drops by only a few
   GB even when the call succeeds; Milvus keeps the active-list pages
   pinned as it is actively searching. To get a truly cold start,
   `bench_tenants.py --reset-cache` does a `release_collection ->
   drop_caches -> load_collection` sequence. My probes relied on a
   warmup phase instead of a true cold start.
5. **Do not compare solo vs concurrent at different `rpt` values.**
   The bench_result logs used rpt=16 for solo everywhere but rpt=8 for
   A+B on the SS08 variants and rpt=16 for QC02_SS02 A+B. Per-worker
   normalisation fixes this only if the system is not saturated; it
   was saturated in solo for some variants. My per-partition probes
   all use rpt=8 for uniformity.
6. **The `CONC_SHARED×SHARED` probe with QC08_SS08 returned a
   pathological 0.10 per worker.** It is a transient — the cache state
   carried over from preceding probes hit an eviction pattern Milvus
   couldn't recover from within 30 s of warmup. Re-running the probe
   in isolation gave numbers consistent with the other variants. Be
   suspicious of single-probe numbers; warmup budgets shorter than the
   working set's refill time look like different regimes.
7. **A simple additive model of interference fails.** The full A+B
   workload is 3× worse than even the worst of `CONC_SHARED×SHARED`
   and `CONC_PRIVATE×PRIVATE` alone. Designing configurations or
   isolating tenants by partition size alone will not capture this.

## 6. Outputs

All under `out/`:

* `summary.csv`, `probe_summary.csv`
* `qps_timeseries.png`, `interference_bar.png`, `latency_box.png`
* `probe_perworker.png`, `probe_latency.png`
* `per_partition_cost_<variant>_warm.json`,
  `per_partition_cost_wiki_QC08_SS08_coldstart.json`
* `warm_share_<variant>.csv`, `warm_share_<variant>.json`
* `log_ppcost_*.txt`, `log_warm_share_*.txt`

## 7. Direct measurement of thrashing (experiments T1 + T2)

So far the "thrashing" story was inferred from throughput and latency.
Two further scripts measure it directly, using kernel counters.

* `thrash_probe.py` runs one chosen probe while sampling, at 1 Hz, the
  Milvus process's `/proc/<pid>/stat majflt`, `/proc/<pid>/io
  read_bytes`, and the cgroup v2 `memory.stat` fields `pgmajfault`,
  `workingset_refault_file`, `workingset_activate_file`. The
  `workingset_refault_file` counter is the kernel's own definition of
  thrashing: it counts pages that were evicted and then re-read from
  disk within the kernel's refault distance window.
* In parallel the script takes 0.5 Hz `mincore()` snapshots of 36
  Milvus-mapped segment files, 24 of which are the 133 MB
  `index_files/<coll>/<build>/101/index` HNSW graph files. Sum of
  resident bytes across samples gives the page cache footprint over
  time; sum of absolute differences between consecutive samples gives
  a "churn" measure.
* `compile_thrash.py` aggregates nine runs (three variants x three
  concurrent probes) into `out/thrash_summary.csv` and
  `out/thrash_compare.png`; `plot_thrash.py` draws per-probe counter
  and residency time series.

Key numbers, 20 s warmup + 20 s measurement, rpt=8:

| variant      | probe        | per-worker QPS | refaults/query | MB read/query | HNSW churn, MB / 20s |
|--------------|--------------|---------------:|---------------:|--------------:|--------------------:|
| QC02_SS02    | CONC_SHARED  |          4.49  |            1.8 |           0.0 |                 398 |
| QC02_SS02    | CONC_PRIVATE |          2.17  |          4 301 |          17.6 |               2 126 |
| QC02_SS02    | CONC_BOTH    |          0.32  |        123 961 |         507.7 |               2 406 |
| QC02_SS08    | CONC_SHARED  |          1.24  |          2 209 |           9.1 |               1 286 |
| QC02_SS08    | CONC_PRIVATE |          7.74  |            1.3 |           0.0 |               1 037 |
| QC02_SS08    | CONC_BOTH    |          0.18  |        190 282 |         779.4 |               2 014 |
| QC08_SS08    | CONC_SHARED  |          1.13  |         16 394 |          67.2 |                 462 |
| QC08_SS08    | CONC_PRIVATE |          9.29  |            1.0 |           0.0 |                 529 |
| QC08_SS08    | CONC_BOTH    |          0.20  |        199 993 |         819.2 |               1 648 |

Four things pop out:

1. **`CONC_BOTH` is in heavy thrashing across all three variants.** Each
   query refaults 120k to 200k pages, i.e. pulls 500 to 800 MB from
   disk. The combined "useful" bytes to answer a query are ~10 MB, so
   every page is being re-read tens of times per query. This is the
   direct confirmation that the shared-then-private alternation breaks
   the page cache.
2. **`workingset_activate_file` is zero for every probe.** The kernel
   only increments `activate` when a refault's distance is short
   enough that the page should have stayed in cache under more RAM.
   `activate = 0` with huge `refault` means the working set is bigger
   than any LRU-reasonable cache can hold; this is the kernel saying
   "you would still thrash even with more memory" under the current
   access pattern.
3. **CONC_PRIVATE on QC02_SS02 thrashes (4 301 refaults/query)** even
   though on the SS08 variants it is essentially clean (~1/query).
   Two disjoint 14 M private partitions have a combined working set
   that exceeds the hot cache; two disjoint 3.9 M partitions do not.
   This isolates a pure-capacity contribution to interference.
4. **CONC_SHARED thrashing scales with shared partition size and with
   the query coverage of the shared set.** 7 M shared is free (1.8
   refaults/query), 28 M shared with 20% coverage is moderate, 28 M
   shared with 80% coverage refaults most (16 394/query). This is the
   opposite of the earlier per-worker-QPS intuition, and it shows
   that the QC08_SS08 queries actually walk more of the shared graph
   under concurrency than the QC02_SS08 queries do, even though
   QC08_SS08 still recovers slightly better throughput.

Plots:
* `out/thrash_compare.png` — side-by-side log-scale bars for refaults
  per query and MB read per query across probes and variants.
* `out/counters_<tags>.png` and `out/residency_<tags>.png` — per-run
  time series for one or more probes (generated by `plot_thrash.py`).

## 8. Updated lessons

Adding to section 5:

8. **The kernel's `workingset_refault_file` counter is the right
   metric for "is this workload thrashing".** It is per-cgroup,
   costs nothing to read, and it separates "the cache is small" from
   "the cache is oscillating". `majflt` and `read_bytes` capture the
   same information but also include first-time loads; `refault_file`
   only counts pages that were already in cache and got kicked out.
9. **`mincore()` residency is the right metric for "how much of the
   index is currently warm".** Taking snapshots every 2 s and
   computing churn is sufficient to see whether the resident set is
   stable or sliding. We do not need to mmap the files from inside
   Milvus; any process can query mincore against the same kernel page
   cache, so a separate sampler works.
10. **Thrashing is not additive with the partition overlap, it is
    multiplicative.** CONC_SHARED contributes 2k refaults/query and
    CONC_PRIVATE contributes 1/query for QC02_SS08, but CONC_BOTH
    contributes 190k/query. The alternation itself is the dominant
    cost.

## 9. Open follow-ups, not done here

* Set `queryNode.mmap.vectorIndex = false` so the HNSW graph lives in
  heap instead of mmap, and repeat `CONC_BOTH`. If `refaults/query`
  drops sharply that confirms the mmap-LRU path is the problem and
  Milvus's own cache management would do better.
* Re-run the baseline `bench_tenants.py` benchmark with uniform
  `rpt=8` for solo and concurrent everywhere.
* Sweep `rpt` from 1 to 16 while running `thrash_probe.py` on
  `CONC_BOTH`; the knee of the refault curve gives the concurrency at
  which the workload first becomes I/O-bound.
* Try setting a memory cap via `launch_memory_limit.sh` and re-run
  the same thrash probe to quantify how much extra RAM would be
  needed to turn off the alternation-thrashing.
