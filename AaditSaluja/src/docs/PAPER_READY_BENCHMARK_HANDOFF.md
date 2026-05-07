# Paper-Ready Benchmark Handoff

Last updated: 2026-05-06

## Follow-Up Run Launched After Paper-Ready Matrix

The next scheduler invocation extends the completed 63-job matrix instead of
replacing it. It is intended to make the current paper claims stronger and to
add a negative predictor result:

- Increase existing paper-ready cells from three to five repeats. With resume
  enabled, existing `r0`--`r2` artifacts are skipped and only `r3`--`r4` are
  run.
- Add `predictor_nolearn`, a cold-by-default packing ablation using the same
  predictive storage plugin with `predictor_strategy=never_promote`. This
  isolates packing benefit from learning benefit.
- Add `predictor_false_hot_churn`, a downside benchmark that induces false-hot
  directory predictions before a create-heavy cold-file churn phase.
- Add `scaled_hotcold_cold90_access10_20k`, a 20k-file / 80k-op hot/cold run
  with 128 directories. This is the scheduled scaled-up experiment.
- Generate PDF plots with error bars via
  `src/scripts/generate_paper_figures.py`.

With all follow-up switches enabled and `--repeats 5`, the scheduler writes
162 planned job rows. Because the original 63 artifacts already exist and
resume is enabled, the expected new work is 99 jobs: two extra repeats for the
original cells, five-repeat no-learning ablations, five-repeat false-hot
downside cells, and three-repeat scaled cells.

Launch command:

```sh
cd ~/FinalProj
nohup ./src/scripts/schedule_cloudlab_paperready_bench.sh \
  --out-dir report/results/cloudlab-paperready-20260506-3x \
  --repeats 5 \
  --include-ablation \
  --include-false-hot \
  --include-scaled \
  --scaled-repeats 3 \
  --filebench-runtime 60 \
  > report/results/cloudlab-paperready-20260506-3x/followup_scheduler.log 2>&1 &
```

Status check:

```sh
pgrep -af schedule_cloudlab_paperready
tail -80 report/results/cloudlab-paperready-20260506-3x/followup_scheduler.log
test ! -f report/results/cloudlab-paperready-20260506-3x/FAILED
```

Node0 does not currently have `pdflatex`; regenerate final PDF figures locally
after copying the completed follow-up result directory back.

## Current CloudLab Run Status

The paper-readiness benchmark scheduler completed on node0.

```sh
ssh cs2640
cd ~/FinalProj
pgrep -af schedule_cloudlab_paperready || true
tail -120 report/results/cloudlab-paperready-20260506-3x/scheduler.log
```

Output directory:

```sh
report/results/cloudlab-paperready-20260506-3x/
```

Final artifact status:

- Completed result files: 63/63.
- Direct external sidecars: 18/18.
- In-repo native/oracle/predictor runs: 45/45.
- Repeats: 3 per cell.
- Failure marker: none present after completion.
- Detailed analysis: `report/results/cloudlab-paperready-20260506-3x/PAPER_READY_RESULTS.md`.
- Generated figures: `report/figures/paperready_inrepo_throughput.svg`,
  `report/figures/paperready_inrepo_speedup.svg`,
  `report/figures/paperready_external_throughput.svg`, and
  `report/figures/paperready_predictor_precision.svg`.

Main files to inspect:

- `PAPER_READY_RESULTS.md`: clean analysis, interpretation, and graph links.
- `summary.md`: compact rolling summary.
- `summary.csv`: per workload/variant means and predictor directory precision.
- `phase_summary.csv`: per phase means, including in-repo p95/p99 latency.
- `external_phase_summary.csv`: parsed direct IOR/mdtest/Filebench phase rates.
- `run_manifest.csv`: one row per completed result file.
- `*.cmd`: exact command for every run.
- `*.log`: raw stdout/stderr for every run.
- `*.json` and `*.csv`: in-repo raw result artifacts.
- `*.external.json`: parsed direct external benchmark sidecars.
- `cache_drop.log`: cache drop and ASLR setup log.

