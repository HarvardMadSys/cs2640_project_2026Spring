# Results State

Last updated: 2026-05-06

## Paper-Ready Scheduler Status

The final paper-ready result set is in
`report/results/cloudlab-paperready-20260506-3x/`.

- Completed result files: 162.
- Core in-repo cells and direct external sidecars have five repeats.
- The scaled hot/cold 20k follow-up has three repeats per variant.
- `predictor_nolearn` is included as the cold-by-default packing ablation.
- `predictor_false_hot_churn` is included as the negative predictor-precision
  benchmark.
- Final PDF figures are under `report/figures/` and the compiled report is
  `report.pdf`.

## CloudLab Hot/Cold Matrix

Result directory:

```sh
report/results/cloudlab-hotcold-matrix-20260506-3x/
```

Remote source:

```sh
cs2640:~/FinalProj/report/results/cloudlab-hotcold-matrix-20260506-3x/
```

Run status:

- Completed 54 of 54 runs.
- Three repeats per scenario/variant cell.
- Raw artifacts copied back locally: 54 JSON files, 54 CSV files, and 54 logs.
- Generated aggregates: `summary.md`, `summary.csv`, and
  `phase_summary.csv`.
- Ceph was `HEALTH_OK` after the run.
- Layout xattr failures across all benchmark JSON files: 0.

## Matrix Summary

Mean measured ops/s across three runs:

| Scenario | Native | Hybrid | Hybrid speedup | Hybrid + layout | Layout speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| `cold70_access0` | 361.46 | 540.88 | 1.50x | 391.02 | 1.08x |
| `cold70_access10` | 333.98 | 398.07 | 1.19x | 726.85 | 2.18x |
| `cold70_access20` | 318.28 | 1090.59 | 3.43x | 424.67 | 1.33x |
| `cold90_access0` | 345.38 | 1002.16 | 2.90x | 593.16 | 1.72x |
| `cold90_access10` | 393.39 | 1969.90 | 5.01x | 1254.27 | 3.19x |
| `cold90_access20` | 393.89 | 860.88 | 2.19x | 888.02 | 2.25x |

Best mean variant per scenario:

| Scenario | Best variant | Mean ops/s | Speedup vs native |
| --- | --- | ---: | ---: |
| `cold70_access0` | hybrid | 540.88 | 1.50x |
| `cold70_access10` | hybrid + layout | 726.85 | 2.18x |
| `cold70_access20` | hybrid | 1090.59 | 3.43x |
| `cold90_access0` | hybrid | 1002.16 | 2.90x |
| `cold90_access10` | hybrid | 1969.90 | 5.01x |
| `cold90_access20` | hybrid + layout | 888.02 | 2.25x |

Across the six scenarios:

- Hybrid average speedup: 2.70x; median speedup: 2.54x.
- Hybrid + layout average speedup: 1.96x; median speedup: 1.95x.
- 70%-cold scenarios reduced estimated namespace entries from 4565 to 2409
  entries, a 47.2% reduction.
- 90%-cold scenarios reduced estimated namespace entries from 4565 to 1809
  entries, a 60.4% reduction.

## Interpretation

The oracle cold-packing policy beats native in every tested hot/cold mix by
mean measured throughput. The advantage is strongest when the dataset is mostly
cold, because the hybrid layout avoids materializing most cold directories and
cold files as native CephFS namespace entries.

The CephFS layout-xattr variant is not uniformly better than plain hybrid. It
wins in `cold70_access10` and `cold90_access20`, but plain hybrid wins the other
four scenarios. Treat layout xattrs as a tunable lower-level policy, not a
universal improvement.

The testbench is fair for within-allocation A/B testing:

- same cluster, mount, benchmark root, file count, file size, directory count,
  worker count, operation count, and cleanup behavior for every variant.
- same seeds reused across variants within each scenario/repeat.
- randomized global run order and serial execution.
- `ceph -s` health checked before every run.
- unique per-run workload roots to avoid direct same-path reuse.

Residual caveats:

- The current CloudLab allocation has no `/dev/sdb`; OSDs are directory-backed
  under `/opt/cs2640-ceph` on root disks. Comparisons are fair within this
  allocation, but not directly comparable to a dedicated-disk allocation.
- The runner did not drop kernel/Ceph caches or remount between runs. Randomized
  order and unique roots reduce, but do not eliminate, cache/order effects.
- Several hybrid cells have high run-to-run variance with only three repeats.
  The mean speedups are useful, but final presentation should mention variance
  or include error bars.

## Validation Already Completed

- Python compile passed for the changed benchmark/storage files and matrix
  runner.
