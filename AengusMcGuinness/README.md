# Evaluating RDMA for Distributed Cache Reads and Metadata Updates

**Author:** Aengus McGuinness

This project implements a small distributed key-value cache prototype to compare TCP/RPC, two-sided RDMA send/receive, one-sided RDMA reads, and one-sided RDMA reads with an RDMA atomic metadata update. The final CloudLab experiments measure throughput, latency, server CPU utilization, and attempted network byte counters across 1, 2, 4, and 8 clients.

## Repository Layout

- `report.pdf`: compiled USENIX-format final report.
- `report/`: LaTeX source, USENIX style file, and report figures.
- `src/`: C++ source code, CMake build file, benchmark drivers, tests, documentation source, and experiment scripts.
- `experiments/`: final raw trial data, aggregated CSVs, and CloudLab metadata used by the report.
- `ai-usage.md`: final AI usage report.

## Build

From this folder:

```bash
cd src
cmake -S . -B build
cmake --build build -j
ctest --test-dir build
```

RDMA binaries are built when `libibverbs` is available.

## Run Locally With TCP

```bash
cd src
./build/kv_server 9090
```

In another terminal:

```bash
cd src
./build/kv_client 127.0.0.1 9090
```

Example commands:

```text
SET foo bar
GET foo
DEL foo
QUIT
```

## Run A TCP Benchmark

Start the TCP server, then run:

```bash
cd src
./build/kv_benchmark   --host 127.0.0.1 --port 9090   --clients 4 --ops 100000 --keys 1024   --value-size 64 --get-ratio 0.95 --zipf-s 0.8   --warmup 5000 --csv experiments/tcp_cloudlab_clients.csv
```

## Run On CloudLab

The final RDMA measurements used the private CloudLab data interface backed by RDMA device `mlx5_3`.

Two-sided RDMA server:

```bash
cd src
./build/kv_server_rdma --mode two-sided --device mlx5_3 --port 9091
```

One-sided RDMA server:

```bash
cd src
./build/kv_server_rdma --mode one-sided --device mlx5_3 --port 9091 --preload 1024
```

Client-side runners:

```bash
cd src
./scripts/run_tcp_experiments.sh --host "$SERVER_IP" --reset
./scripts/run_two_sided_rdma_experiments.sh --host "$SERVER_IP" --device mlx5_3 --reset
./scripts/run_one_sided_rdma_experiments.sh --host "$SERVER_IP" --device mlx5_3 --reset
```

For CPU and network metric collection, see `src/scripts/metrics_collector.py` and the discussion in `report.pdf`. The final network byte-counter data is included for transparency but treated as inconclusive because Linux netdev counters reported physically implausible RDMA bytes/op.

## Regenerate Final Summary Plots

```bash
cd src
python3 scripts/plot_final_trials.py   --trials-dir ../experiments/final_trials_mlx5_3_100k   --summary-dir ../experiments/final_summary_mlx5_3_100k   --plots-dir ../report/plots/final_summary_mlx5_3_100k
```

## Documentation

Source documentation is configured with Doxygen:

```bash
cd src
doxygen Doxyfile
```

The documentation overview is in `src/docs/doxygen_main.md`.
