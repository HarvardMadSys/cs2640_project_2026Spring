# Paper-Ready Benchmark Fairness Notes

Started: 2026-05-06T18:24:12-06:00

This run has two roles:

- Direct external validation: IOR, mdtest, and Filebench run directly against
  the mounted CephFS filesystem using binaries under `/users/aadits/bench-tools`.
- In-repo policy comparison: recreated workload shapes run through the local
  Python runner against `native`, `oracle_cold_segments`, and
  `predictive_cold_segments`.

Controls:

- Jobs are serial and globally randomized with seed `26400507`.
- Each policy cell uses the same workload parameters and repeat seed.
- Each job gets a unique CephFS root under `/mnt/cephfs/cs2640-bench`.
- `ceph -s` must report HEALTH_OK before each job.
- Before each job, the scheduler drops the node0 Linux page cache, drops active
  MDS caches, and runs `sync`. Server-node Linux page caches cannot be dropped
  from node0 because this allocation does not allow node0 SSH access to the
  server nodes with the current key setup; this is recorded as a residual
  limitation.
- Filebench requires Linux address-space randomization disabled for stable
  worker startup on this build. The scheduler sets
  `kernel.randomize_va_space=0` at startup.

Policy information boundaries:

- `native` has no packing or predictor layer.
- `oracle` only receives benchmark-defined hot prefixes on workloads with a
  generator hot set: `oracle_hotcold_mix`, `ycsb_file_skew`, and
  `predictor_false_hot_churn`. Generic recreated mdtest/Filebench shapes
  receive no hidden hot labels and are packed as all-cold.
- `predictor` does not inspect `hot*` or `cold*` path labels for placement.
  It observes online read events, learns hot parent directories, and makes later
  creates under predicted-hot directories native. Existing packed files are not
  rewritten during measured read/stat phases.
- `predictor_nolearn`, when enabled, uses the same predictive storage layer
  but disables learning with `predictor_strategy=never_promote`. It is a
  cold-by-default packing ablation that isolates packing benefit from predictor
  benefit.
- `predictor_false_hot_churn`, when enabled, intentionally drives reads to
  generator-cold directories before a later create wave. It exposes the
  downside of false-hot directory classification: later cold creates become
  native under the predictor but remain packed under oracle and no-learning
  packing.

Outputs:

- Raw logs: `*.log`
- Exact commands: `*.cmd`
- In-repo JSON/CSV: `*.json`, `*.csv`
- External sidecar JSON: `*.external.json`
- Incremental summaries: `run_manifest.csv`, `summary.csv`,
  `phase_summary.csv`, `external_phase_summary.csv`, and `summary.md`