- CloudLab layout-xattr smoke:
  `report/results/cloudlab-20260506-layout-xattr-fix-smoke.json`.
- The smoke attempted 6 layout xattrs, applied all 6, and recorded no failures.
- The earlier `EINVAL` was caused by applying `ceph.file.layout.*` to a
  directory. The code now uses `ceph.dir.layout.*` for directory defaults and
  `ceph.file.layout.*` for segment files.

## Useful Files

- `report/results/cloudlab-hotcold-matrix-20260506-3x/summary.md`
- `report/results/cloudlab-hotcold-matrix-20260506-3x/summary.csv`
- `report/results/cloudlab-hotcold-matrix-20260506-3x/phase_summary.csv`
- `report/results/cloudlab-hotcold-matrix-20260506-3x/run_manifest.json`

## Predictive Cold-Packing Profile

Result directories:

```sh
report/results/cloudlab-predictor-profile-20260506/
report/results/cloudlab-predictor-profile-native-20260506/
```

Audit status:

- The profile below is now classified as **pre-fairness-fix** and should not be
  used as a paper headline result.
- Root cause: packed `stat()` returned from the in-memory Python index, so the
  predictor avoided CephFS metadata work for many access-phase operations.
- Additional artifact: the predictor packed all newly created files, including
  hot-directory churn files. The oracle kept known-hot files native from create
  time, so the two policies were not paying the same hot-path costs.
- Fixes now in local code: `packed_stat_mode=index` forces packed stats to touch
  the packed index file, predictor directory promotion makes later files under a
  promoted hot directory native, and hot-churn parent directories are selected
  with the seeded RNG instead of deterministic round-robin assignment.
- A post-fix serial smoke on the rebuilt 2026-05-06 allocation with
  `file_count=300`, `ops=900`, `cold90_access10`, and seed `92` produced:
  native `3337.39` ops/s, oracle `4668.30` ops/s, and fair predictor
  `1284.03` ops/s. This is only a small smoke, but it confirms that the earlier
  multi-x predictor-over-oracle result was not reportable as measured.

## Representative Workload Smoke

Result directory:

```sh
report/results/cloudlab-representative-smoke-20260506/
```

Small serial CloudLab smoke runs against the current in-repo equivalents of
representative workload sources:

| Source shape | Workload | Native ops/s | Predictor ops/s | Notes |
| --- | --- | ---: | ---: | --- |
| IOR/mdtest-style metadata tree | `mdtest_tree` | 2433.20 | 6499.98 | Predictor packed all files; no promotions. |
| Filebench mail-style macro workload | `filebench_varmail_like` | 767.11 | 1761.48 | Predictor promoted 3 paths and packed 696 writes. |
| Skewed/YCSB-like directory access | `hotdirs_zipf` | 2356.67 | 9786.16 | Predictor packed all files; no repeated-path promotions. |
| Hot/cold mix | `oracle_hotcold_mix` | 728.79 | 2263.33 | Predictor still lagged oracle because stat/read promotion cost dominated. |

This is a smoke matrix, not a publication matrix. It is useful because it showed
the predictor was not generally broken; the slowdown was isolated to the
hot/cold predictor path where promotion cost is paid during measured access
phases.

## Predictor Improvement: Directory-Hotset Lazy Mode

Result directories:

```sh
report/results/cloudlab-predictor-diagnosis-20260506/
report/results/cloudlab-predictor-lazyhotset-20260506-after-mkdir-cache/
```

Diagnosis on `cold90_access10`, `file_count=800`, `ops=2400`, seed `401`:

| Variant | Mean ops/s | Key issue |
| --- | ---: | --- |
| native | 727.46 | Native hot churn create was slow on this rebuilt allocation. |
| oracle | 4593.31 | Upper-bound policy for this workload. |
| `pred_read1` | 2731.04 | Fast stats, but read phase promoted 141 files, including 59 cold false positives. |
| `pred_statread4` | 2148.56 | Stat phase slowed by synchronous promotions. |

Implemented fixes:

- `predictor_strategy=directory_hotset`: classify a parent directory as hot only
  after repeated access events and enough distinct paths.
- `predictor_promote_existing=false`: lazy mode keeps already-packed files
  packed and only uses the learned hot directory for future native creates.
- Cached materialized native directories so predicted-hot churn no longer calls
  `mkdir -p` on every create.

Post-fix smoke, same `cold90_access10` parameters:

| Variant | Ops/s | Predicted hot dirs | Promotions | Notes |
| --- | ---: | ---: | ---: | --- |
| `lazy_read` | 4143.12 | 4 | 0 | Cleanest: read-only learning found the 4 hot dirs. |
| `lazy_statread` | 4026.73 | 28 | 0 | Similar speed, but over-classified cold dirs because stat traffic includes cold accesses. |

The clean candidate is `pred_dirhot_lazy_read`: read-only directory-hotset
learning with lazy existing-file handling. It reached 90.2% of oracle throughput
on this smoke (`4143.12 / 4593.31`) and 5.70x native, while avoiding cold file
promotions.

## Comprehensive Benchmark

Completed on node0:

```sh
report/results/cloudlab-comprehensive-20260506-3x/
```

The run compares `native`, `oracle`, and `predictor` across six workload
scenarios with three repeats each. It records raw JSON, CSV, logs, exact command
lines, `summary.csv`, `phase_summary.csv`, and `summary.md`.

Status:

- 54/54 JSON runs completed.
- No `FAILED` marker was present.
- Paper-readiness synthesis: `src/docs/PAPER_READINESS_REPORT.md`.
- Simple plots: `report/figures/comprehensive_throughput.svg`,
  `report/figures/comprehensive_speedup.svg`, and
  `report/figures/comprehensive_p95.svg`.

Headline mean throughput:

| Workload | Native ops/s | Oracle ops/s | Predictor ops/s | Predictor vs native |
| --- | ---: | ---: | ---: | ---: |
| IOR/mdtest-style | 2412.22 | 12771.00 | 12461.66 | 5.17x |
| Filebench-like | 69.61 | 1106.62 | 248.96 | 3.58x |
| Zipf hotdirs | 321.59 | 402.25 | 14825.19 | 46.10x |
| 70% cold / 10% access | 320.57 | 1012.25 | 1506.55 | 4.70x |
| 90% cold / 10% access | 334.01 | 1115.13 | 1208.52 | 3.62x |
| 90% cold / 20% access | 337.89 | 787.34 | 1053.40 | 3.12x |

Interpretation caveat: the Zipf-hotdirs predictor result is not a paper
headline. The current hotdirs workload has no read phase, so read-only
directory-hotset prediction never learns and the predictor behaves like all-cold
packing. It is useful as a packing stress test, not as proof of predictor
quality.

## Paper-Ready Benchmark Stage

Result directory copied back locally:

```sh
report/results/cloudlab-paperready-20260506-3x/
```

Detailed analysis:

```sh
report/results/cloudlab-paperready-20260506-3x/PAPER_READY_RESULTS.md
```

Status:

- Completed result files: 162.
- Direct external sidecars: five repeats per benchmark.
- Core in-repo cells: five repeats per variant.
- Scaled hot/cold follow-up: three repeats per variant.
- Failure marker: none present after completion.
- Final report figures use PDF error-bar plots generated under
  `report/figures/paperready_*errorbars.pdf`.

Direct external measurements:

| Benchmark | Runs | Mean ops/s | Stdev | Notes |
| --- | ---: | ---: | ---: | --- |
| `direct_mdtest` | 5 | 2252.49 | 180.70 | Upstream mdtest, 3000 1 KiB files, 8 MPI ranks. |
| `direct_mdtest_10k` | 5 | 2046.26 | 80.52 | Upstream mdtest, 10000 1 KiB files, 8 MPI ranks. |
| `direct_filebench_fileserver` | 5 | 458.58 | 58.73 | Upstream Filebench fileserver, 3000 files, 8 threads, 60 s. |
| `direct_filebench_varmail` | 5 | 343.90 | 34.07 | Upstream Filebench varmail, 3000 files, 8 threads, 60 s. |
| `direct_ior` | 5 | 29284.53 | 1215.17 | IOR POSIX file-per-process, 16 MiB aggregate. |
| `direct_ior_512m` | 5 | 23389.33 | 587.98 | IOR POSIX file-per-process, 512 MiB aggregate. |

In-repo policy matrix:

| Workload | Native ops/s | Oracle ops/s | Predictor ops/s | Oracle vs native | Predictor vs native | Predictor precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Recreated mdtest | 446.08 | 11605.16 | 11121.33 | 26.02x | 24.93x | 0.0 |
| Recreated varmail | 89.83 | 993.89 | 289.65 | 11.06x | 3.22x | 0.0 |
| YCSB Zipfian | 223.63 | 448.31 | 414.50 | 2.00x | 1.85x | 0.0625 |
| YCSB hotspot | 236.50 | 544.62 | 566.64 | 2.30x | 2.40x | 0.0625 |
| Hot/cold 90/10 | 490.49 | 1098.20 | 1316.19 | 2.24x | 2.68x | 0.304664 |

