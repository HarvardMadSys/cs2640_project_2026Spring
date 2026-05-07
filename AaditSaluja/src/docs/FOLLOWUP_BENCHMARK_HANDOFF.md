# Follow-Up Benchmark Handoff

Last updated: 2026-05-06

This handoff covers the final follow-up work requested after the completed
63-job paper-ready matrix.

## Purpose

- Show the downside of inaccurate predictor classification with a throughput
  drop.
- Add a crucial ablation that separates packing benefit from predictor benefit.
- Increase weak three-repeat cells toward paper-reportable statistics.
- Schedule one scaled-up policy run.
- Keep all jobs serial, randomized, resumable, and checked against Ceph
  `HEALTH_OK`.

## New Cells

- `predictor_false_hot_churn`: negative workload. It reads generator-cold
  directories enough to make the predictor classify them as hot, then creates
  more cold files in those directories. Expected outcome: predictor throughput
  drops versus oracle and `predictor_nolearn` because future cold creates become
  native CephFS files.
- `predictor_nolearn`: ablation variant. Same predictive storage layer, same
  cold packing, but `predictor_strategy=never_promote`. This answers whether
  throughput comes from packing alone or from useful online learning.
- `scaled_hotcold_cold90_access10_20k`: scaled run with 20,000 files, 80,000
  ops, 128 directories, and 8 workers.

## Launch Command

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

## Check Status

```sh
pgrep -af schedule_cloudlab_paperready
tail -80 report/results/cloudlab-paperready-20260506-3x/followup_scheduler.log
test ! -f report/results/cloudlab-paperready-20260506-3x/FAILED
```

## After Completion

Node0 does not currently have a TeX installation with `pdflatex`, so generate
the final PDF figures locally after copying the completed result directory back
from CloudLab.

```sh
rsync -az cs2640:~/FinalProj/report/results/cloudlab-paperready-20260506-3x/ \
  report/results/cloudlab-paperready-20260506-3x/
python3 src/scripts/generate_paper_figures.py
cd report
make
```

The final false-hot, ablation, and scaled numbers have been copied into
`report/main.tex` from
`report/results/cloudlab-paperready-20260506-3x/summary.csv` and
`phase_summary.csv`.

## Final Local Paper State

After the follow-up scheduler completed, the local report was refreshed from
the final synced results:

- `report/figures/paperready_inrepo_throughput_errorbars.pdf`
- `report/figures/paperready_external_throughput_errorbars.pdf`
- `report/figures/paperready_predictor_precision_errorbars.pdf`
- `report/figures/paperready_followup_throughput_errorbars.pdf`
- `report/main.pdf`

The final report includes the false-hot downside result, the no-learning
ablation, and the scaled hot/cold cell.
