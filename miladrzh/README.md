# On Scheduling and KV-Cache Management for AI Agents & HyperAgenting

**Author:** Milad Rezaei Hajidehi
**Course:** CS 264: Storage Systems (Prof. Yang), Harvard University

When an LLM agent calls an external tool, its KV-cache sits idle on the GPU. This project characterizes these idle windows across four agent types (RAG, SQL, Data Analysis, SWE-Bench), instruments vLLM's block manager for per-tool-call KV-cache tracing, and introduces HyperAgenting: a cooperative multitasking scheduler that fills idle windows by switching between partner tasks, achieving 22% wall-time reduction and 28% throughput improvement on a single A100.

**Goal achieved:** Between 100% and 125%. (without disk)

## Repository Structure

```
miladrzh/
  README.md            this file
  report.pdf           compiled final report (USENIX format, 4 pages)
  ai-usage.md          AI usage report
  report/              LaTeX source and figures
    final_report.tex
    hyper_v3_vs_baseline.png
    speculation_time_mae.png
  src/
    agent/             core framework
      loop.py          ReAct agent loop with tool calling
      tracer.py        per-tool-call JSON trace collector
      vllm_engine.py   InstrumentedEngine, AsyncInstrumentedEngine, AgentAwareBlockManager
      hyperagent.py    cooperative multitasking scheduler (HyperAgenting)
      tools.py         tool dispatchers (web_search, sql_exec, python_exec, bash_exec, etc.)
      vllm_hooks.py    vLLM lifecycle hooks
      config.py        model constants and feature flags
    tasks/             task loaders per agent type
      rag.py
      sql.py
      data_analysis.py
      hotpotqa.py
      browsecomp.py
      swe_bench.py
    benchmarks/        benchmark datasets and experiment scripts
      _sweep256.json   curated 256-task pool (87 HotpotQA + 84 BrowseComp + 85 GAIA)
      _smoke16.json    16-task smoke test subset
      hotpotqa/        HotpotQA multi-hop QA tasks
      browsecomp/      BrowseComp deep web browsing tasks
      slurm/           SLURM job scripts for cluster runs
    run.py             single-task runner
    benchmark.py       batch runner (async/concurrent, baseline and hyperagent modes)
    speculation.py     speculation accuracy measurement
    requirements.txt   Python dependencies
```

## Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA support (tested on H100 80GB, A100 80GB)
- vLLM 0.7.3
- Brave Search API key (for RAG agents, set `BRAVE_API_KEY` env var)

## Setup

```bash
cd src/
pip install -r requirements.txt
export BRAVE_API_KEY=your_key_here
```

## Running Experiments

### Single Agent Task

```bash
python run.py --model Qwen/Qwen2.5-7B-Instruct --task hotpotqa --trace-dir ../traces/
```

### Baseline Benchmark (Concurrent Agents)

```bash
python benchmark.py --model Qwen/Qwen2.5-7B-Instruct \
    --tasks benchmarks/_smoke16.json \
    --mode baseline \
    --trace-dir ../traces/
```

### HyperAgenting (Cooperative Multitasking)

```bash
python benchmark.py --model Qwen/Qwen2.5-7B-Instruct \
    --tasks benchmarks/_smoke16.json \
    --mode hyperagent \
    --trace-dir ../traces/
```

### Speculation Accuracy

```bash
python speculation.py --model Qwen/Qwen2.5-7B-Instruct \
    --trace-dir ../traces/
```

### SLURM Cluster Sweep

```bash
cd benchmarks/slurm/
bash submit_sweep.sh
```

