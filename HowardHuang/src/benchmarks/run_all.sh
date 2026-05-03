#!/bin/bash
#
# run_all.sh — Master benchmark orchestrator
#
# Runs benchmarks in two modes:
#   1. NATIVE — RocksDB, MongoDB, CacheLib directly on the physical NVMe
#   2. EMULATED — Same workloads inside a QEMU/KVM VM against emulated
#      ZNS, FDP, and conventional NVMe devices
#
# Usage:
#   ./run_all.sh                  # Run everything (native + emulated)
#   ./run_all.sh --native-only    # Only native benchmarks
#   ./run_all.sh --emulated-only  # Only emulated ZNS/FDP benchmarks
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="/local/repository/results"
NVME_MOUNT="/mnt/nvme"

RUN_NATIVE=true
RUN_EMULATED=true

case "${1:-}" in
    --native-only)  RUN_EMULATED=false ;;
    --emulated-only) RUN_NATIVE=false ;;
esac

mkdir -p "$RESULTS_DIR"

echo "============================================"
echo " CS2640 Final — Full Benchmark Suite"
echo " $(date)"
echo " Native: $RUN_NATIVE | Emulated: $RUN_EMULATED"
echo "============================================"
echo ""

# Capture system info
echo "--- System Information ---"
uname -a | tee "$RESULTS_DIR/system_info.txt"
lscpu | tee -a "$RESULTS_DIR/system_info.txt"
free -h | tee -a "$RESULTS_DIR/system_info.txt"
echo "" >> "$RESULTS_DIR/system_info.txt"
echo "--- NVMe Devices ---" >> "$RESULTS_DIR/system_info.txt"
sudo nvme list | tee -a "$RESULTS_DIR/system_info.txt"
echo "" >> "$RESULTS_DIR/system_info.txt"
sudo nvme smart-log /dev/nvme0n1 2>/dev/null | tee "$RESULTS_DIR/nvme_smart_pre.txt" || true
echo ""

# Drop caches helper
drop_caches() {
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null
    sleep 2
}

# =====================================================================
# PHASE 1: NATIVE BENCHMARKS (directly on physical NVMe)
# =====================================================================
if [ "$RUN_NATIVE" = true ]; then
    echo "##############################"
    echo "# PHASE 1: NATIVE BENCHMARKS #"
    echo "##############################"
    echo ""

    # -------------------------------------------------------------------
    # 1. RocksDB Benchmarks
    # -------------------------------------------------------------------
    echo "=============================="
    echo " [1/3] RocksDB (native NVMe)"
    echo "=============================="
    drop_caches
    bash "$SCRIPT_DIR/rocksdb/run_rocksdb.sh" "$NVME_MOUNT" "$RESULTS_DIR" 2>&1 | \
        tee "$RESULTS_DIR/rocksdb_full.log"
    echo ""

    # -------------------------------------------------------------------
    # 2. MongoDB Benchmarks
    # -------------------------------------------------------------------
    echo "=============================="
    echo " [2/3] MongoDB (native NVMe)"
    echo "=============================="
    drop_caches
    bash "$SCRIPT_DIR/mongodb/run_mongodb.sh" "$NVME_MOUNT" "$RESULTS_DIR" 2>&1 | \
        tee "$RESULTS_DIR/mongodb_full.log"
    echo ""



    echo "--- Native benchmarks complete ---"
    echo ""
fi

# =====================================================================
# PHASE 2: EMULATED ZNS/FDP BENCHMARKS (inside QEMU/KVM VM)
# =====================================================================
if [ "$RUN_EMULATED" = true ]; then
    echo "##################################"
    echo "# PHASE 2: EMULATED ZNS/FDP     #"
    echo "##################################"
    echo ""

    bash "$SCRIPT_DIR/emulated/run_emulated.sh" "$RESULTS_DIR/emulated" 2>&1 | \
        tee "$RESULTS_DIR/emulated_full.log"
    echo ""

    echo "--- Emulated benchmarks complete ---"
    echo ""
fi

# =====================================================================
# Summary
# =====================================================================
echo "============================================"
echo " All Benchmarks Completed — $(date)"
echo "============================================"
echo ""
echo "Results saved to: $RESULTS_DIR"
ls -la "$RESULTS_DIR"
echo ""
sudo nvme smart-log /dev/nvme0n1 2>/dev/null | tee "$RESULTS_DIR/nvme_smart_post.txt" || true
