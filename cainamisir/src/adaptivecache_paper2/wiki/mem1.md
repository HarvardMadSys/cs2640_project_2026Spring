---
title: "MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents (Zhou et al., 2025)"
type: source
tags: [context-management, reinforcement-learning, memory, multi-turn, constant-memory]
date_created: 2026-04-04
date_updated: 2026-04-04
---

## Problem

Long-horizon agents using the standard full-context append model (ReAct style) suffer quadratic memory and compute growth as context length increases. Summarization-based compression methods like periodic context replacement still require large context windows during the rollout phase. The paper asks: can an agent maintain **constant peak memory** across arbitrarily long tasks while retaining the ability to reason about information from many steps ago?

A secondary challenge is **multi-objective tasks** — tasks that require tracking many independent sub-goals simultaneously. Standard agents exhibit rapid performance degradation as the number of objectives grows (e.g., 16-objective QA drops to near zero accuracy).

## Proposed Method

### Internal State `<IS>`

MEM1 introduces a special token block `<IS>` (Internal State) that acts as the sole persistent memory across turns. At the end of each turn:

1. The model reads the current observation and its existing `<IS>` block
2. It **generates a new `<IS>`** that blends reasoning traces and updated memory
3. The new `<IS>` completely **replaces** the old one — prior context (observations, prior `<IS>`) is discarded

The context at turn t is exactly:
```
(task_description, <IS>t, current_observation)
```

This gives constant peak memory regardless of trajectory length. The model must learn to encode all task-relevant state into `<IS>` before discarding the prior context.

### Training with 2D Attention Mask

Because each turn discards prior context, standard sequence-level training cannot backpropagate across turns. MEM1 uses a **2D attention mask** that stitches multiple turns into a single training sequence while blocking attention from later turns to earlier turns' discarded context. This allows joint optimization over the full trajectory without requiring full context retention.

### RL Training Setup

- Base: Qwen2.5-14B-Instruct (and smaller variants)
- Algorithm: PPO with multi-objective task augmentation
- Training data: tasks with varying numbers of objectives (1–16) to teach the model to scale memory usage
- `<IS>` format is free-form text; the model learns what to store without explicit structured memory schemas

### Multi-Objective Task Augmentation

To train robust memory, tasks are augmented with increasing numbers of independent sub-objectives. The model must track all objectives simultaneously in `<IS>`, teaching it to write compact, complete internal states.

## Key Results / Findings

- **3.5× memory reduction** vs. full-context baseline (peak context tokens)
- **3.7× performance gain** on 16-objective tasks vs. Qwen2.5-14B baseline
- MEM1 (training-free) — using a base model without MEM1 training and forcing the `<IS>` format — actually **hurts** performance, demonstrating that the behavior must be trained
- Scales gracefully from 1 to 16 objectives; full-context agents degrade rapidly beyond 4–6 objectives
- Outperforms summarization-based baselines on multi-objective benchmarks while using less context

## Relationship to AdaptiveCache

MEM1 and AdaptiveCache both aim to control context growth, but their designs are diametrically opposed on the key axis of prefix cache behavior:

**Prefix cache behavior.** MEM1 generates a *new* `<IS>` block every turn. This new `<IS>` is freshly generated text — token IDs that have never appeared in this position in prior steps. Every turn therefore produces a completely new prefix, making **prefix KV-cache reuse impossible**. The KV state computed for turn t's `<IS>` is useless for turn t+1's new `<IS>`. AdaptiveCache is specifically designed around the opposite goal: preserve prefix identity across turns so that cached KV states remain valid and reusable.

**Training requirement.** MEM1 requires full PPO training from a base model. The paper explicitly shows that applying the `<IS>` format to an untrained model *reduces* performance — the behavior cannot be prompted in. AdaptiveCache requires no training; it is a post-hoc context layout policy that works with any frozen model.

**Memory model.** MEM1's memory is generative and implicit: the model writes whatever it deems relevant into `<IS>` as free text. AdaptiveCache's "memory" is structural: it retains actual tokens from the agent's history, reordered by predicted reuse value. MEM1 compresses semantically (losing some information irreversibly); AdaptiveCache selects and reorders without lossy compression.

**Compute model.** MEM1 achieves O(1) peak context tokens, but pays a generation cost at each turn to produce the new `<IS>`. AdaptiveCache adds minimal overhead (a heuristic layout operation) and does not require any extra LLM generation steps.

**When MEM1 wins.** For very long tasks (hundreds of turns) where context cannot be bounded by selection — e.g., tasks requiring integration of thousands of distinct facts — MEM1's constant-memory guarantee is valuable. AdaptiveCache does not reduce token count; if the retained context still grows beyond the window, AdaptiveCache alone cannot solve the overflow problem.

**Complementarity.** MEM1's `<IS>` content could be structured to serve as a stable prefix if the format were fixed (e.g., a structured schema with consistent field positions). In that case, AdaptiveCache-style layout optimization could be applied *within* the `<IS>` block. This is speculative but suggests potential for hybrid designs.

## Citations to Follow Up

- Qwen2.5-14B-Instruct — base model used for MEM1 training
- PPO (Schulman et al., 2017) — RL algorithm used
- ReAct (Yao et al., 2022) — baseline agent loop; `wiki/react.md`
- MemAgent (Yu et al., 2025) — alternative constant-memory approach via chunk processing; `wiki/memagent.md`
- SUPO / Lu et al. (2025) — RL-trained summarization; `wiki/summarization-rl.md`
- Context-Folding (ByteDance Seed, 2025) — active context management with branch/return; `wiki/context-folding.md`
