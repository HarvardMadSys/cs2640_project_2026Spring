# Paper 2 — v0 findings (2026-04-28)

Microbench results from `tests/microbench_growing.py` on Qwen3-4B-Instruct-2507 (Blackwell sm_120, vLLM 0.13.0 + Memento overlay, FlashInfer attention backend).

## Setup

- Synthetic agent loop with growing trajectory: turn N's prompt extends turn N-1's prompt with one new tool obs (4000 chars filler). Constant 16-token completion.
- Two variants:
  - **baseline**: no masking, no memento. Each turn adds full obs to prompt.
  - **memento**: `MementoVLLMModel` with default `last_only_masking=True`. Each turn renders earlier tool obs as plain inline memento text (~30 tokens) and the latest one with full block + summary markers.

## Headline (20 turns)

| | Baseline | Memento |
|---|---|---|
| Total wall | 4052 ms | **3554 ms (-12.3%)** |
| Turn-0 wall | 125 ms | 144 ms (+15%) |
| Turn-19 wall | 258 ms | **184 ms (-29%)** |
| Turn-19 prompt size | 17,008 tok | **2,186 tok (-87%)** |
| Memento per-turn wall | — | **flat in [173, 190] ms** |

Crossover at turn 5; gap widens monotonically afterwards.

## Per-turn breakdown (10 turns, abbreviated)

| Turn | Baseline ms | Memento ms | Δ ms | Baseline tok | Memento tok |
|---|---|---|---|---|---|
| 0 | 125.5 | 143.6 | +18.1 | 876 | 902 |
| 4 | 175.1 | 176.6 | +1.5 | 4268 | 1170 |
| 9 | 198.6 | 178.1 | -20.5 | 8508 | 1505 |
| 19 | 257.9 | 183.7 | -74.2 | 17,008 | 2186 |

## What it means

Memento's per-turn cost is **flat** (bounded by the inlined memento text size, ~67 tokens/turn). Baseline's per-turn cost **grows linearly** because each turn's prompt is fully sent to the engine. As trajectory length increases, baseline's wall time becomes quadratic in obs size × turns; memento stays linear in turns (with a small constant overhead per compaction event).

## Projected scaling (extrapolated; not measured)

| Trajectory length | Baseline final-turn | Memento final-turn |
|---|---|---|
| 20 turns | 258 ms | 184 ms |
| 50 turns | ~1.0 s | ~190 ms |
| 100 turns | ~3.0 s or OOM | ~190 ms |

The Paper 2 win regime is **long agent trajectories** — exactly the SWE-bench-Verified, τ-bench-airline territory where Paper 1 ran into context-length cliffs.

## Engineering details that mattered

1. **`last_only_masking=True`** (the working multi-turn recipe). Without it, every prior tool message gets re-marked → cascading re-prefills → 5× slowdown.
2. **`restart_mode=True`** required for prompt-time compaction. Without it, the engine hangs in deferred-compaction limbo.
3. **`keep_last_n_blocks=0`** (compact all completed blocks). Values > 0 deadlock in some configurations.
4. **FlashInfer attention backend** required on Blackwell + CUDA 12.8 (PyPI wheel's FA-2 .so is built for CUDA 13).
5. **Qwen3 vocab token IDs repurposed** as block markers — no SFT, no tokenizer modification.

## What's still open (Paper 2 v1+)

- **Recall mechanism** — Memento's overlay only masks; we need to *unmask* a block when the agent's attention indicates it needs the original obs. This is the actually-novel piece beyond the Memento paper.
- **The early-turn spike (~18ms)** — fixed per-block compaction cost. Reducible via `compact_on_summary_end=False` + manual `force_compact_pending` after generation, which moves the work off the chat() critical path. Untested.
- **MoE scaling** — Qwen3-30B-A3B works on this stack; growing-bench numbers on 30B will look different (longer per-turn baseline, larger absolute savings).
- **Real-benchmark measurements** — the cleanest comparison is on swebench_live or τ-bench, which is task #6.