## What Was Installed

External tools are installed on node0 under:

```sh
~/bench-tools/bin/ior
~/bench-tools/bin/mdtest
~/bench-tools/bin/filebench
```

Build sources are under:

```sh
~/bench-tools/src/ior
~/bench-tools/src/filebench
```

The scheduler uses the upstream `filebench` build from `~/bench-tools`. Filebench
requires Linux address-space randomization disabled for stable worker startup on
this node, so the scheduler runs:

```sh
sudo sysctl -w kernel.randomize_va_space=0
```

An older tagged Filebench build also exists at `~/bench-tools-filebench149`, but
the active scheduler uses the primary `~/bench-tools/bin/filebench` after ASLR
was disabled.

## Scheduled Workloads

There are 63 jobs total after the latest scheduler update:

- Direct external validation, three repeats each:
  - `direct_mdtest`: `mpirun` plus IOR/mdtest `mdtest`.
  - `direct_ior`: `mpirun` plus IOR POSIX file-per-process read/write.
  - `direct_filebench_fileserver`: upstream Filebench `fileserver.f`, scaled to
    3000 files, 8 threads, 4 KiB file/IO sizes, 60 second timed run.
  - `direct_mdtest_10k`: larger mdtest run with 10,000 1 KiB files. This checks
    whether conclusions survive a larger metadata namespace than the first
    3000-file external smoke.
  - `direct_ior_512m`: larger POSIX IOR run with 512 MiB aggregate data
    (`8 ranks * 16384 segments * 4 KiB`). This is intended to reduce the cache
    sensitivity of the original 16 MiB direct IOR cell.
  - `direct_filebench_varmail`: upstream Filebench `varmail.f`, scaled to 3000
    files, 8 threads, 4 KiB file/append/read sizes, 60 second timed run. This
    is the closest direct external counterpart to the in-repo varmail-like
    workload.
- In-repo policy comparison, three repeats each, across `native`, `oracle`, and
  `predictor`:
  - `recreated_mdtest_tree`
  - `recreated_filebench_varmail_like`
  - `ycsb_zipfian_file_skew`
  - `ycsb_hotspot_file_skew`
  - `hotcold_cold90_access10`

The in-repo YCSB workload now has:

- load/create phase
- read phase
- update phase
- file-level Zipfian or hotspot path sampling
- seed-shuffled rank order inside hot/cold groups to avoid lexicographic bias
- predictor metrics for predicted hot dirs, predicted false-hot/cold dirs, and
  predicted hot-directory precision

## Fairness Controls

The scheduler is serial and globally randomized with order seed `26400507`.
Each policy cell uses the same seed and workload parameters across native,
oracle, and predictor.

Before every job it:

```sh
sync
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
ceph tell mds.<active-name> cache drop
```

It discovers active MDS names from `ceph mds stat --format json`. Server-node
Linux page caches are not dropped from node0 because node0 cannot SSH to the
server nodes with the current key setup. Treat this as a residual limitation for
paper claims; randomized order, repeated trials, unique roots, node0 cache
drops, and MDS cache drops reduce but do not fully eliminate cache bias.

Policy information boundaries:

- `native`: direct CephFS POSIX files.
- `oracle`: receives `hot` prefixes only for workloads with generator-defined
  hot sets, currently `oracle_hotcold_mix` and `ycsb_file_skew`. Generic
  recreated mdtest/Filebench shapes receive no hidden hot labels.
- `predictor`: uses only online read observations. It does not inspect `hot*`
  or `cold*` path names for decisions.

## Final Results

Direct external benchmark summary:

| Benchmark | Runs | Mean ops/s | Stdev | Notes |
| --- | ---: | ---: | ---: | --- |
| `direct_mdtest` | 3 | 2272.67 | 251.62 | Upstream mdtest, 3000 1 KiB files, 8 MPI ranks. |
| `direct_mdtest_10k` | 3 | 2011.84 | 70.76 | Upstream mdtest, 10000 1 KiB files, 8 MPI ranks. |
| `direct_filebench_fileserver` | 3 | 462.80 | 49.02 | Upstream Filebench fileserver, 3000 files, 8 threads, 60 s. |
| `direct_filebench_varmail` | 3 | 345.67 | 40.21 | Upstream Filebench varmail, 3000 files, 8 threads, 60 s. |
| `direct_ior` | 3 | 28561.87 | 356.00 | IOR POSIX file-per-process, 16 MiB aggregate. |
| `direct_ior_512m` | 3 | 23456.95 | 635.57 | IOR POSIX file-per-process, 512 MiB aggregate. |

In-repo policy matrix:

| Workload | Native ops/s | Oracle ops/s | Predictor ops/s | Predictor vs native | Predictor precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| Recreated mdtest | 455.46 | 11805.66 | 11239.52 | 24.68x | 0.0 |
| Recreated varmail | 84.53 | 956.23 | 286.91 | 3.39x | 0.0 |
| YCSB Zipfian | 225.57 | 442.06 | 426.24 | 1.89x | 0.0625 |
| YCSB hotspot | 248.66 | 568.59 | 593.99 | 2.39x | 0.0625 |
| Hot/cold 90/10 | 383.47 | 972.62 | 1289.40 | 3.36x | 0.338461 |

See `PAPER_READY_RESULTS.md` for phase bottlenecks, variance, and interpretation.

## Failure Mode

The scheduler stopped immediately after the measured
`direct_filebench_fileserver__external__r1` run. Filebench was launched with
`sudo`, created root-owned files under the CephFS benchmark root, and then the
scheduler attempted:

```sh
rm -rf "$root"
```

That produced many `Permission denied` cleanup errors and exited under
`set -e`. The benchmark itself had already written:

```sh
report/results/cloudlab-paperready-20260506-3x/direct_filebench_fileserver__external__r1.external.json
```

The scheduler was patched locally and synced to node0 after inspection:
external job cleanup uses `sudo rm -rf "$root"`, and the Filebench per-operation
parser captures names such as `statfile1` and `appendfilerand1` instead of
numeric fragments. The first Filebench sidecar in this directory still reflects
the old parser because it was written before the patch; later repeats use the
corrected parser.

The scheduler was also augmented with the three larger/direct external cells
listed above before the final 63-job run completed.

## Observed Before Earlier Handoff

Two scheduler bugs were fixed before handoff:

- Blank TSV fields were being collapsed by Bash and passing `0` as
  `--ycsb-distribution`.
- `mpirun` inherited stdin from the job TSV pipe and consumed remaining job
  lines after the first external `mdtest`; benchmark commands now run with
  stdin redirected from `/dev/null`.

Previously observed successful scheduled jobs:

- `recreated_filebench_varmail_like__predictor__r0`
- `ycsb_zipfian_file_skew__predictor__r0`
- `direct_mdtest__external__r1`

At that earlier point `summary.md`, `summary.csv`, `phase_summary.csv`, and
`external_phase_summary.csv` were being updated correctly.

## Rerun Commands

The final run completed cleanly, so these commands are only for a future rerun
or failure investigation. If a new run stops and `FAILED` exists:

```sh
cd ~/FinalProj
cat report/results/cloudlab-paperready-20260506-3x/FAILED
tail -100 report/results/cloudlab-paperready-20260506-3x/*.log
```

Rerun with the same output directory. The scheduler is resumable and skips
completed JSON/CSV results.

```sh
cd ~/FinalProj
nohup ./src/scripts/schedule_cloudlab_paperready_bench.sh \
  --out-dir report/results/cloudlab-paperready-20260506-3x \
  --repeats 3 \
  --filebench-runtime 60 \
  > report/results/cloudlab-paperready-20260506-3x/scheduler.log 2>&1 &
```

When the run finishes, copy the directory back locally and start analysis from:

```sh
report/results/cloudlab-paperready-20260506-3x/summary.csv
report/results/cloudlab-paperready-20260506-3x/phase_summary.csv
report/results/cloudlab-paperready-20260506-3x/external_phase_summary.csv
```
