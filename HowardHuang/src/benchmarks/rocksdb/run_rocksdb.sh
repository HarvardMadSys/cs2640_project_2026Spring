#!/bin/bash
#
# run_rocksdb.sh — RocksDB db_bench workloads on NVMe
#
# Usage: ./run_rocksdb.sh <nvme_mount> <results_dir>
#
set -euo pipefail

NVME_MOUNT="${1:-/mnt/nvme}"
RESULTS_DIR="${2:-/local/repository/results}"
DB_DIR="$NVME_MOUNT/rocksdb_bench"
WAL_DIR="$NVME_MOUNT/rocksdb_wal"

mkdir -p "$RESULTS_DIR/rocksdb" "$DB_DIR" "$WAL_DIR"

NUM_KEYS=5000000       # 5M keys
VALUE_SIZE=1024        # 1KB values (~5GB dataset)
DURATION=120           # 120 seconds per timed workload
NUM_THREADS=8

echo "=== RocksDB db_bench ==="
echo "Database dir: $DB_DIR"
echo "Keys: $NUM_KEYS, Value size: $VALUE_SIZE bytes"
echo "Threads: $NUM_THREADS, Duration: ${DURATION}s"
echo ""

# Common db_bench flags
COMMON_FLAGS="--db=$DB_DIR \
    --wal_dir=$WAL_DIR \
    --num=$NUM_KEYS \
    --value_size=$VALUE_SIZE \
    --threads=$NUM_THREADS \
    --compression_type=snappy \
    --statistics \
    --histogram \
    --bloom_bits=10 \
    --write_buffer_size=67108864 \
    --max_write_buffer_number=3 \
    --target_file_size_base=67108864 \
    --max_background_compactions=4 \
    --max_background_flushes=2 \
    --use_direct_io_for_flush_and_compaction \
    --use_direct_reads"

# -----------------------------------------------------------------------
# Workload 1: fillseq — Sequential bulk load
# -----------------------------------------------------------------------
echo "--- [1/5] fillseq (sequential write) ---"
rm -rf "$DB_DIR"/* "$WAL_DIR"/*
db_bench --benchmarks=fillseq \
    $COMMON_FLAGS \
    2>&1 | tee "$RESULTS_DIR/rocksdb/fillseq.log"
echo ""

# -----------------------------------------------------------------------
# Workload 2: fillrandom — Random write load
# -----------------------------------------------------------------------
echo "--- [2/5] fillrandom (random write) ---"
rm -rf "$DB_DIR"/* "$WAL_DIR"/*
db_bench --benchmarks=fillrandom \
    $COMMON_FLAGS \
    2>&1 | tee "$RESULTS_DIR/rocksdb/fillrandom.log"
echo ""

# -----------------------------------------------------------------------
# Workload 3: readrandom — Point lookups on filled DB
# -----------------------------------------------------------------------
echo "--- [3/5] readrandom (random read) ---"
# First, fill the DB
db_bench --benchmarks=fillseq $COMMON_FLAGS > /dev/null 2>&1
# Then benchmark reads
db_bench --benchmarks=readrandom \
    $COMMON_FLAGS \
    --use_existing_db \
    --duration=$DURATION \
    2>&1 | tee "$RESULTS_DIR/rocksdb/readrandom.log"
echo ""

# -----------------------------------------------------------------------
# Workload 4: readwhilewriting — Mixed read/write (OLTP-style)
# -----------------------------------------------------------------------
echo "--- [4/5] readwhilewriting (mixed OLTP) ---"
db_bench --benchmarks=readwhilewriting \
    $COMMON_FLAGS \
    --use_existing_db \
    --duration=$DURATION \
    2>&1 | tee "$RESULTS_DIR/rocksdb/readwhilewriting.log"
echo ""

# -----------------------------------------------------------------------
# Workload 5: overwrite — Update-heavy workload (WAF stress)
# -----------------------------------------------------------------------
echo "--- [5/5] overwrite (update heavy) ---"
db_bench --benchmarks=overwrite \
    $COMMON_FLAGS \
    --use_existing_db \
    --duration=$DURATION \
    2>&1 | tee "$RESULTS_DIR/rocksdb/overwrite.log"
echo ""

echo "=== RocksDB benchmarks complete ==="
