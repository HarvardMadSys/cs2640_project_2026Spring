#!/bin/bash
#
# setup.sh — CloudLab startup script
#
# Installs all dependencies, compiles RocksDB, installs MongoDB, and builds
# CacheLib on the provisioned bare-metal node.  Output is logged to
# /local/logs/setup.log so the user can monitor progress.
#
set -euo pipefail

LOGDIR="/local/logs"
LOGFILE="$LOGDIR/setup.log"
WORKDIR="/local/repository"
BUILDDIR="/local/build"

mkdir -p "$LOGDIR" "$BUILDDIR"

exec > >(tee -a "$LOGFILE") 2>&1
echo "========================================="
echo " CS2640 Setup — $(date)"
echo "========================================="

# -----------------------------------------------------------------------
# 1. System packages
# -----------------------------------------------------------------------
echo "[1/6] Installing system packages ..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential cmake git wget curl \
    libgflags-dev libsnappy-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev \
    libtbb-dev \
    python3 python3-pip python3-venv \
    openjdk-17-jdk maven \
    nvme-cli fio sysstat \
    numactl liburing-dev \
    gnupg lsb-release \
    qemu-system-x86 qemu-utils cloud-image-utils

# -----------------------------------------------------------------------
# 2. Detect NVMe devices
# -----------------------------------------------------------------------
echo "[2/7] Detecting NVMe devices ..."
sudo nvme list

# Find an NVMe device that is NOT the OS disk.
# The OS disk has partitions (p1, p2, etc.); the free disk has none.
TARGET_DEV=""
for dev in $(lsblk -dno NAME,TRAN | grep nvme | awk '{print "/dev/"$1}'); do
    PART_COUNT=$(lsblk -no NAME "$dev" | wc -l)
    if [ "$PART_COUNT" -le 1 ]; then
        # This device has no partitions -- it's the free benchmark disk
        TARGET_DEV="$dev"
        break
    else
        echo "Skipping $dev (has partitions -- likely OS disk)"
    fi
done

if [ -z "$TARGET_DEV" ]; then
    echo "ERROR: No free NVMe device found. All devices have partitions."
    echo "Available devices:"
    lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,TRAN
    exit 1
fi

echo "Primary benchmark target: $TARGET_DEV"

# Format and mount the NVMe for database workloads
echo "Formatting $TARGET_DEV with ext4 ..."
sudo mkfs.ext4 -F "$TARGET_DEV"
sudo mkdir -p /mnt/nvme
sudo mount -o discard,noatime "$TARGET_DEV" /mnt/nvme
sudo chmod 777 /mnt/nvme
echo "$TARGET_DEV mounted at /mnt/nvme"

# -----------------------------------------------------------------------
# 3. Build RocksDB + db_bench
# -----------------------------------------------------------------------
echo "[3/6] Building RocksDB ..."
cd "$BUILDDIR"
if [ ! -d rocksdb ]; then
    git clone --depth 1 --branch v9.11.2 https://github.com/facebook/rocksdb.git
fi
cd rocksdb
mkdir -p build && cd build
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DWITH_SNAPPY=ON -DWITH_LZ4=ON -DWITH_ZSTD=ON -DWITH_BZ2=ON \
    -DWITH_GFLAGS=ON \
    -DWITH_BENCHMARK_TOOLS=ON \
    -DFAIL_ON_WARNINGS=OFF
make -j$(nproc) db_bench
sudo cp db_bench /usr/local/bin/
echo "db_bench installed: $(which db_bench)"

# -----------------------------------------------------------------------
# 4. Install MongoDB
# -----------------------------------------------------------------------
echo "[4/6] Installing MongoDB 7.0 ..."
# Import the MongoDB GPG key and add the repository
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
    sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
    sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt-get update -qq
sudo apt-get install -y -qq mongodb-org
# Don't start it yet — benchmarks will start it with specific dbpath
sudo systemctl disable mongod || true
sudo systemctl stop mongod || true
echo "mongod installed: $(mongod --version | head -1)"

# -----------------------------------------------------------------------
# 5. Build & install YCSB
# -----------------------------------------------------------------------
echo "[5/6] Installing YCSB ..."
cd "$BUILDDIR"
if [ ! -d ycsb ]; then
    curl -fsSL https://github.com/brianfrankcooper/YCSB/releases/download/0.17.0/ycsb-0.17.0.tar.gz \
        | tar xz
    mv ycsb-0.17.0 ycsb
fi
echo "YCSB installed at $BUILDDIR/ycsb"

# -----------------------------------------------------------------------
# 6. Download Ubuntu cloud image for QEMU ZNS/FDP emulation
# -----------------------------------------------------------------------
echo "[6/6] Downloading Ubuntu 22.04 cloud image for QEMU VM ..."
CLOUD_IMG_DIR="/local/build/images"
mkdir -p "$CLOUD_IMG_DIR"
if [ ! -f "$CLOUD_IMG_DIR/jammy-server-cloudimg-amd64.img" ]; then
    wget -q -O "$CLOUD_IMG_DIR/jammy-server-cloudimg-amd64.img" \
        https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
fi
# Create a working copy and resize it
cp "$CLOUD_IMG_DIR/jammy-server-cloudimg-amd64.img" "$CLOUD_IMG_DIR/vm-disk.qcow2"
qemu-img resize "$CLOUD_IMG_DIR/vm-disk.qcow2" 30G
echo "Cloud image ready at $CLOUD_IMG_DIR/vm-disk.qcow2"

# -----------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------
echo ""
echo "========================================="
echo " Setup completed successfully! — $(date)"
echo "========================================="
echo ""
echo "NVMe target device: $TARGET_DEV"
echo "NVMe mount point:   /mnt/nvme"
echo ""
echo "To run all benchmarks (native + emulated ZNS/FDP):"
echo "  cd /local/repository && sudo ./benchmarks/run_all.sh"
echo ""
echo "To run only native NVMe benchmarks:"
echo "  cd /local/repository && sudo ./benchmarks/run_all.sh --native-only"
echo ""
echo "To run only emulated ZNS/FDP benchmarks:"
echo "  cd /local/repository && sudo ./benchmarks/run_all.sh --emulated-only"
echo ""
