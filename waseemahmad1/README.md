
# Workload-Aware KV-Cache Management for LLM Inference

**Author:** Waseem Ahmad

This project presents a lightweight, trace-driven Python simulator for studying KV-cache behavior under LLM-like inference workloads. The simulator is block-level, reproducible, and designed to evaluate how workload structure and cache design affect hit rate and recomputation cost.

## Milestone Scope

- **75% baseline**
  - Fixed-capacity cache simulator
  - FIFO and LRU eviction
  - Synthetic LLM-like workloads
  - Metrics, tests, and visualizations

- **100% extension**
  - MOONCAKE-inspired shared-global cache
  - Concurrent multi-request traces
  - Cross-request prefix reuse analysis
  - Sensitivity sweeps (concurrency and shared-prefix intensity)

- **125% extension**
  - Adaptive cost-aware eviction in shared-global mode
  - Workload-shifted two-phase evaluation
  - Robustness analysis under changing memory pressure

## Key Features

- **Block-level KV-cache model** (capacity measured in number of token blocks)
- **Eviction policies**
  - FIFO
  - LRU
  - Adaptive (shared-global experiments)
- **Workload families**
  - Short prompt (high locality)
  - Multi-turn conversation (prefix/system reuse + recent-turn locality)
  - Long context (large working set, weaker locality)
  - Concurrent shared-prefix workloads
  - Shifted concurrent workloads (phase1 -> phase2)
- **Metrics**
  - Total accesses, hits, misses
  - Hit rate and miss rate
  - Recomputation cost
  - Workload summary statistics (unique blocks, reuse ratio, etc.)
- **Reproducibility**
  - Deterministic workload generation with fixed seeds
  - Multi-seed aggregation (default 15 seeds in experiment scripts)

## Repository Layout (inside this course submission)

Project code is included under:

- `waseemahmad1/src/kvcache-sim/`

Main structure:

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

## Setup Instructions

From the course repo root:

```bash
cd waseemahmad1/src/kvcache-sim
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Build / Run Instructions

### Run tests

```bash
python3 -m pytest -q
```

### Run baseline (75%) experiments

```bash
python3 experiments/exp_policy_compare.py
python3 experiments/exp_cache_size_sweep.py
```

### Run shared-global (100%) experiments

```bash
python3 experiments/exp_shared_global_cache.py
python3 experiments/exp_shared_global_sensitivity.py
```

### Run adaptive (125%) experiments

```bash
python3 experiments/exp_adaptive_controller.py
```

### Optional: generate table figures for presentation/reporting

```bash
python3 experiments/exp_table_figures.py
python3 experiments/exp_table_100_percent_summary.py
```

## Reproducibility Notes

- Workload generators are deterministic for a fixed seed.
- Experiment scripts use fixed seed ranges (default: 15 seeds).
- Running the same script with unchanged code and parameters produces identical outputs.

## Outputs

Outputs are saved automatically:

- CSV files: `results/data/`
- Figures: `results/figures/`

Typical files include:

- **75%**: `policy_compare*.csv`, `cache_size_sweep*.csv`
- **100%**: `shared_global_compare*.csv`, `shared_global_sensitivity*.csv`
- **125%**: `adaptive_controller*.csv`
- Workload summaries: `*_workload_summary*.csv`
- Table figures: `table_*.png`

## Course Submission Artifacts

- Final report PDF: `waseemahmad1/report.pdf`
- Report source folder / source note: `waseemahmad1/report/`
- AI usage report: `waseemahmad1/ai-usage.md`

## Source Repository

Development repository: [waseemahmad1/llm-kvcache-sim](https://github.com/waseemahmad1/llm-kvcache-sim)

## Notes

- This is a simulator only; it does **not** run real LLM inference.
- Cache entries are modeled at token-block granularity (not tensor/head-level).
- Baseline recomputation cost is unit-cost per miss; shared-global/adaptive runs can use block-dependent recomputation costs.
