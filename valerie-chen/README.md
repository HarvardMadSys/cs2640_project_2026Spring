# iOS Storage Benchmarking

An iOS app that measures the cost of persistence guarantees across three common storage strategies: **Core Data**, an **append-only log**, and **per-event file writes**. Each backend runs under default or aggressive flushing (`F_FULLFSYNC` / explicit `save()`) over small-write (100 byte – 1 KB) and large-write (1 – 50 MB) workloads. Reports per-write latency (p50/p95/p99) and throughput.

## Requirements
- macOS for host system
- Xcode (to build and install)

## Build and Run

1. Download Xcode
2. `open StorageBenchmark.xcodeproj`
3. Connect iOS device to host via cable (or select macOS as run destination)
4. cmd-R to run
5. Run trials inside iOS app