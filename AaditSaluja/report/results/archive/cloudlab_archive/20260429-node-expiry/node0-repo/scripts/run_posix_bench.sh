#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHONPATH=src "${PYTHON:-python3}" -m cephfs_metadata.benchmark_runner \
  --backend posix \
  --root "${BENCH_ROOT:-/tmp/cs2640-cephfs-bench}" \
  --output "${BENCH_OUTPUT:-results/posix-latest.json}" \
  --csv "${BENCH_CSV:-results/posix-latest.csv}" \
  "$@"
