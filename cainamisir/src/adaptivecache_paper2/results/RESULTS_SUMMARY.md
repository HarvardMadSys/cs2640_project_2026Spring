# Experiment Results Summary

Generated: 2026-04-24

## SWE-bench Policy Comparison Experiments

These experiments run SWE-bench instances under different context management policies
using Qwen models served via SGLang on Modal.

### Policy: `none`

| File | Budget | Model | Instances | Resolved | Rate | Time (s) |
|------|--------|-------|-----------|----------|------|----------|
| `experiment_none_24000_1775698585.json` | 24000 | openai/Qwen/Qwen2.5-7B-Instruct | 5 | 0 | 0.0% | 272 |
| `experiment_none_24000_1775700053.json` | 24000 | openai/Qwen/Qwen2.5-7B-Instruct | 5 | 0 | 0.0% | 1260 |
| `experiment_none_32000_1775700751.json` | 32000 | openai/Qwen/Qwen2.5-7B-Instruct | 5 | 0 | 0.0% | 595 |
| `experiment_none_32000_1775702826.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 264 |
| `experiment_none_32000_1775703003.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 170 |
| `experiment_none_32768_1775693544.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 28 |
| `experiment_none_32768_1775693573.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 6 |
| `experiment_none_32768_1775693654.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 24 |
| `experiment_none_32768_1775693687.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 6 |
| `experiment_none_32768_1775694875.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 1105 |
| `experiment_none_32768_1775695013.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 281 |
| `experiment_none_32768_1775695728.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 275 |
| `experiment_none_32768_1775696069.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 1 | 0 | 0.0% | 97 |
| `experiment_none_32768_1775696905.json` | 32768 | openai/Qwen/Qwen2.5-7B-Instruct | 5 | 0 | 0.0% | 667 |
| `experiment_none_56000_1775701538.json` | 56000 | openai/Qwen/Qwen3.5-35B-A3B | 5 | 0 | 0.0% | 87 |
| `experiment_none_56000_1775701847.json` | 56000 | openai/Qwen/Qwen3.5-35B-A3B | 5 | 0 | 0.0% | 303 |

### Policy: `fifo`

| File | Budget | Model | Instances | Resolved | Rate | Time (s) |
|------|--------|-------|-----------|----------|------|----------|
| `experiment_fifo_32000_1775702829.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 268 |
| `experiment_fifo_32000_1775702999.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 166 |

### Policy: `adaptive`

| File | Budget | Model | Instances | Resolved | Rate | Time (s) |
|------|--------|-------|-----------|----------|------|----------|
| `experiment_adaptive_10000_1775708275.json` | 10000 | openai/Qwen/Qwen3-30B-A3B | 5 | 0 | 0.0% | 446 |
| `experiment_adaptive_32000_1775702826.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 264 |
| `experiment_adaptive_32000_1775703001.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 168 |

### Policy: `kv_adaptive`

