# CS2640 Final Project

This project studies CephFS metadata performance for small-file workloads. It
compares native CephFS, subtree-pinning policies, and userspace cold-file
packing layers that reduce physical namespace pressure.

## Layout

- `report.pdf`: compiled USENIX-format report.
- `report/`: LaTeX source, figures, and raw benchmark result artifacts.
- `src/`: benchmark runner, storage/policy plugins, scripts, and project notes.
- `ai-usage.md`: short AI usage report.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt
```

## Build the Report

```sh
cd report
make
cp main.pdf ../report.pdf
```

Regenerate report figures from the final aggregate CSVs:

```sh
python3 src/scripts/generate_paper_figures.py
```

## Run Benchmarks

Local POSIX smoke test:

```sh
./src/scripts/run_posix_bench.sh --suite quick
```

Local containerized CephFS development cluster:

```sh
./src/scripts/cephfs_up.sh
./src/scripts/cephfs_status.sh
./src/scripts/cephfs_smoke.sh
```

CloudLab-scale paper runs use:

```sh
./src/scripts/schedule_cloudlab_paperready_bench.sh
```

Results are written under `report/results/`.

### Readme's in each of the directories under src/ detail how to use modular components within these directories, eg: different policies, storage options, etc.

For optimal recreation of results/working on this further, use at least 4 node setup with a dedicated client node.