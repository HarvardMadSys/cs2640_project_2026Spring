---
title: "ReSuM: Unlocking Long-Horizon Search Intelligence via Context Summarization (Wu et al., 2025)"
type: source
tags: [context-management, summarization, plug-and-play, training-free, reinforcement-learning, gaia, browsecomp]
date_created: 2026-04-04
date_updated: 2026-04-04
---

## Problem

Long-horizon search agents (web browsing, GAIA-style multi-step research) accumulate large amounts of context from tool returns that quickly fill the context window. Existing solutions either require retraining the agent (SUPO, MEM1) or perform naive truncation that loses critical information. The paper asks: can context summarization be added as a **plug-and-play module** without modifying the agent model, while still improving performance on long-horizon search benchmarks?

A secondary question: can RL training further adapt an external summarization tool to the specific needs of long-horizon search (beyond what a general summarizer provides)?

## Proposed Method

### ReSumTool (Training-Free Mode)

ReSuM's core contribution is an **external summarization tool** — a separate model that the agent calls to compress its accumulated context. The base version uses **ReSumTool-30B**, a fine-tuned Qwen3-30B-A3B-Thinking model trained on summarization tasks.

The summarization flow:
1. Agent accumulates tool returns until context approaches the limit
2. Agent calls ReSumTool with the accumulated history
3. ReSumTool generates a summary of the accumulated context
4. The summary replaces the accumulated history in the agent's context
5. Agent continues from `(task_prefix, summary, current_step)`

The agent model itself is **never modified** — it uses the same weights throughout. ReSumTool is a separate model instance.

### ReSum-GRPO (RL Adaptation)

For further gains, the paper introduces **ReSum-GRPO**: RL training of ReSumTool to produce summaries that maximize the downstream agent's task success.

Key design choices:
- **Advantage broadcasting**: A single trajectory-level reward (task success/failure) is broadcast back to all summarization steps in the trajectory. This allows the summarizer to receive credit/blame for individual summarization decisions even though only a final reward is observed.
- The agent model is frozen during ReSum-GRPO training — only ReSumTool's weights are updated
- Rollouts interleave agent steps and summarization steps; only summarization-step losses contribute to the gradient

### Evaluation Setup

- Agent backbone: a capable long-context LLM (e.g., Qwen3-72B or similar)
- Benchmarks: GAIA (general agentic QA), BrowseComp (web browsing), BrowseComp-zh (Chinese)
- Compared against: ReAct (no summarization), SUPO, MEM1 (training-free)

## Key Results / Findings

- **+4.5% over ReAct baseline** with ReSumTool (training-free) on GAIA + BrowseComp combined
- **+8.2% with ReSum-GRPO** (RL-adapted summarizer) over ReAct baseline
- MEM1 applied training-free (forced format without RL training) **hurts performance** by ~2% — demonstrating that naively applying memory formats without training is harmful
- Advantage broadcasting is critical: without it, ReSum-GRPO provides only +1.3% (vs. +8.2% with broadcasting)
- ReSumTool generalizes across agent backbones — plug-and-play without agent modification
- Performance scales with summarizer model size; 30B summarizer outperforms 7B

## Relationship to AdaptiveCache

ReSuM is the closest competitor to AdaptiveCache in spirit — both are **modular additions** to existing agents rather than full agent retrain approaches. The comparison is therefore particularly instructive:

**Modularity.** Both are designed to be plugged into existing agent loops without modifying the base agent. ReSuM adds an external summarizer call; AdaptiveCache adds a layout reordering step. Neither requires agent fine-tuning.

**Training-free performance.** ReSuM's training-free mode achieves +4.5% over ReAct. AdaptiveCache similarly targets training-free gains. This makes ReSuM the most direct empirical baseline for AdaptiveCache to beat in a training-free setting.

**Prefix cache behavior.** This is the key distinction. ReSuM's summarization generates *new tokens* to replace the accumulated history. Even if the task prefix `(task_description, system_prompt)` is preserved, the summary text after it is fresh — its token IDs have never been cached. **Every summarization event invalidates cached KV states** for all tokens after the preserved task prefix. AdaptiveCache never generates new tokens; it reorders and selects from the existing token set, preserving token identity and enabling KV cache hits on all retained tokens.

**Inference cost model.** ReSuM adds an additional LLM inference call (ReSumTool) at each summarization event. For a 30B summarizer called every ~50 steps, this adds significant per-trajectory compute. AdaptiveCache's layout heuristic adds negligible compute — it is a scoring and sorting operation, not an LLM generation.

**Information loss.** ReSuM's generated summary is lossy — the summarizer decides what to include, and important details may be dropped. This is inherent to generative compression. AdaptiveCache retains actual tokens from the agent's history (no lossy compression), selecting which tokens to keep but never replacing them with generated proxies.

**When ReSuM wins.** For tasks requiring genuine semantic compression — e.g., distilling a 10-page web article into a 3-sentence summary before moving to the next retrieval step — ReSuM's generative summarization captures information that pure token selection cannot. If the relevant content in an observation is buried in verbose prose, selecting the prose verbatim (AdaptiveCache) is inferior to extracting the key facts (ReSuM).

**Advantage broadcasting as a technique.** ReSuM's advantage broadcasting (propagating trajectory reward to summarization steps) is a credit assignment mechanism applicable to any modular system where intermediate decisions affect final reward. If AdaptiveCache were to be trained (though it is currently training-free), a similar broadcasting technique could be applied to the layout decisions.

## Citations to Follow Up

- Qwen3-30B-A3B-Thinking — base model for ReSumTool
- GRPO (Shao et al., 2024) — RL algorithm base; ReSum-GRPO adapts this for summarizer training
- GAIA benchmark — general agentic QA evaluation
- BrowseComp / BrowseComp-zh — web browsing benchmarks
- SUPO / Lu et al. (2025) — RL-trained summarization in the agent itself; `wiki/summarization-rl.md`
- MEM1 (Zhou et al., 2025) — memory-state alternative; `wiki/mem1.md`
- Context-Folding (ByteDance Seed, 2025) — active context management; `wiki/context-folding.md`
