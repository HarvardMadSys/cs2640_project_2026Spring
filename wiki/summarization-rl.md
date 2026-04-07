---
title: "Scaling LLM Multi-Turn RL with End-to-End Summarization-Based Context Management (Lu et al., 2025)"
type: source
tags: [context-management, reinforcement-learning, summarization, multi-turn, swe-bench]
date_created: 2026-04-04
date_updated: 2026-04-04
---

## Problem

As LLM agents tackle longer-horizon tasks, context windows fill up with accumulated Thought-Action-Observation history. Naively truncating old context (as done in standard GRPO-based RL training) degrades performance: the model loses access to critical earlier information. The paper asks how to scale multi-turn RL training to long-horizon agentic tasks without hitting context limits.

The specific failure mode is **trajectory truncation during rollout**: when a trajectory exceeds the context window, tokens are dropped from the front, destroying task-relevant history and making reward attribution noisy.

## Proposed Method

**SUPO (Summarization-Augmented Policy Optimization)** extends the standard RL-from-policy-rollout framework with a periodic summarization step that compresses accumulated context into a short summary, then restarts context from that summary.

### Summarization-Augmented MDP

The standard MDP over (state, action) pairs is extended: when a trigger condition fires (e.g., context length exceeds a threshold), the agent invokes a **summarization action** that:

1. Reads the current full context ct
2. Generates a textual summary s
3. Replaces ct with the condensed context (task_prefix, s)

This defines a **summarization-augmented MDP** where the state transition includes both environment dynamics and a compression step. The full trajectory is now a sequence of sub-trajectories separated by summarization points.

### Policy Gradient Decomposition

The SUPO objective decomposes the policy gradient across sub-trajectories separated by summarization checkpoints. Each sub-trajectory's gradient is computed independently (similar to GRPO-style group sampling), then summed. An **overlong trajectory masking** mechanism discards trajectories that still exceed the window after summarization, preventing degenerate long-context examples from corrupting gradient estimates.

### Training

- Base model: fine-tuned LLM (architecture not specified in detail)
- RL algorithm: GRPO-style with group-sampled rollouts
- Summarization is learned jointly with the task policy — the model learns when to summarize and how to summarize
- Evaluated on CodeGym (coding tasks) and BrowseComp-Plus (web browsing QA)

## Key Results / Findings

- **+3.2% on CodeGym** relative to GRPO baseline without summarization
- **+14.0% on BrowseComp-Plus** relative to GRPO baseline
- Overlong trajectory masking alone provides ~2% lift; joint summarization provides the remainder
- Ablations show that summarization placement (trigger threshold) matters: too-early summarization discards still-useful context; too-late summarization doesn't prevent truncation
- Summarization quality improves during RL training — the model learns to preserve task-critical information in summaries

## Relationship to AdaptiveCache

SUPO and AdaptiveCache share the same high-level goal — prevent context window overflow in long-horizon agentic tasks — but take fundamentally different approaches:

**Training requirement.** SUPO requires RL training on task-specific rollouts. The summarization policy and the task policy are co-trained. AdaptiveCache is entirely training-free: it applies a layout heuristic (promote stable tokens toward prefix, demote volatile tokens toward suffix) without any gradient updates.

**Prefix cache behavior.** This is the sharpest contrast. SUPO's summarization step *rewrites* the prefix: the new context is `(task_prefix, generated_summary)`. The summary is new text that was never seen before — its token embeddings have never been cached. Every summarization event therefore **invalidates the entire KV cache** for the new prefix, forcing a full recomputation at the next forward pass. AdaptiveCache deliberately avoids this: by reordering and selecting within the existing token set (without generating new summary tokens), it preserves prefix identity and allows KV cache reuse across steps.

**Granularity.** SUPO operates at session/trajectory granularity — summarization is coarse and infrequent. AdaptiveCache operates at the item level on every step, continuously optimizing layout.

**Complementarity.** SUPO could theoretically benefit from AdaptiveCache within each sub-trajectory (between summarization events). AdaptiveCache's layout optimization would reduce inference cost during the intervals when SUPO is not summarizing. The two are not mutually exclusive.

**When SUPO wins.** For tasks where context genuinely must be *distilled* (e.g., summarizing 50 web pages into a coherent answer), SUPO's generative summarization captures semantic compression that pure reordering cannot. AdaptiveCache does not reduce token count — it only improves hit rate on the tokens that are kept.

## Citations to Follow Up

- GRPO (Shao et al., 2024) — Group Relative Policy Optimization, the base RL algorithm
- BrowseComp-Plus — benchmark for web browsing QA with long-horizon reasoning
- CodeGym — coding benchmark used for evaluation
- Context-Folding (ByteDance Seed, 2025) — related ByteDance work on active context management; `wiki/context-folding.md`
- ReSuM (Wu et al., 2025) — plug-and-play summarization alternative (training-free mode); `wiki/resum.md`
- MEM1 (Zhou et al., 2025) — alternative memory-state approach; `wiki/mem1.md`
