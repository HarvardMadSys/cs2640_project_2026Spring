---
title: "MemAgent: Reshaping Long-Context LLM Agent with Memory-Augmented Chunk-by-Chunk Processing (Yu et al., 2025)"
type: source
tags: [context-management, reinforcement-learning, memory, long-context, chunk-processing, linear-complexity]
date_created: 2026-04-04
date_updated: 2026-04-04
---

## Problem

Long-document and long-horizon tasks require reasoning over inputs that can span millions of tokens — far beyond any practical context window. Naively chunking input and processing each chunk independently loses cross-chunk dependencies. Full-context processing is quadratically expensive and hits hardware limits. The paper asks: can an agent process arbitrarily long inputs in linear time while maintaining useful memory across chunks?

The focus is primarily on **long static document reading** (NIAH-style needle-in-a-haystack, multi-hop QA over books) rather than interactive tool-use, though the method generalizes.

## Proposed Method

### Chunk-by-Chunk Processing with Overwrite Memory

MemAgent processes input sequentially in fixed-size chunks. At each chunk step, the context is:

```
(task_description, memory_block, current_chunk)
```

where `memory_block` is a fixed 1024-token region. After processing each chunk, the model:

1. Reads the current `memory_block` and `current_chunk`
2. **Overwrites** `memory_block` with a new 1024-token memory — selectively retaining, updating, or discarding prior memory contents
3. Discards the processed chunk entirely

The context window is fixed at ~8K tokens (1024 memory + ~5000 chunk + task overhead), regardless of total input length. This yields **O(N) complexity** for N-token inputs.

### Multi-Conv DAPO

Standard RL algorithms (GRPO, PPO) sample single-conversation rollouts. MemAgent's chunk-by-chunk structure means each "conversation" is one chunk step — but the agent's performance on the full task depends on behavior across all chunk steps. The paper introduces **Multi-Conv DAPO**, which:

- Treats each chunk conversation as an **independent RL optimization unit** (no shared KV state across chunks during rollout)
- Applies DAPO (Dynamic Advantage Policy Optimization) within each chunk conversation
- Aggregates reward from the full-trajectory outcome, broadcasting it back to each chunk step for credit assignment

This allows standard RL machinery to train across chunk boundaries without requiring cross-chunk attention during training rollouts.

### Training Setup

- Base: Qwen2.5-7B-Instruct (primary), also 3B and 14B
- Context during training: 8K tokens per chunk conversation
- Training data: 32K-token documents (NIAH tasks, reading comprehension)
- At inference: extrapolates to 3.5M tokens with less than 5% accuracy loss, trained only on 32K data

### Memory Format

Memory is free-form text (the model writes whatever it finds relevant). No structured schema is imposed. The model learns memory writing discipline from RL reward signal.

## Key Results / Findings

- **Linear O(N) complexity** — processes 3.5M tokens with an 8K context window
- **Less than 5% accuracy loss** vs. full-context oracle on NIAH tasks, even at 3.5M tokens when trained on 32K
- Outperforms retrieval-augmented generation (RAG) baselines on multi-hop QA over long documents
- Multi-Conv DAPO provides +8–12% over naive chunk processing without memory RL training
- Memory overwrite strategy outperforms memory append strategies (which re-introduce the context growth problem)
- 7B MemAgent outperforms 72B full-context baseline on NIAH-style tasks

## Relationship to AdaptiveCache

MemAgent and AdaptiveCache both manage context to enable long-horizon processing, but their domains and mechanisms are distinct:

**Use case scope.** MemAgent is optimized for **long static document reading** — processing a fixed input that the agent reads sequentially. AdaptiveCache targets **interactive agentic tasks** where context grows from tool calls, observations, and reasoning traces that arrive dynamically. MemAgent's chunk structure assumes a pre-existing document to chunk; it does not naturally handle the dynamic, unpredictable token arrival of agentic loops.

**Prefix cache behavior.** Like MEM1, MemAgent overwrites its memory block at each chunk step. The new memory text is freshly generated and was never cached. **Cross-chunk prefix reuse is impossible**: each chunk conversation starts fresh. AdaptiveCache preserves token identity across steps, enabling KV cache hits on stable prefix regions. MemAgent's approach is fundamentally incompatible with prefix caching across chunk boundaries.

**Training requirement.** Multi-Conv DAPO requires RL training on task-specific rollouts. AdaptiveCache requires no training. Notably, MemAgent's memory discipline must be learned — without RL training, naive chunk-by-chunk processing loses critical cross-chunk information.

**Complexity.** MemAgent achieves true O(N) complexity for static inputs. AdaptiveCache does not reduce worst-case complexity — it reduces the fraction of tokens that must be recomputed per step (by improving cache hit rate), but does not compress the context to a fixed size.

**Memory in token space.** MemAgent's memory is human-readable text — a distinct design choice from attention-based memory (e.g., retrieval over embeddings). This aligns with AdaptiveCache's token-space orientation: both work within the standard autoregressive context rather than maintaining external vector stores.

**Potential synergy.** In an agentic setting where MemAgent is used to process large tool outputs (e.g., reading a 1M-token codebase), AdaptiveCache could optimize the *outer* agent context (task description, tool invocations, memory block position) to maximize prefix cache hits on the non-chunk portion of the context. The memory block itself, being overwritten each step, would not benefit from prefix caching.

## Citations to Follow Up

- DAPO (Qwen team, 2025) — Dynamic Advantage Policy Optimization base algorithm
- Multi-Conv DAPO — MemAgent's extension to multi-conversation RL
- NIAH (Needle-in-a-Haystack) benchmark — primary evaluation suite
- Qwen2.5-7B-Instruct — base model
- MEM1 (Zhou et al., 2025) — alternative constant-memory approach via internal state; `wiki/mem1.md`
- MemoryOS, MemGPT — related memory-augmented agent systems
- ReSuM (Wu et al., 2025) — complementary summarization tool; `wiki/resum.md`
