# Comprehensive Benchmark Handoff

Last updated: 2026-05-06

## Active Run

The comprehensive CloudLab benchmark is running on node0 under `nohup`.

```sh
cd ~/FinalProj
pgrep -af schedule_cloudlab_comprehensive
tail -f report/results/cloudlab-comprehensive-20260506-3x/scheduler.log
```

Output directory:

```sh
report/results/cloudlab-comprehensive-20260506-3x/
```

Scheduler command:

```sh
nohup ./src/scripts/schedule_cloudlab_comprehensive_bench.sh \
  --out-dir report/results/cloudlab-comprehensive-20260506-3x \
  --repeats 3 \
  --seed-base 8000 \
  --order-seed 264005061 \
  --sleep 5 \
  > report/results/cloudlab-comprehensive-20260506-3x/scheduler.log 2>&1 &
```

Expected jobs:

- 54 total jobs.
- 6 workload scenarios.
- 3 storage variants.
- 3 repeats.
- Randomized serial order.

At handoff time, the first two jobs had completed successfully and the third
was running:

- `hotcold_cold70_access10__oracle__r1`
- `hotcold_cold90_access10__oracle__r1`
- running: `ycsb_zipf_hotdirs__native__r2`

The cluster was `HEALTH_OK` when the run was launched and after the first
completed jobs.

## Output Files

The scheduler updates these incrementally after each completed job:

- `scheduler.log`: high-level start/done log.
- `jobs.tsv`: full unshuffled job manifest.
- `jobs_shuffled.tsv`: randomized execution order.
- `completed.tsv`: completed job records and wall-clock duration.
- `FAIRNESS.md`: encoded fairness assumptions for this run.
- `run_manifest.csv`: one row per completed JSON.
- `summary.csv`: aggregate ops/s and p95 summary by workload and variant.
- `phase_summary.csv`: per-phase ops/s, p95, and p99 summary.
- `summary.md`: human-readable aggregate table.
- `<workload>__<variant>__r<repeat>.json`: full benchmark JSON.
- `<workload>__<variant>__r<repeat>.csv`: phase rows for one run.
- `<workload>__<variant>__r<repeat>.log`: stdout/stderr for one run.
- `<workload>__<variant>__r<repeat>.cmd`: exact command line.

If the run is interrupted, rerun the same scheduler command. It is resumable by
default and skips runs with existing JSON and CSV outputs.

## Workloads

Representative recreated workloads:

- `ior_mdtest_tree`: in-repo IOR/mdtest-style directory tree metadata phases.
- `filebench_varmail_like`: in-repo Filebench mail-style mixed macro workload.
- `ycsb_zipf_hotdirs`: in-repo skewed hot-directory workload, standing in for
  YCSB-style Zipf/hotspot access.
- `hotcold_cold70_access10`: hot/cold locality stress, 70% cold dataset and
  10% cold accesses.
- `hotcold_cold90_access10`: hot/cold locality stress, 90% cold dataset and
  10% cold accesses.
- `hotcold_cold90_access20`: hot/cold locality stress, 90% cold dataset and
  20% cold accesses.

## Storage Variants

- `native`: direct CephFS files.
- `oracle`: oracle cold packing. On `oracle_hotcold_mix`, oracle sees the
  benchmark-defined `hot*` directories. On generic representative workloads
  without production-visible labels, oracle receives no hidden labels and runs
  as all-cold packing. On `ycsb_zipf_hotdirs`, oracle is allowed to know the
  generator-defined hot directory `dir0000`.
- `predictor`: `predictive_cold_segments` with directory-hotset lazy read mode:
  `predictor_strategy=directory_hotset`,
  `predictor_promote_existing=false`,
  `predictor_dir_event_threshold=8`,
  `predictor_dir_distinct_threshold=3`,
  `promotion_triggers=read`, and `packed_stat_mode=index`.

The predictor does not inspect `hot*` or `cold*` labels for placement. It only
observes online read operations and learns hot parent directories from observed
accesses.

## Local Follow-Up

When the run completes, copy results back:

```sh
rsync -az cs2640:~/FinalProj/report/results/cloudlab-comprehensive-20260506-3x/ \
  report/results/cloudlab-comprehensive-20260506-3x/
```

Then inspect:

```sh
sed -n '1,120p' report/results/cloudlab-comprehensive-20260506-3x/summary.md
column -s, -t < report/results/cloudlab-comprehensive-20260506-3x/summary.csv | less -S
column -s, -t < report/results/cloudlab-comprehensive-20260506-3x/phase_summary.csv | less -S
```