Interpretation:

- Packing remains the strongest result. Oracle all-cold packing reaches 26.0x
  native on recreated mdtest and 11.1x native on recreated varmail-like; the
  fair predictor reaches 24.9x and 3.2x respectively.
- Direct external mdtest confirms file creation is the bottleneck: 155.01 ops/s
  for 3000 files and 141.79 ops/s for 10000 files, while stat/read rates are in
  the thousands of ops/s.
- The YCSB file-skew workloads validate the read/update path, but predictor
  hot-directory precision is poor at 0.0625 for both Zipfian and hotspot.
- The hot/cold 90/10 workload is the cleanest positive predictor locality result
  here: 2.68x native, 1.20x oracle, and 0.305 precision. Do not frame this as
  universal oracle beating; the lazy predictor and oracle have different
  semantics.

## Predictive Cold-Packing Profile Details

Implementation:

- New storage plugin: `predictive_cold_segments`.
- New files are packed first, without using `hot*`/`cold*` labels for the
  decision.
- The predictor observes `stat`/`read` traffic and promotes files back into
  native CephFS after a configurable access threshold.
- Profiled strategies:
  - `pred_read1`: promote after one read.
  - `pred_statread2`: promote after two stat/read events.
  - `pred_statread4`: promote after four stat/read events.

Same-window native baselines:

| Scenario | Native ops/s | Stdev |
| --- | ---: | ---: |
| `cold70_access20` | 360.23 | 15.63 |
| `cold90_access10` | 410.46 | 10.68 |
| `cold90_access20` | 383.04 | 0.60 |

Predictor profile, mean ops/s over two runs:

| Scenario | Variant | Mean ops/s | vs native | vs oracle profile | Promotions | Hot promos | Cold promos |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cold70_access20` | oracle | 530.77 | 1.47x | 1.00x | 0.0 | 0.0 | 0.0 |
| `cold70_access20` | `pred_read1` | 1403.33 | 3.90x | 2.64x | 1367.5 | 844.5 | 523.0 |
| `cold70_access20` | `pred_statread2` | 1603.11 | 4.45x | 3.02x | 1793.5 | 907.5 | 886.0 |
| `cold70_access20` | `pred_statread4` | 2608.79 | 7.24x | 4.92x | 1023.5 | 909.0 | 114.5 |
| `cold90_access10` | oracle | 588.73 | 1.43x | 1.00x | 0.0 | 0.0 | 0.0 |
| `cold90_access10` | `pred_read1` | 5714.55 | 13.92x | 9.71x | 593.0 | 304.5 | 288.5 |
| `cold90_access10` | `pred_statread2` | 4932.44 | 12.02x | 8.38x | 594.0 | 307.0 | 287.0 |
| `cold90_access10` | `pred_statread4` | 5291.78 | 12.89x | 8.99x | 315.5 | 309.0 | 6.5 |
| `cold90_access20` | oracle | 790.95 | 2.06x | 1.00x | 0.0 | 0.0 | 0.0 |
| `cold90_access20` | `pred_read1` | 5492.51 | 14.34x | 6.94x | 850.0 | 303.5 | 546.5 |
| `cold90_access20` | `pred_statread2` | 2861.80 | 7.47x | 3.62x | 1149.0 | 308.5 | 840.5 |
| `cold90_access20` | `pred_statread4` | 5259.36 | 13.73x | 6.65x | 383.5 | 309.5 | 74.0 |

Predictor interpretation:

- `pred_statread4` is the cleanest current strategy. It promotes nearly all
  benchmark-hot paths while avoiding most cold false promotions.
- `pred_read1` is very fast in 90%-cold scenarios, but it promotes many cold
  paths when the workload intentionally reads cold files 10%-20% of the time.
- `pred_statread2` is too eager on these mixes; it promotes many cold paths.
- The predictor can beat the oracle profile because it is not just estimating
  the oracle. It uses a different policy: cold-by-default packing plus online
  promotion. That reduces initial namespace creation even for paths that later
  become hot.

Reportability caveat:

- Do not describe the predictor result as “near oracle.” It is stronger than
  the oracle in this profile because it changes the policy model and because
  the pre-fix profile had the measurement artifacts listed above.
- Report it as a third design: **cold-by-default predictive promotion**.
- The result is promising, but it needs more validation before a paper-level
  claim: rerun the full six-scenario sweep with `packed_stat_mode=index`,
  directory promotion enabled, randomized hot churn, at least three repeats,
  error bars, and a discussion of how the packed index would be recovered and
  maintained in a production CephFS implementation.
