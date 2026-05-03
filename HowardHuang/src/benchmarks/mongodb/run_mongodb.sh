#!/bin/bash
#
# run_mongodb.sh — MongoDB + YCSB workloads on NVMe
#
# Usage: ./run_mongodb.sh <nvme_mount> <results_dir>
#
set -euo pipefail

NVME_MOUNT="${1:-/mnt/nvme}"
RESULTS_DIR="${2:-/local/repository/results}"
DB_DIR="$NVME_MOUNT/mongodb_data"
YCSB_DIR="/local/build/ycsb"
MONGO_PORT=27099
RECORD_COUNT=1000000   # 1M records
OP_COUNT=500000        # 500K operations per workload
THREADS=8

mkdir -p "$RESULTS_DIR/mongodb" "$DB_DIR"

echo "=== MongoDB + YCSB Benchmarks ==="
echo "Data dir: $DB_DIR"
echo "Records: $RECORD_COUNT, Operations: $OP_COUNT"
echo "Threads: $THREADS"
echo ""

# Raise file descriptor limit (mongod recommends >= 64000)
ulimit -n 65536 2>/dev/null || ulimit -n 8192 2>/dev/null || true

# Kill any stale mongod instances and clean up
echo "Cleaning up any stale mongod ..."
pkill -x mongod 2>/dev/null || true; sleep 2

# Start a fresh mongod instance pointing at the NVMe
echo "Starting mongod on port $MONGO_PORT ..."
rm -rf "$DB_DIR"/*
mkdir -p "$DB_DIR"
mongod --dbpath "$DB_DIR" \
    --port $MONGO_PORT \
    --bind_ip 127.0.0.1 \
    --wiredTigerCacheSizeGB 4 \
    --wiredTigerCollectionBlockCompressor snappy \
    --logpath "$RESULTS_DIR/mongodb/mongod.log" \
    --fork

# Wait for mongod to accept connections
for i in $(seq 1 30); do
    if mongosh --port $MONGO_PORT --eval "db.runCommand({ping:1})" > /dev/null 2>&1; then
        echo "mongod is ready."
        break
    fi
    sleep 1
done

# YCSB runner helper
run_ycsb() {
    local workload=$1
    local phase=$2    # load or run
    local label=$3

    echo "--- $label ---"
    python2 "$YCSB_DIR/bin/ycsb" "$phase" mongodb \
        -s \
        -P "$YCSB_DIR/workloads/$workload" \
        -p recordcount=$RECORD_COUNT \
        -p operationcount=$OP_COUNT \
        -p mongodb.url="mongodb://127.0.0.1:$MONGO_PORT/ycsb" \
        -threads $THREADS \
        2>&1 | tee "$RESULTS_DIR/mongodb/${label}.log"
    echo ""
}

# -----------------------------------------------------------------------
# Workload A: 50% Read / 50% Update (Update Heavy)
# -----------------------------------------------------------------------
echo "=== Workload A: Update Heavy (50/50 R/W) ==="
run_ycsb workloada load  "workloada_load"
run_ycsb workloada run   "workloada_run"

# Drop and recreate for clean state
mongosh --port $MONGO_PORT --eval "db.getSiblingDB('ycsb').dropDatabase()" > /dev/null 2>&1

# -----------------------------------------------------------------------
# Workload B: 95% Read / 5% Update (Read Mostly)
# -----------------------------------------------------------------------
echo "=== Workload B: Read Mostly (95/5 R/W) ==="
run_ycsb workloadb load  "workloadb_load"
run_ycsb workloadb run   "workloadb_run"

mongosh --port $MONGO_PORT --eval "db.getSiblingDB('ycsb').dropDatabase()" > /dev/null 2>&1

# -----------------------------------------------------------------------
# Workload C: 100% Read (Read Only)
# -----------------------------------------------------------------------
echo "=== Workload C: Read Only (100% Read) ==="
run_ycsb workloadc load  "workloadc_load"
run_ycsb workloadc run   "workloadc_run"

mongosh --port $MONGO_PORT --eval "db.getSiblingDB('ycsb').dropDatabase()" > /dev/null 2>&1

# -----------------------------------------------------------------------
# Workload F: 50% Read / 50% Read-Modify-Write
# -----------------------------------------------------------------------
echo "=== Workload F: Read-Modify-Write ==="
run_ycsb workloadf load  "workloadf_load"
run_ycsb workloadf run   "workloadf_run"

# -----------------------------------------------------------------------
# Capture WiredTiger stats
# -----------------------------------------------------------------------
echo "--- WiredTiger Server Status ---"
mongosh --port $MONGO_PORT --eval \
    "JSON.stringify(db.serverStatus().wiredTiger, null, 2)" \
    2>/dev/null | tee "$RESULTS_DIR/mongodb/wiredtiger_stats.json" || true

# Shutdown mongod
echo "Shutting down mongod ..."
mongosh --port $MONGO_PORT --eval "db.adminCommand({shutdown: 1})" 2>/dev/null || true
sleep 3

echo "=== MongoDB benchmarks complete ==="
