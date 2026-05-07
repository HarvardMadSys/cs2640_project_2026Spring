# Paper-Ready Benchmark Results

Last updated: 2026-05-06

Result directory: `report/results/cloudlab-paperready-20260506-3x/`.

## Run Status

- Completed result files: 162.
- Core in-repo cells: 5 repeats per native/oracle/predictor/no-learning cell.
- Scaled hot/cold cells: 3 repeats per variant.
- Direct external sidecars: 5 repeats per benchmark.
- Failure marker: none present after completion.
- Scheduler controls: serial randomized order, unique roots, `HEALTH_OK` checks, node0 page-cache drops, active MDS cache drops, and exact command/log capture.
- Residual caveat: server-node Linux page caches were not dropped from node0; OSDs are directory-backed on root disks in this allocation.

## Direct External Benchmarks

| Benchmark | Runs | Mean ops/s | Stdev |
| --- | ---: | ---: | ---: |
| `direct_mdtest` | 5 | 2252.49 | 180.70 |
| `direct_mdtest_10k` | 5 | 2046.26 | 80.52 |
| `direct_filebench_fileserver` | 5 | 458.58 | 58.73 |
| `direct_filebench_varmail` | 5 | 343.90 | 34.07 |
| `direct_ior` | 5 | 29284.53 | 1215.17 |
| `direct_ior_512m` | 5 | 23389.33 | 587.98 |

## In-Repo Policy Matrix

| Workload | Native ops/s | Oracle ops/s | Predictor ops/s | Oracle vs native | Predictor vs native | Predictor precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Recreated mdtest | 446.08 | 11605.16 | 11121.33 | 26.02x | 24.93x | 0.000 |
| Recreated varmail | 89.83 | 993.89 | 289.65 | 11.06x | 3.22x | 0.000 |
| YCSB Zipfian | 223.63 | 448.31 | 414.50 | 2.00x | 1.85x | 0.0625 |
| YCSB hotspot | 236.50 | 544.62 | 566.64 | 2.30x | 2.40x | 0.0625 |
| Hot/cold 90/10 | 490.49 | 1098.20 | 1316.19 | 2.24x | 2.68x | 0.305 |

## Follow-Up Results

| Workload | Variant | Runs | Mean ops/s | vs oracle |
| --- | --- | ---: | ---: | ---: |
| False-hot churn | native | 5 | 140.34 | 0.004x |
| False-hot churn | oracle | 5 | 36537.80 | 1.00x |
| False-hot churn | predictor | 5 | 188.96 | 0.005x |
| False-hot churn | no-learning | 5 | 29632.48 | 0.81x |
| Scaled hot/cold 20k | native | 3 | 334.60 | 0.53x |
| Scaled hot/cold 20k | oracle | 3 | 626.40 | 1.00x |
| Scaled hot/cold 20k | predictor | 3 | 791.92 | 1.26x |
| Scaled hot/cold 20k | no-learning | 3 | 8389.15 | 13.39x |

## Interpretation

Packing is still the clearest result. Oracle packing is 26.0x native on
recreated mdtest and 11.1x native on recreated varmail-like. The fair predictor
gets most of the mdtest gain but is much weaker on varmail-like.

The predictor should not be framed as universally beating oracle. It is a lazy
cold-by-default policy that keeps existing files packed and only changes future
creates after learning a hot directory. Its YCSB throughput is close to oracle,
but its generator-labeled hot-directory precision is only 0.0625.

The false-hot benchmark is the main downside result. Once the predictor marks
cold directories as hot, a later cold create wave becomes native CephFS work,
and throughput drops to 189 ops/s. Oracle and no-learning packing remain orders
of magnitude faster on that workload.
