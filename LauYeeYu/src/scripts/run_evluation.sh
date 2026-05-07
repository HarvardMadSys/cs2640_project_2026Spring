#!/bin/bash

set -e

cd "$(dirname "$0")/.."

mkdir -p logs
make

for trace_file in ../datasets/qwen-bailian-usagetraces-anon/qwen_traceA_blksz_16.jsonl ../datasets/qwen-bailian-usagetraces-anon/qwen_traceB_blksz_16.jsonl; do
  for cache_size in 10k 100k 1M 100M; do
    log_file="logs/$(basename "$trace_file" .jsonl)_cache_${cache_size}.log"
    echo "Running evaluation on $trace_file with cache size $cache_size, logging to $log_file"
    build/evaluate --trace-file "$trace_file" --cache-size "$cache_size" > "$log_file"
  done
done
