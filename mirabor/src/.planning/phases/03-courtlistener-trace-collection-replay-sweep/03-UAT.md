---
status: complete
phase: 03-courtlistener-trace-collection-replay-sweep
source: [03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md]
started: 2026-04-19T17:05:00Z
updated: 2026-04-19T17:17:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Makefile Congress back-compat dry-run
expected: `make -n run-sweep` (no env vars) renders the original Congress command (`./cache_sim --alpha-sweep --shards --output-dir results/congress`), with no reference to court trace or --trace flag
result: pass

### 2. Court sweep invocation dry-run
expected: `make -n run-sweep WORKLOAD=court TRACE=traces/court_trace.csv` renders `./cache_sim --trace traces/court_trace.csv --replay-zipf --alpha-sweep --output-dir results/court`
result: pass

### 3. Workload stats JSON sanity check
expected: `cat results/court/workload_stats.json` shows total_requests=20000, unique_objects≈15018, alpha_mle≈1.028 (higher than Congress's 0.797), mean_size≈3144 bytes, median_size=1381, max_size=462490 (the 500KB-class opinion per PITFALLS M5). Confirms the court trace is MORE skewed than Congress (higher α) AND has a much wider size distribution (500× bigger max).
result: pass

### 4. MRC figure renders with W-TinyLFU styling
expected: Open `results/court/figures/mrc.pdf`. Six policy curves plotted (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU). W-TinyLFU renders in brown (#8c564b tab:brown) with filled-plus markers (per Phase 2 styling in scripts/plot_results.py). W-TinyLFU curve visibly lower (= better miss ratio) than LRU across most cache-fraction points. Legend includes all 6 policies.
result: pass

### 5. Alpha-sensitivity figure shows WTLFU dominance
expected: Open `results/court/figures/alpha_sensitivity.pdf`. X-axis = α (0.6 → 1.2), Y-axis = miss ratio. Six curves. W-TinyLFU monotonically below LRU across every α, with the gap widening at higher α (more skew = more signal for W-TinyLFU to exploit). Matches the Phase 2 Congress finding on this second workload.
result: pass

### 6. Collector CLI exposes all flags
expected: `.venv/bin/python3 scripts/collect_court_trace.py --help` prints usage with at least these flags: `--output`, `--report`, `--target-rows`, `--resume`. Each flag should have a short description. Script should exit 0.
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]
