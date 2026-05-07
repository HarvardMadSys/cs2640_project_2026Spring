# Hot Start Context

Last updated: 2026-05-06

Read this first in a new session, then read `src/docs/CLOUDLAB_REALLOCATION_HANDOFF.md`
if working on CloudLab setup or node reallocation.

## Project State

- This repo is a fresh implementation for the CS2640 CephFS metadata project.
- Goal: evaluate metadata-heavy CephFS workloads and two improvement directions:
  metadata placement policies and small-file packing.
- Placement-only `ceph.dir.pin` policies did not beat default CephFS on the
  tested workloads.
- Strongest improvement result: cold-file packing reduces CephFS namespace
  pressure. In the final paper-ready run, predictive cold packing reached
  `24.93x` native on recreated mdtest and `2.68x` native on hot/cold 90/10.
- Predictor prototype: `predictive_cold_segments` packs new files first and
  moves future creates in learned-hot directories back to native CephFS.
- Predictor audit result: the first predictor profile was inflated by two
  artifacts and is not paper-reportable as a performance claim. Packed stats
  were pure in-memory index lookups, and hot churn creates stayed packed until
  individual paths were promoted. Local code now has `packed_stat_mode=index`,
  predictor directory promotion, and seeded-random hot-churn directory
  selection.
- Final predictor candidate: `pred_dirhot_lazy_read`, implemented with
  `predictor_strategy=directory_hotset`,
  `predictor_promote_existing=false`, read-only triggers, and directory
  thresholds 8 events / 3 distinct paths. The final report pairs its positive
  hot/cold result with a false-hot negative case to keep the claim bounded.

## Current Artifacts

- Complete node0 CloudLab results are local in `report/results/`.
- Current result summary is in `report/results/RESULTS.md`.
- Pre-expiry CloudLab archive is local in
  `report/results/archive/cloudlab_archive/20260429-node-expiry/`.
- Reallocation/setup handoff is in `src/docs/CLOUDLAB_REALLOCATION_HANDOFF.md`.
- Main project chronology and interpretations are in `src/docs/PROJECT_NOTES.md`.
- Slide structure is in `src/docs/PRESENTATION_LAYOUT.md`.
- Workload and benchmark usage details are in `src/docs/BENCHMARKS.md`.
- Small-dirs vs hot-dirs graph takeaways are in
  `src/docs/SMALLDIRS_HOTDIRS_GRAPHS.md`.
- Oracle hybrid summary and new presentation figures are in
  `src/docs/ORACLE_HYBRID_RESULTS.md`.

## CloudLab Snapshot To Remember

- Current 2026-05-06 topology is 4 nodes:
  - node0/client: `pc40.cloudlab.umass.edu`
  - node1/server: `pc31.cloudlab.umass.edu`
  - node2/server: `pc25.cloudlab.umass.edu`
  - node3/server: `pc29.cloudlab.umass.edu`
- Current daemon layout after the fresh rebuild: node0 is client only; node1
  runs monitor, manager, OSD, and active MDS rank 0; node2 runs an OSD and
  active MDS rank 1; node3 runs an OSD and standby MDS.
- Working Ceph version was Nautilus `14.2.22` via
  `quay.io/ceph/daemon:latest-nautilus`.
- Current Ceph v17/Quincy containers and Ubuntu Ceph binaries failed on the
  CloudLab CPUs due to unsupported x86 instruction requirements.
- The live cluster used Podman containers and `/opt/cs2640-ceph` bind mounts.
- This allocation has no `/dev/sdb`; the current OSDs are directory-backed under
  `/opt/cs2640-ceph` on the root disks. Treat comparisons as fair within this
  allocation, not directly comparable to an ideal dedicated-disk allocation.
- Exact Podman commands, config locations, keyring archive locations, and rebuild
  checklist are in `src/docs/CLOUDLAB_REALLOCATION_HANDOFF.md`.

## Reallocation Routine

- Refresh local `~/.ssh/config` so `cs2640` and `cs2640-node*` point at the new
  public hosts.
- Re-read `src/docs/CLOUDLAB_REALLOCATION_HANDOFF.md` before starting any new
  CloudLab session.
- Run a single smoke benchmark after the new machines are reachable, then
  resume larger experiments.

## Results To Carry Forward

- Policy matrix: static and reactive pinning usually hurt. Some apparent
  predictive wins had zero pin events and should be treated as run variance.
- Retained-data improvement matrix: `append_segments` reduced data objects from
  thousands to a few, but write throughput was below native CephFS.
- Sharded packing matrix: directory/hash sharding made packed writes slower,
  likely because it added physical files and index logs.
- Pin-rank/co-location diagnosis: pin rank matters, but pinning hot directories
  still lost to default; co-locating root and hot child did not fix it.
