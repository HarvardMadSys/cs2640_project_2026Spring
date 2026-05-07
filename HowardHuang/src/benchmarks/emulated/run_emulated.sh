#!/bin/bash
#
# run_emulated.sh — Run all database benchmarks inside QEMU VM against
#                   emulated ZNS, FDP, and conventional NVMe devices.
#
# This script:
#   1. Starts the QEMU VM with KVM (ZNS + FDP + conventional NVMe)
#   2. Installs pre-compiled tools from the host into the VM
#   3. Runs db_bench, YCSB, and cachebench against each NVMe type
#   4. Collects results and NVMe stats
#   5. Shuts down the VM
#
# Usage: ./run_emulated.sh [results_dir]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${1:-/local/repository/results/emulated}"
SSH_PORT=2222

mkdir -p "$RESULTS_DIR"

echo "============================================"
echo " Emulated ZNS/FDP Benchmarks"
echo " $(date)"
echo "============================================"

# -----------------------------------------------------------------------
# 1. Start the QEMU VM
# -----------------------------------------------------------------------
echo "[1/5] Starting QEMU VM ..."
bash "$SCRIPT_DIR/setup_qemu_vm.sh" start

# Helper to run commands in the VM
vm() {
    ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_rsa -p $SSH_PORT bench@localhost "$@"
}

# Wait for VM to be fully ready
echo "Waiting for VM to be fully initialized ..."
for i in $(seq 1 60); do
    if vm "test -f /tmp/vm_ready" 2>/dev/null; then
        break
    fi
    sleep 5
done

# -----------------------------------------------------------------------
# 2. Verify emulated devices
# -----------------------------------------------------------------------
echo "[2/5] Verifying emulated NVMe devices ..."
vm "sudo nvme list" | tee "$RESULTS_DIR/nvme_devices.txt"
vm "lsblk" | tee "$RESULTS_DIR/lsblk.txt"
echo ""

# Map device names (order: ZNS=nvme0, FDP=nvme1, Conventional=nvme2)
ZNS_DEV="/dev/nvme0n1"
FDP_DEV="/dev/nvme1n1"
CONV_DEV="/dev/nvme2n1"

# Verify ZNS zones
echo "Verifying ZNS zones ..."
vm "sudo nvme zns report-zones $ZNS_DEV -d 5" | tee "$RESULTS_DIR/zns_zones.txt" || true

# Note: FDP emulation not available in QEMU 6.2; fdp device runs as plain conventional NVMe
echo "Note: QEMU 6.2 does not support FDP emulation; FDP slot uses conventional NVMe (protocol comparison via workload patterns)"

# -----------------------------------------------------------------------
# 3. Prepare filesystems and install dependencies in the VM
# -----------------------------------------------------------------------
echo "[3/5] Preparing filesystems and dependencies on emulated devices ..."

# Mount host share
vm "sudo mkdir -p /host && sudo mount -t 9p -o trans=virtio hostshare /host 2>/dev/null" || true

# Install runtime dependencies inside the VM (f2fs, python2, java, mongodb)
echo "Installing dependencies inside VM (f2fs, python2, java, mongodb) ..."
vm "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq f2fs-tools python2-minimal openjdk-17-jre-headless gnupg curl"
vm "if ! command -v mongod &>/dev/null; then \
        curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg && \
        echo 'deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse' | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list && \
        sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mongodb-org; \
    fi"


# Unmount if already mounted
vm "sudo umount /mnt/conv 2>/dev/null || true"
vm "sudo umount /mnt/fdp 2>/dev/null || true"
vm "sudo umount /mnt/zns 2>/dev/null || true"

# Create filesystems on conventional and FDP devices
vm "sudo mkfs.ext4 -F $CONV_DEV && sudo mkdir -p /mnt/conv && sudo mount -o discard,noatime $CONV_DEV /mnt/conv && sudo chmod 777 /mnt/conv"
vm "sudo mkfs.ext4 -F $FDP_DEV && sudo mkdir -p /mnt/fdp && sudo mount -o discard,noatime $FDP_DEV /mnt/fdp && sudo chmod 777 /mnt/fdp"

