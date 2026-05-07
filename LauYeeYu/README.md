# Computation-Aware Caching for KV Cache

## Abstract

As Large Language Models scale to massive context windows, the Key-Value (KV)
cache becomes the dominant factor in serving performance. Existing prefix
caching strategies ignore the quadratic complexity of self-attention, treating
all cache blocks as equally valuable despite the large recomputation asymmetry
between root and leaf tokens. This project presents **CHAFRA**
(Cache-Hole-Aware Frequency-Recency Algorithm), a system that optimizes prefix
caching by accounting for both computational cost and cache fragmentation. It
introduces **CAFRA**, an online eviction policy that prioritizes deep,
computationally expensive blocks, and a hole-aware management layer that
enables reuse of fragmented cached segments via batched hole filling.
Evaluation on production traces shows CHAFRA reduces Time-to-First-Token
(TTFT) by protecting high-value blocks.

See `report.pdf` for the full writeup and `ai-usage.md` for AI tooling
disclosure.

## Goal Status

| Goal | Description | Status |
|------|-------------|--------|
| 75%  | Benchmark vLLM with LRU; characterize recomputation latency of deep tokens; show inefficiency of recency-only eviction. | **Met** |
| 100% | Implement computation-aware and hole-aware optimizations (CAFRA / CHAFRA); demonstrate improved throughput and reduced TTFT variance. | **Met** |
| 125% | Explore dynamic hierarchical caching (HBM/CPU RAM) for agentic workloads; evaluate capacity vs. recomputation trade-offs. | **Not achieved** |

## Repository Layout

```
LauYeeYu/
├── README.md           This file
├── report.pdf          Final report (USENIX format)
├── report/
│   ├── report.tex      LaTeX source
│   └── figure/         Figures used in the report
├── ai-usage.md         AI usage disclosure
└── src/
    ├── evaluate.cpp    Trace-driven simulator (links libCacheSim)
    ├── parser.h        Trace parsing helpers
    ├── Makefile        Build + sweep + plotting pipeline
    ├── analyze_*.py    Trace / hole / prefix-tree analysis
    ├── visualize_results.py
    ├── ilp_*.py        ILP-based offline-optimal baseline
    ├── add_positional_encoding.py
    ├── reconstruct_trace.py
    ├── interactive_trace_analyzer.py
    ├── pretty_plot_qwen.py
    ├── compare_eviction_logs.py
    ├── scripts/        Driver shell scripts
    ├── trace_tools/    Trace conversion / interleaving utilities
    ├── vllm_eval/      End-to-end vLLM benchmarks (latency / throughput)
    ├── libCacheSim/    Submodule: forked libCacheSim with new policies
    └── vllm/           Submodule: forked vLLM with hole-aware block manager
```

The two submodules contain the bulk of the implementation:

- `src/libCacheSim` — adds the CAFRA / CHAFRA eviction policies and the
  Belady-compute / GDCF / LCD baselines used in the simulator sweep.
- `src/vllm` — adds the hole-aware block manager and integrates the new
  policies for end-to-end evaluation.

## Building

### 1. Clone with submodules

```bash
git clone --recurse-submodules <repo-url>
# or, if already cloned:
git submodule update --init --recursive
```

### 2. Install libCacheSim (required by the simulator)

Follow the upstream build instructions in `src/libCacheSim/`. The Makefile
expects headers / libraries / `libCacheSim.pc` under `~/.local`:

```bash
cd src/libCacheSim
mkdir -p build && cd build
cmake -DCMAKE_INSTALL_PREFIX=$HOME/.local ..
make -j && make install
```

Also install `glib-2.0` and `nlohmann-json` development packages
(`libglib2.0-dev` + `nlohmann-json3-dev` on Debian/Ubuntu;
`brew install glib nlohmann-json` on macOS).

### 3. Build the simulator

```bash
cd src
make            # release build  -> build/evaluate
make sanitize   # ASan build     -> build-san/evaluate-san
```

### 4. Python dependencies

```bash
cd src
pip install -r requirements.txt   # matplotlib, numpy
```

Plotting and ILP scripts additionally use `pulp` for the optimal baseline.

### 5. vLLM evaluation (optional, requires GPU)

```bash
cd src/vllm
pip install -e .                  # install the forked vLLM
```

A CUDA GPU and a local model checkpoint are required. The default
`MODEL_PATH` in `src/vllm_eval/Makefile` is
`/scratch/yiyu/models/Qwen3-Coder-30B-A3B-Instruct` — override it on the
command line.

## Running

### Trace-driven simulator sweep

```bash
cd src
make eval-parallel        # sweep all (algo, trace, cache size) combos
make all-plots            # generate plots from the logs
make all-analysis         # full pipeline: sweep + plots + trace analysis
make help                 # full target list
```

Logs land under `src/logs/` and figures under `src/plots/`. Trace files are
expected under `../datasets/` (see the `TRACES` variables in the Makefile for
the exact paths).

### Offline-optimal (ILP) baseline

```bash
cd src
make ilp-parallel         # solves the reuse-interval ILP for each (trace, size)
```

### End-to-end vLLM benchmarks

```bash
cd src/vllm_eval
make help                          # list targets
make eval-parallel                 # simulator-style sweep through evaluate.py
make latency-benchmark-and-plot    # real vLLM TTFT measurements
make throughput-benchmark-and-plot # real vLLM throughput measurements
```

Pass `MODEL_PATH=...` to override the model location.

## Reproducing the Report

Figures in `report/figure/` are generated by:

- `pretty_plot_qwen.py` — the `qwen_*_pos_pretty.pdf` and `*_latency_pretty.pdf` plots
- `analyze_holes.py` — `hole_filling.pdf`
- `vllm_eval/visualize_latency.py` — `qwen3_coder_30b_latency_curve.pdf`
- `analyze_traces.py` — context-length and FLOP-proportion CDFs

The report itself builds with any standard LaTeX distribution:

```bash
cd report
pdflatex report.tex && pdflatex report.tex
```
