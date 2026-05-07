# SSTable Striping and Proactive Reclaim for ZNS-Aware RocksDB

This repository evaluates two ZNS-aware RocksDB optimizations on an emulated ZNS device:

1. **SSTable striping**: split one logical SSTable into fragments across multiple ZNS zones.
2. **Proactive reclaim**: reclaim invalidated zones before foreground writes run out of free zones.

---

## 1. Testbed assumptions

This README assumes the following setup:

- Linux machine with zoned block device support.
- RocksDB fork or build with the ZenFS plugin enabled.
- A 128 GiB emulated ZNS device backed by a regular SSD.
- 72 MiB emulated zones.
- `db_bench` and the `zenfs` utility built from the same RocksDB tree.
- Custom project changes for:
  - SSTable striping.
  - Proactive GC / reclaim.

The examples below use these variables:

```bash
export ROCKSDB_DIR=$HOME/rocksdb
export ZBD_SHORT=nvme0n1        # short name only, not /dev/nvme0n1
export ZBD=/dev/$ZBD_SHORT
export AUX_PATH=/tmp/zenfs-aux
export DB_PATH=rocksdbtest/dbbench
export RESULTS_DIR=$HOME/zns-rocksdb-results
mkdir -p "$RESULTS_DIR"
```

Replace `nvme0n1` with the device name of your emulated ZNS device.

---

## 2. Install dependencies

Typical dependencies:

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake git pkg-config \
  libsnappy-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev \
  libgflags-dev libnuma-dev libaio-dev \
  liburing-dev libzbd-dev \
  util-linux
```

Useful debugging tools:

```bash
sudo apt-get install -y nvme-cli fio
```

---

## 3. Build RocksDB, db_bench, and ZenFS

From the RocksDB source tree:

```bash
cd "$ROCKSDB_DIR"

# Build db_bench.
make -j"$(nproc)" db_bench

# Build the ZenFS command-line utility.
# The exact target may differ by RocksDB/ZenFS version.
make -j"$(nproc)" plugin/zenfs/util/zenfs
```

Expected binaries:

```bash
test -x ./db_bench && echo "db_bench found"
test -x ./plugin/zenfs/util/zenfs && echo "zenfs utility found"
```

---

## 4. Verify the emulated ZNS device

Check that the kernel sees the device as zoned:

```bash
lsblk -o NAME,SIZE,MODEL,ZONED "$ZBD"
```

Expected: the `ZONED` column should indicate a zoned device, for example `host-managed` or equivalent.

Inspect zones:

```bash
sudo blkzone report "$ZBD" | head -40
```

Optional NVMe check:

```bash
sudo nvme zns report-zones "$ZBD" | head -40
```

For this project, the expected high-level configuration is:

```text
Device capacity: 128 GiB
Zone size:       72 MiB
```

---

## 5. Format the device with ZenFS

`zenfs mkfs` creates the ZenFS metadata and superblock on the zoned device. The auxiliary path stores host-side files such as logs and locks.

```bash
cd "$ROCKSDB_DIR"

sudo rm -rf "$AUX_PATH"
sudo mkdir -p "$AUX_PATH"
sudo chown "$USER":"$USER" "$AUX_PATH"

sudo ./plugin/zenfs/util/zenfs mkfs \
  --zbd="$ZBD_SHORT" \
  --aux_path="$AUX_PATH" \
  --force
```

A successful run should print a message indicating that the ZenFS file system was created and should report free space.

---

## 6. Common db_bench parameters

The project uses a `fillrandom` workload with approximately 20 GiB of random key-value pairs.

```bash
export NUM_KEYS=26000000       # about 20 GiB for 20 B keys + 800 B values
export KEY_SIZE=20
export VALUE_SIZE=800
export MEMTABLE_SIZE=$((64 * 1024 * 1024))
export THREADS=4
export BG_JOBS=4
```

Common options:

```bash
export COMMON_DB_BENCH_OPTS="
  --fs_uri=zenfs://dev:$ZBD_SHORT
  --db=$DB_PATH
  --benchmarks=fillrandom
  --num=$NUM_KEYS
  --key_size=$KEY_SIZE
  --value_size=$VALUE_SIZE
  --write_buffer_size=$MEMTABLE_SIZE
  --compression_type=none
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --max_background_jobs=$BG_JOBS
  --threads=$THREADS
  --statistics=true
  --histogram=true
  --stats_interval_seconds=5
  --report_interval_seconds=5
"
```

---

## 7. Run baseline db_bench

Reformat ZenFS before each clean baseline and experiment run:

```bash
cd "$ROCKSDB_DIR"

sudo ./plugin/zenfs/util/zenfs mkfs \
  --zbd="$ZBD_SHORT" \
  --aux_path="$AUX_PATH" \
  --force
```

Run example:

```bash
./db_bench $COMMON_DB_BENCH_OPTS \
  --use_existing_db=false \
  2>&1 | tee "$RESULTS_DIR/fillrandom_baseline.log"
```

A successful run should end with a `fillrandom` result line showing throughput, micros/op, and/or MB/s. You should also see periodic statistics every 5 seconds.

Check that files were created inside ZenFS:

```bash
sudo ./plugin/zenfs/util/zenfs list \
  --zbd="$ZBD_SHORT" \
  --path="$DB_PATH" | head -40
```

You should see RocksDB files such as `OPTIONS-*`, `MANIFEST-*`, `.log`, and `.sst` files.
