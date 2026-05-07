# kvcache-sim

A lightweight Python simulator for KV-cache behavior under LLM-like inference workloads.

This repo includes:
- 75% baseline: fixed-capacity cache, LRU/FIFO, synthetic workloads, plots.
- 100% extension: MOONCAKE-inspired shared global cache across concurrent requests.
- 125% extension: adaptive cost-aware eviction under workload shifts and memory pressure.

## Features

- Block-level cache model (capacity measured in number of token blocks)
- Eviction policies:
  - FIFO
  - LRU
  - Adaptive (shared-global mode only)
- Workload families:
  - short prompt (high locality)
  - multi-turn conversation (system/prefix reuse + recent-turn reuse)
  - long context (large working set, weaker locality)
  - concurrent shared-prefix requests (global-cache experiments)
  - shifted concurrent workloads (adaptive experiments, phase1 -> phase2)
- Metrics:
  - total accesses, hits, misses
  - hit rate, miss rate
  - recomputation cost
  - workload summaries (unique blocks, reuse ratio, etc.)
- Reproducible multi-seed experiments (default: 15 seeds)

## Project Structure

```text
kvcache-sim/
  main.py
  requirements.txt
  README.md
  simulator/
    __init__.py
    cache.py
    policies.py
    workload.py
    runner.py
    global_workload.py
    global_cache.py
  experiments/
    exp_policy_compare.py
    exp_cache_size_sweep.py
    exp_shared_global_cache.py
    exp_shared_global_sensitivity.py
    exp_adaptive_controller.py
    exp_table_figures.py
    exp_table_100_percent_summary.py
  tests/
    test_cache.py
    test_policies.py
    test_workloads.py
    test_runner.py
    test_global_workload.py
    test_global_cache.py
  results/
    data/
    figures/
```

## Setup

```bash
cd kvcache-sim
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run Tests

Run from the `kvcache-sim/` directory:

```bash
python3 -m pytest -q
```

If you run from the parent folder, use:

```bash
python3 -m pytest -q kvcache-sim/tests
```

## Run Experiments

From `kvcache-sim/`:

75% baseline:
```bash
python3 experiments/exp_policy_compare.py
python3 experiments/exp_cache_size_sweep.py
```

100% shared global cache:
```bash
python3 experiments/exp_shared_global_cache.py
python3 experiments/exp_shared_global_sensitivity.py
```

125% adaptive controller:
```bash
python3 experiments/exp_adaptive_controller.py
```

Optional table figures for slides:
```bash
python3 experiments/exp_table_figures.py
python3 experiments/exp_table_100_percent_summary.py
```

## Reproducibility

- Workload generators are deterministic for a fixed seed.
- Experiment scripts use fixed seed ranges (default 15 seeds) and report mean/std.
- Running the same script with unchanged code/parameters produces identical CSVs/plots.
- Seed ranges are set in each experiment script (for example, `range(2026, 2026 + 15)`).

## Outputs

All outputs are saved automatically:
- CSVs: `results/data/`
- Figures: `results/figures/`

Typical files:
- 75%: `policy_compare*.csv`, `cache_size_sweep*.csv`
- 100%: `shared_global_compare*.csv`, `shared_global_sensitivity*.csv`
- 125%: `adaptive_controller*.csv`
- Workload summaries: `*_workload_summary*.csv`
- Slide tables: `table_*.png`

## Notes

- This is a simulator only (no real LLM inference).
- Cache entries are modeled at token-block granularity, not tensor/head level.
- Baseline recomputation cost is 1 per miss; shared-global experiments can use block-dependent costs.
