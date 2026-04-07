---
title: Context-Folding (Sun et al., 2025)
type: source
tags: [context-management, context-folding, reinforcement-learning, long-horizon, swe-bench, kv-cache]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Scaling Long-Horizon LLM Agent via Context-Folding

**Authors:** Weiwei Sun, Miao Lu, Zhan Ling, Kang Liu, Xuesong Yao, Yiming Yang, Jiecao Chen  
**Affiliations:** ByteDance Seed, CMU, Stanford  
**Date:** October 15, 2025 (arXiv:2510.11967)  
**Source file:** `raw/papers/contextFolding.pdf`

## Problem

LLM agents on long-horizon tasks accumulate a linear interaction history (reasoning + tool calls + observations). This causes two failures at scale:
1. **Degraded performance** — models struggle to use relevant information in very long contexts ("lost in the middle")
2. **Poor efficiency** — quadratic attention scaling + growing KV-cache overhead

Existing approaches:
- **Summarization-based**: trigger compression when context is full — disruptive, breaks reasoning flow
- **Multi-agent**: distribute across specialized agents — requires handcrafted workflows, hard to generalize

## Proposed Method: Context Folding

An agentic mechanism giving the model two special actions:

- **`branch(description, prompt)`** — create a sub-trajectory with its own isolated context to handle a subtask
- **`return(message)`** — fold the sub-trajectory: discard intermediate steps, keep only the summary `message`, and rejoin the main thread

The context manager `F` folds all action-observation pairs between `branch` and `return` calls:

```
F(a1,o1, [a2,o2,a3,o3,a4], o4, a5, [o5,a6,o6,a7,o7,a8], o8, a9,o9,a10,o10)
→    a1,o1,  a2,              o4, a5,  o5,                  o8, a9,o9,a10,o10
```

The agent operates in two states:
- **Planning state** (main thread): high-level reasoning, decides when to branch. Token-heavy operations discouraged here.
- **Execution state** (inside a branch): handles the assigned subtask. No nested branching.

**KV-cache efficiency**: when `return` is called, the KV-cache rolls back to the state before `branch` — the context prefix is identical to pre-branch, so cached computation is reused.

## Training: FoldGRPO

Standard GRPO (binary outcome reward) is insufficient — two failure modes emerge:
1. Agent leaves token-heavy operations in main context → exhausts budget
2. Agent fails to return from branches → incorrect scope

**FoldGRPO** adds token-level process rewards:

| Penalty | Condition | Value |
|---|---|---|
| Unfolded token penalty | Main thread > 50% of context limit | Q = −1 for main-thread tokens (except branch-creating turns) |
| Out-of-scope penalty | Branch actions outside specified subtask (judged by GPT-5-nano) | Q = −0.2 for all branch tokens |
| Failure penalty | Failed tool call turn | Q = −1 |

## Results

Base model: Seed-OSS-36B-Instruct. Context: 32K active, up to 10 branches (327K theoretical max).

| Model | BrowseComp-Plus Pass@1 | SWE-Bench Verified Pass@1 | Active Context |
|---|---|---|---|
| ReAct (32K) | 0.286 | 0.436 | 32K |
| ReAct (327K) | 0.478 | 0.552 | 327K |
| Summary Agent + RL | 0.527 | 0.550 | 32K × 10 |
| **Folding Agent + FoldGRPO** | **0.620** | **0.580** | 32K × 10 |
| GPT-5 (ReAct 327K) | 0.793 | 0.718 | 327K |

Key: Folding + FoldGRPO matches or beats the 327K ReAct baseline while using 10× smaller active context.

FoldGRPO cuts main trajectory to ~8K tokens while processing 100K+ total — **>90% context compression**.

## Key Insights

1. **RL is essential** — without FoldGRPO, the folding agent underperforms the long-context ReAct baseline
2. **More tool calls = better** — RL training encourages more thorough exploration; hard instances see response length grow from 100K → 160K tokens during training
3. **Length generalization** — trained on ≤10 branches, adaptively uses 32.6 branches on 50-question compound tasks
4. **Parallel branching** — experimented but showed no gain over sequential; tasks appear depth-first in structure

## Relationship to AdaptiveCache

**Complementary, not competing.** Context Folding and AdaptiveCache operate at different granularities:

| Dimension | Context Folding | AdaptiveCache |
|---|---|---|
| Granularity | Sub-trajectory (coarse) | Token/item level (fine) |
| Mechanism | Branch + return to collapse sub-task history | Compaction + reordering of individual items |
| Training | Requires RL (FoldGRPO) | Online heuristic (no training) |
| Layout-aware | No — doesn't optimize item ordering | Yes — core design goal |
| Prefix-cache-aware | Partially (KV rollback on return) | Fully (layout is the primary lever) |
| Online/offline | Online (during execution) | Online (during execution) |

**Potential synergy**: Context Folding handles the coarse structure (sub-task decomposition); AdaptiveCache could handle fine-grained ordering within the main thread and within branches. The two approaches could stack.

**AdaptiveCache's gap relative to this work**: Context Folding achieves strong results on SWE-bench (58%) via RL training. AdaptiveCache's 100% milestone targets the same benchmark. The key differentiator is: AdaptiveCache is training-free and explicitly optimizes prefix cache hit rate as a cost metric — Context Folding does not model this.

## Citations to Follow Up

- [19] Lu et al. — "Scaling LLM multi-turn RL with end-to-end summarization-based context management" (direct competitor)
- [43] Zhou et al. — Mem1 (memory + reasoning for long-horizon agents)
- [1] OpenHands context condensation
- [35] Yao et al. — ReAct (primary baseline framework)
