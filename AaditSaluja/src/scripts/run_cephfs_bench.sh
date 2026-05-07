#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONTAINER="${CEPH_CONTAINER:-cs2640-ceph-mon}"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
REMOTE_SRC="/tmp/cs2640-bench-src-${STAMP}"
REMOTE_RESULTS="/tmp/cs2640-bench-results-${STAMP}"
LOCAL_RESULTS="${BENCH_RESULTS_DIR:-report/results}"
OUTPUT_NAME="${BENCH_OUTPUT_NAME:-cephfs-${STAMP}.json}"
CSV_NAME="${BENCH_CSV_NAME:-cephfs-${STAMP}.csv}"

mkdir -p "$LOCAL_RESULTS"
docker exec "$CONTAINER" mkdir -p "$REMOTE_SRC" "$REMOTE_RESULTS"
docker cp src/cephfs_metadata "$CONTAINER:$REMOTE_SRC/"

docker exec \
  -e "PYTHONPATH=$REMOTE_SRC" \
  "$CONTAINER" \
  python -m cephfs_metadata.cephfs_py2_benchmark \
    --root "${BENCH_ROOT:-/cs2640-bench}" \
    --output "$REMOTE_RESULTS/$OUTPUT_NAME" \
    --csv "$REMOTE_RESULTS/$CSV_NAME" \
    "$@"

docker cp "$CONTAINER:$REMOTE_RESULTS/$OUTPUT_NAME" "$LOCAL_RESULTS/$OUTPUT_NAME"
docker cp "$CONTAINER:$REMOTE_RESULTS/$CSV_NAME" "$LOCAL_RESULTS/$CSV_NAME"
docker exec "$CONTAINER" rm -rf "$REMOTE_SRC" "$REMOTE_RESULTS"

echo "wrote $LOCAL_RESULTS/$OUTPUT_NAME"
echo "wrote $LOCAL_RESULTS/$CSV_NAME"
