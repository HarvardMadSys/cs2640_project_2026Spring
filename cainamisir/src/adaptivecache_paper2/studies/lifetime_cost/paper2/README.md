# Paper 2 — Memento for Agents

KV-aware compaction with recall, on self-hosted vLLM.

## Status

v0 in progress. See `paper/PAPER2_PLAN.md` for the project-level plan.

## Stack

- **Hardware**: NVIDIA RTX PRO 6000 Blackwell (sm_120, 96GB)
- **Driver / CUDA**: 570.211.01 / 12.8
- **Python**: 3.12 in `.venv-paper2/` (separate from project venv)
- **vLLM**: 0.13.0 + Memento overlay (`external/memento/vllm/install_overlay.sh`)
- **Engine**: V1 + FlashInfer attention backend (force via `VLLM_ATTENTION_BACKEND=FLASHINFER`)
- **Model (v0)**: `Qwen/Qwen3-4B-Instruct-2507` (cached at `/scratch/hf/`)
- **Memento writer**: Anthropic Haiku 4.5 (off-the-shelf, generates the textual memento at compaction events)

vLLM 0.13.0's default PyPI wheel ships with a FlashAttention-2 .so built for newer CUDA — fails on driver 12.8 with `cudaErrorUnsupportedPtxVersion`. FlashInfer (cu128) works; that's the chosen backend.

## Block markers

We don't have a Memento-trained model, so we drive masking with token IDs that already exist in the Qwen3 vocab and won't be naturally emitted in agent chat:

| Role           | Token              | ID     |
|----------------|--------------------|--------|
| `block_start`  | `<tool_response>`  | 151665 |
| `block_end`    | `</tool_response>` | 151666 |
| `summary_start`| `<\|fim_prefix\|>` | 151659 |
| `summary_end`  | `<\|fim_middle\|>` | 151660 |

Tool responses become natural blocks. Memento text is wrapped in repurposed FIM tokens.

`mask_delimiters=False` (Qwen3-style): block_start / block_end stay visible; only the content between them is masked.

## Cost model

Self-hosted GPU time per resolved task. Wall-clock includes prefill + decode + tool execution. Convert to $ at standard cloud rate (~$1-2/hour for A100, ~$2-4/hour for H100 spot) for paper figures. Phase 0 / Paper 1 measure API tokens; the comparison must be explicit about the cost-model switch.

## Layout

```
paper2/
├── adapters/        # ChatModel adapter wrapping vLLM + Memento config
├── prompt/          # Prompt construction with block markers
├── memento_writer/  # Haiku-based memento text generator
├── recall/          # v1: unmask triggers (LRU / embedding-sim / attention-driven)
├── configs/         # YAML configs for runs
└── tests/           # Smoke tests + regressions
```