- Oracle hybrid matrix: batching cold-file data/journal writes, keeping cold
  directories virtual, and leaving hot files native produced mean speedups over
  native in all six hot/cold scenarios. Best cell: `cold90_access10`, hybrid
  `1969.90` ops/s versus native `393.39` ops/s (`5.01x`).
- Predictive cold-packing profile: old profile numbers are preserved in
  `report/results/RESULTS.md` for traceability, but they are marked pre-fix. Treat
  them as an audit finding, not as final evidence.
- Representative smoke results and the post-fix predictor diagnosis are in
  `report/results/RESULTS.md`, with raw files under
  `report/results/cloudlab-representative-smoke-20260506/`,
  `report/results/cloudlab-predictor-diagnosis-20260506/`, and
  `report/results/cloudlab-predictor-lazyhotset-20260506-after-mkdir-cache/`.
- Active comprehensive benchmark run:
  `report/results/cloudlab-comprehensive-20260506-3x/` completed 54/54 runs.
- Paper-readiness synthesis is in `src/docs/PAPER_READINESS_REPORT.md`; plots are
  in `report/figures/comprehensive_*.svg`.
- Final USENIX-format paper source is in `report/main.tex`; compiled output is
  `report/main.pdf`, with the submission copy at the repository root as
  `report.pdf`.
- Paper-ready benchmark handoff is in
  `src/docs/PAPER_READY_BENCHMARK_HANDOFF.md`. The node0 output directory is
  `report/results/cloudlab-paperready-20260506-3x/`. The scheduler completed 63/63 jobs with no failure marker.
- The paper-ready scheduler is now patched and augmented to 63 planned jobs:
  the original 54 plus larger mdtest, larger IOR, and direct Filebench varmail
  cells.
- Clean completed analysis is in
  `report/results/cloudlab-paperready-20260506-3x/PAPER_READY_RESULTS.md`. Original
  SVG figures are `report/figures/paperready_*.svg`; current paper PDF figures
  with error bars are `report/figures/paperready_*errorbars.pdf`.
- Final direct external means: mdtest 3k `2252.49` ops/s, mdtest 10k
  `2046.26`, Filebench fileserver `458.58`, Filebench varmail `343.90`, IOR
  16 MiB `29284.53`, and IOR 512 MiB `23389.33`.
- Final paper-ready in-repo headline: predictor is 24.93x native on recreated
  mdtest, 3.22x on recreated varmail, 1.85x on YCSB Zipfian, 2.40x on YCSB
  hotspot, and 2.68x on hot/cold 90/10. YCSB predictor precision is weak
  (`0.0625`), and hot/cold precision is `0.3047`, so frame predictor claims
  carefully.
- Follow-up scheduler work is complete for the final paper:
  `predictor_nolearn` ablates learning while preserving cold-by-default packing,
  `predictor_false_hot_churn` is the negative benchmark for false-hot
  prediction, core cells now have five repeats, and
  `scaled_hotcold_cold90_access10_20k` is the scaled-up policy run.
- Paper-ready PDF figures with error bars are generated by
  `src/scripts/generate_paper_figures.py` and written to
  `report/figures/*errorbars.pdf`.
- The local `report/main.pdf` and root `report.pdf` have been rebuilt from the
  completed synced follow-up results.
- CephFS layout-xattr bug: directory defaults must use `ceph.dir.layout.*`,
  while segment files use `ceph.file.layout.*`. A 2026-05-06 CloudLab smoke
  after the fix attempted 6 layout xattrs, applied 6, and recorded no failures.

## Next Useful Work

- Generate final plots from
  `report/results/cloudlab-hotcold-matrix-20260506-3x/summary.csv` and
  `phase_summary.csv`, including error bars because several hybrid cells have
  high run-to-run variance.
- Expand `predictive_cold_segments` testing from the three-scenario profile to a
  full six-scenario matrix with at least three repeats, using
  `packed_stat_mode=index` and `pred_dirhot_lazy_read`.
- For final presentation/report work, start from
  `src/docs/PAPER_READINESS_REPORT.md`, then inspect
  `report/results/cloudlab-comprehensive-20260506-3x/summary.csv` and
  `phase_summary.csv`.
- Use `report/results/cloudlab-paperready-20260506-3x/PAPER_READY_RESULTS.md`,
  `summary.csv`, `phase_summary.csv`, and `external_phase_summary.csv` for the
  completed 63-job paper-ready matrix.
- For a fresh paper build, rerun `python3 src/scripts/generate_paper_figures.py`
  and rebuild `report/main.pdf` from `report/`.
- Compare plain hybrid against hybrid-layout carefully: layout xattrs had zero
  failures, but the layout variant only won two of six scenarios by mean
  throughput.
- Keep generated figures large-font and takeaway-oriented for final slides.
- If CloudLab nodes are reallocated, use
  `src/docs/CLOUDLAB_REALLOCATION_HANDOFF.md` before running new experiments.