# ZNS needs a zone-aware filesystem — f2fs with -m (zoned) flag
vm "sudo mkfs.f2fs -f -m $ZNS_DEV 2>/dev/null && sudo mkdir -p /mnt/zns && sudo mount -o discard,noatime,background_gc=off $ZNS_DEV /mnt/zns && sudo chmod 777 /mnt/zns" || {
    echo "NOTE: f2fs zoned mount failed for ZNS. ZNS db_bench will be skipped; fio runs directly on raw device."
    vm "sudo mkdir -p /mnt/zns && sudo chmod 777 /mnt/zns" || true
}

# -----------------------------------------------------------------------
# 4. Run benchmarks
# -----------------------------------------------------------------------
echo "[4/5] Running benchmarks ..."

# --- 4a. fio direct I/O benchmarks for protocol comparison ---
echo ""
echo "=== fio protocol-level benchmarks ==="

# ZNS: Sequential write (zone append) — use libaio for QEMU 6.2 compatibility
echo "--- fio: ZNS Sequential Write ---"
vm "sudo fio --name=zns_seqwrite \
    --filename=$ZNS_DEV --direct=1 --ioengine=libaio \
    --rw=write --bs=128k \
    --iodepth=32 --numjobs=1 --size=4G \
    --output-format=json" 2>&1 | tee "$RESULTS_DIR/fio_zns_seqwrite.json"

# FDP slot: Random write (plain NVMe on QEMU 6.2 — same ioengine as conventional)
echo "--- fio: FDP-slot Random Write (conventional NVMe, QEMU 6.2) ---"
vm "sudo fio --name=fdp_randwrite \
    --filename=$FDP_DEV --direct=1 --ioengine=libaio \
    --rw=randwrite --bs=4k \
    --iodepth=32 --numjobs=4 --size=4G \
    --output-format=json" 2>&1 | tee "$RESULTS_DIR/fio_fdp_randwrite.json"

# Conventional: Random write (baseline)
echo "--- fio: Conventional Random Write ---"
vm "sudo fio --name=conv_randwrite \
    --filename=$CONV_DEV --direct=1 --ioengine=libaio \
    --rw=randwrite --bs=4k \
    --iodepth=32 --numjobs=4 --size=4G \
    --output-format=json" 2>&1 | tee "$RESULTS_DIR/fio_conv_randwrite.json"

# --- 4b. RocksDB db_bench (if available in VM) ---
echo ""
echo "=== RocksDB db_bench on emulated devices ==="

# Copy db_bench from host if built
vm "test -f /usr/local/bin/db_bench" 2>/dev/null || {
    vm "sudo cp /host/benchmarks/emulated/../../build/rocksdb/build/db_bench /usr/local/bin/ 2>/dev/null" || {
        echo "NOTE: db_bench not available in VM. Install manually or use host-built binary."
    }
}

for DEV_LABEL in conv fdp zns; do
    MNT="/mnt/$DEV_LABEL"
    echo "--- db_bench on $DEV_LABEL ($MNT) ---"
    vm "rm -rf $MNT/rocksdb_bench $MNT/rocksdb_wal; mkdir -p $MNT/rocksdb_bench $MNT/rocksdb_wal" || continue

    for WORKLOAD in fillseq fillrandom readrandom overwrite; do
        echo "  > $WORKLOAD on $DEV_LABEL"
        USE_EXISTING=""
        if [ "$WORKLOAD" != "fillseq" ] && [ "$WORKLOAD" != "fillrandom" ]; then
            USE_EXISTING="--use_existing_db"
        fi
        vm "db_bench --benchmarks=$WORKLOAD \
            --db=$MNT/rocksdb_bench --wal_dir=$MNT/rocksdb_wal \
            --num=1000000 --value_size=1024 --threads=4 \
            --compression_type=snappy \
            --write_buffer_size=67108864 \
            --use_direct_io_for_flush_and_compaction \
            --use_direct_reads \
            $USE_EXISTING" \
            2>&1 | tee "$RESULTS_DIR/rocksdb_${DEV_LABEL}_${WORKLOAD}.log" || true
    done
done

# --- 4c. MongoDB YCSB on emulated devices ---
echo ""
echo "=== MongoDB YCSB on emulated devices ==="