| File | Budget | Model | Instances | Resolved | Rate | Time (s) |
|------|--------|-------|-----------|----------|------|----------|
| `experiment_kv_adaptive_32000_1775702822.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 261 |
| `experiment_kv_adaptive_32000_1775702999.json` | 32000 | openai/Qwen/Qwen3-8B | 5 | 0 | 0.0% | 166 |

### Notable Cache Traces

- **experiment_adaptive_10000_1775708275.json** / `astropy__astropy-14182`: 6 steps, 19,018 prompt tokens, mean hit rate 0.0%
- **experiment_adaptive_10000_1775708275.json** / `astropy__astropy-14365`: 21 steps, 108,516 prompt tokens, mean hit rate 0.0%
- **experiment_adaptive_10000_1775708275.json** / `astropy__astropy-14995`: 19 steps, 163,745 prompt tokens, mean hit rate 0.0%
- **experiment_adaptive_10000_1775708275.json** / `astropy__astropy-6938`: 12 steps, 63,853 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775698585.json** / `astropy__astropy-12907`: 25 steps, 239,410 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775700053.json** / `astropy__astropy-12907`: 39 steps, 662,157 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775700053.json** / `astropy__astropy-14182`: 97 steps, 1,740,684 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775700053.json** / `astropy__astropy-14365`: 25 steps, 195,476 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775700053.json** / `astropy__astropy-14995`: 20 steps, 111,498 prompt tokens, mean hit rate 0.0%
- **experiment_none_24000_1775700053.json** / `astropy__astropy-6938`: 40 steps, 658,932 prompt tokens, mean hit rate 0.0%
- **experiment_none_32768_1775696905.json** / `astropy__astropy-12907`: 78 steps, 1,415,005 prompt tokens, mean hit rate 0.0%

## Modal Synthetic Experiments (LMCache integration)

Head-to-head policy comparisons using synthetic multi-turn conversations
with LMCache KV-cache tracking.

### `modal_experiment_1775523580.json`
- Policies: none, fifo, kv_adaptive
- Budget: 4096 tokens, Steps: 10

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 25,025 | 0 | 0.0% |
| fifo | 23,613 | 0 | 0.0% |
| kv_adaptive | 24,411 | 0 | 0.0% |

### `modal_experiment_1775523884.json`
- Policies: none, fifo, kv_adaptive
- Budget: 4096 tokens, Steps: 10

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 5,891 | 0 | 0.0% |
| fifo | 5,891 | 0 | 0.0% |
| kv_adaptive | 5,891 | 0 | 0.0% |

### `modal_experiment_1775524112.json`
- Policies: none, fifo, kv_adaptive
- Budget: 400 tokens, Steps: 20

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 20,952 | 0 | 0.0% |
| fifo | 7,962 | 0 | 0.0% |
| kv_adaptive | 7,962 | 0 | 0.0% |

### `modal_experiment_1775524250.json`
- Policies: none, fifo, kv_adaptive
- Budget: 400 tokens, Steps: 20

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 20,952 | 0 | 0.0% |
| fifo | 7,962 | 0 | 0.0% |
| kv_adaptive | 8,181 | 0 | 0.0% |

### `modal_experiment_1775538482.json`
- Policies: none, fifo, kv_adaptive
- Budget: 600 tokens, Steps: 10

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 5,891 | 0 | 0.0% |
| fifo | 4,928 | 0 | 0.0% |
| kv_adaptive | 4,878 | 0 | 0.0% |

### `v2_experiment_1775540387.json` (best result)
- Policies: none, fifo, kv_adaptive
- Budget: 500 tokens, Steps: 10

| Policy | Total Prompt | Total Cached | Mean Hit Rate |
|--------|-------------|-------------|---------------|
| none | 7,708 | 7,280 | 94.4% |
| fifo | 3,669 | 1,472 | 40.1% |
| kv_adaptive | 4,217 | 4,160 | **98.6%** |

## Haiku-5 Batch Experiment (`exp_haiku_5/`)

Large-scale SWE-bench run with 4 policies. Full repo checkouts are gitignored (4.6GB).
Manifest preserved in `results/exp_haiku_5/manifest.json`.

| Policy | Budget | Run ID | Time (s) | Exit Code |
|--------|--------|--------|----------|-----------|
| none | 999999 | none_999999 | 1360 | 0 |
| summarize | 8000 | summarize_8000 | 52310 | 0 |
| fifo | 8000 | fifo_8000 | 3917 | 0 |
| adaptive | 8000 | adaptive_8000 | 742 | -15 |

## Live Experiments (`live_q30/`)

Interactive dashboard and viewer for live 30-question experiments across 4 policies:
- `none_10000/`, `fifo_10000/`, `adaptive_10000/`, `kv_adaptive_10000/`
- `dashboard.html` and `viewer.html` for visualization

## Visualization Artifacts

- `results/v2_experiment_1775540387_before_after.png` — Before/after comparison plot
- `results/v2_experiment_1775540387_hit_rate.png` — Cache hit rate over steps