# Define YCSB paths in the VM (mounted from host)
YCSB_VM_DIR="/host/build/ycsb"
MONGO_PORT=27099

for DEV_LABEL in conv fdp zns; do
    MNT="/mnt/$DEV_LABEL"
    echo "--- MongoDB on $DEV_LABEL ($MNT) ---"
    
    # Setup data dir and start mongod
    vm "sudo rm -rf $MNT/mongodb_data; sudo mkdir -p $MNT/mongodb_data && sudo chown -R mongodb:mongodb $MNT/mongodb_data"
    vm "sudo -u mongodb mongod --dbpath $MNT/mongodb_data --port $MONGO_PORT --bind_ip 127.0.0.1 --wiredTigerCacheSizeGB 4 --wiredTigerCollectionBlockCompressor snappy --fork --logpath $MNT/mongod.log"
    
    # Wait for mongod to be ready
    vm "for i in {1..30}; do if mongosh --port $MONGO_PORT --eval 'db.runCommand({ping:1})' > /dev/null 2>&1; then break; fi; sleep 1; done"
    
    for WORKLOAD in workloada workloadb workloadc workloadf; do
        for PHASE in load run; do
            # F runs after A, B, C usually don't need reload but we'll do standard reload for consistency or just run
            if [[ "$PHASE" == "load" && "$WORKLOAD" != "workloada" ]]; then
                # Only load on workloada, others just run to save time, unless we drop DB
                # Actually we will drop and load for all to be consistent
                vm "mongosh --port $MONGO_PORT --eval \"db.getSiblingDB('ycsb').dropDatabase()\" > /dev/null 2>&1"
            elif [[ "$PHASE" == "load" ]]; then
                vm "mongosh --port $MONGO_PORT --eval \"db.getSiblingDB('ycsb').dropDatabase()\" > /dev/null 2>&1"
            fi
            
            echo "  > YCSB $WORKLOAD ($PHASE) on $DEV_LABEL"
            vm "python2 $YCSB_VM_DIR/bin/ycsb $PHASE mongodb -s \
                -P $YCSB_VM_DIR/workloads/$WORKLOAD \
                -p recordcount=1000000 -p operationcount=500000 \
                -p mongodb.url=\"mongodb://127.0.0.1:$MONGO_PORT/ycsb\" \
                -threads 8" 2>&1 | tee "$RESULTS_DIR/mongodb_${DEV_LABEL}_${WORKLOAD}_${PHASE}.log" || true
        done
    done
    
    # Shutdown mongod
    vm "mongosh --port $MONGO_PORT --eval 'db.adminCommand({shutdown: 1})' > /dev/null 2>&1 || true"
    vm "sleep 3"
done

# --- 4c. Capture final NVMe stats ---
echo ""
echo "=== Final NVMe Statistics ==="
echo "--- ZNS Zone Report ---"
vm "sudo nvme zns report-zones $ZNS_DEV" | tee "$RESULTS_DIR/zns_zones_final.txt" || true

echo "--- NVMe device SMART stats (all devices) ---"
for DEV in $ZNS_DEV $FDP_DEV $CONV_DEV; do
    vm "sudo nvme smart-log $DEV" | tee "$RESULTS_DIR/nvme_smart_${DEV//\//_}.txt" || true
done

# -----------------------------------------------------------------------
# 5. Collect results and shutdown
# -----------------------------------------------------------------------
echo "[5/5] Collecting results ..."

# Copy any results from VM to host
vm "ls -la /mnt/conv/ /mnt/fdp/" | tee "$RESULTS_DIR/device_usage.txt" || true

echo ""
echo "============================================"
echo " Emulated Benchmarks Complete — $(date)"
echo "============================================"
echo "Results saved to: $RESULTS_DIR"
ls -la "$RESULTS_DIR"

# Don't auto-shutdown VM — user may want to inspect it
echo ""
echo "VM is still running. To stop it:"
echo "  sudo $SCRIPT_DIR/setup_qemu_vm.sh stop"
echo "To SSH into the VM:"
echo "  ssh -p $SSH_PORT bench@localhost"
