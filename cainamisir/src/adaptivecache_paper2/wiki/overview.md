---
title: AdaptiveCache — Project Overview
type: overview
tags: [adaptivecache, context-management, prefix-caching, kv-cache, llm-agents]
date_created: 2026-04-04
date_updated: 2026-04-05
---

# AdaptiveCache: Context Management for LLM Agents

**Author:** Vlad Cainamisir, Harvard University  
**Status:** Proposal stage (75% / 100% / 125% milestones defined)

## Core Problem

LLM agents maintain long, evolving contexts (code, tool outputs, conversation history). As context grows:
- **Latency increases** — every new token must attend over the full history
- **Token cost increases** — uncached tokens are significantly more expensive than cached ones
- **Prefix cache invalidation** — any modification to the early prefix invalidates all downstream KV-cache computation

Modern serving systems exploit **prefix caching**: if the prompt prefix is identical across requests, the computed KV states are reused. This makes context layout a first-class cost variable. **Changing early tokens is expensive; appending to the suffix is cheap.**

## Core Insight

> Context management is a **compaction and layout optimization problem**, analogous to storage systems where placement determines reuse efficiency.

Existing approaches (LRU, FIFO, retrieval-based pruning, compression) optimize *what to remove* but ignore *where items should sit* relative to the cached prefix. AdaptiveCache jointly addresses both.

## Proposed System: AdaptiveCache (Refined Design)

*Updated 2026-04-05 based on KV cache architecture research — see [kv-cache-architecture.md](kv-cache-architecture.md)*

AdaptiveCache decomposes into **two distinct mechanisms** that work at different timescales:

### Mechanism 1: Layout Optimizer (runs rarely — when importance structure changes)

Determines the stable prefix composition. Items are scored by:
- **Attention signals** — tokens receiving high attention across recent steps (H2O-style heavy hitters)
- **Structural heuristics** — function signatures, API definitions, documentation headers
- **Recency** — most recent tool outputs stay in the suffix

High-scoring items are promoted to the **pinned prefix zone** at fixed absolute positions. They stay there permanently until explicitly demoted. Low-scoring items stay in the **volatile suffix zone**.

**Why layout still matters even if eviction is free:** Layout optimization is not about making eviction cheap (see below — it already is). It is about ensuring **cross-step prefix cache hits** — the sequence of bytes sent to the API must be identical at the prefix across consecutive steps. This only happens if the same high-value items sit at the same absolute positions step after step. Without layout discipline, important items scatter into the middle of the context and the prefix changes constantly.

### Mechanism 2: In-Place Eviction Engine (runs every step — essentially free)

New finding from the literature ([kv-cache-architecture.md](kv-cache-architecture.md)): mid-sequence KV eviction **does not require recomputing downstream states**, as long as:
1. Post-rotated keys are used (RoPE baked in at compute time — standard in all modern LLMs)
2. Evicted positions become **holes** — not attendable, never renumbered
3. Eviction is aligned to **logical block boundaries** (tool calls, turns — not individual tokens)

This means AdaptiveCache's volatile suffix items can be evicted cheaply each step without generating new tokens or invalidating the cached prefix. The eviction mechanism is equivalent to Claude Code's `cache_edits` primitive or PagedEviction's block-wise approach.

**The "never renumber" invariant:** Once an item is placed at an absolute position, it stays there or becomes a hole. Renumbering surviving tokens after eviction scrambles RoPE and breaks everything — this is the failure mode that makes naive compaction harmful.

### Full System Picture

```
Step N context:
[PINNED PREFIX ZONE          ][VOLATILE SUFFIX ZONE      ]
[sys][stable_A][stable_B][...][recent_tool1][recent_tool2]
 ← cached across all steps →   ← holes left here each step →

Step N+1:
[PINNED PREFIX ZONE          ][           ][new_tool3    ]
[sys][stable_A][stable_B][...][   hole    ][new_tool3    ]
 ← identical bytes → cache hit!  ← evicted, appended    →
```

The system operates **online** (no offline training required), continuously adapting as the agent executes.

### Two-Tier Pipeline (inspired by Claude Code's cheapest-first hierarchy)

| Tier | Operation | Cost | Frequency |
|---|---|---|---|
| 1 | In-place hole eviction of volatile suffix items | ~free (no recompute, no new tokens) | Every step |
| 2 | Layout reorganization (rescore + re-pin prefix) | Moderate (attention scoring pass) | When importance structure changes |
| 3 | Full summarization (emergency fallback only) | Expensive (LLM call, prefix invalidated) | Rarely / never |

The goal: never reach Tier 3.

### Scoring Signals

The layout optimizer uses a 2D scoring framework — see [importance-scoring.md](importance-scoring.md) for full detail.

**Importance signals** (what to keep):
- Expected Attention (closed-form future query prediction — arXiv:2510.00636)
- Cumulative past attention (H2O-style running mean)
- Reference count — how many subsequent tool calls cite this block (novel, agentic-specific)
- Low-entropy token protection — factual anchors must not be evicted (ForesightKV)

**Stability signals** (what to pin *early*):
- Structural type prior — function defs (0.9) vs bash outputs (0.2) vs error messages (0.1)
- Importance variance — consistently important = stable = pin candidate
- Tool-call dependency graph centrality — upstream blocks in the task DAG are load-bearing

**Two additional techniques:**
- Contrastive deduplication — evict blocks that are semantically redundant with already-pinned blocks
- Sink-aware layout — put highest-importance content at positions 4–100, immediately after StreamingLLM-style sink tokens, to exploit primacy bias for free

## Milestones

| Grade | Goal |
|---|---|
| 75% | Instrumentation: log attention, reference counts, cache hit rates on SWE-bench. Implement FIFO/LRU. Validate structural priors. Establish Pareto frontier for naive eviction. |
| 100% | AdaptiveCache: structural type prior + reference counting + importance variance + cumulative attention. Layout optimizer + hole-leaving eviction. Beat ReSuM training-free on cost at equal task performance. |
| 125% | Learned signals: Expected Attention (closed-form) + low-entropy protection (ForesightKV) + learned importance weights distilled from oracle traces via pairwise ranking + GRPO. Compaction aggressiveness sweep. |

See [system-design.md](system-design.md) for the full technical specification and [research-plan.md](research-plan.md) for the experimental design and baseline comparison table.

## Benchmarks

- **SWE-bench Lite / Verified** — real GitHub issues requiring multi-step code edits
- **Synthetic traces** — controlled multi-step workflows

## Metrics

- Token cost (primary)
- Latency
- Prefix cache hit rate
- Task success rate

## Baselines

1. No pruning (full context / ReAct-style)
2. LRU / FIFO pruning
3. Retrieval-only pruning
4. Context compression systems

## Positioning vs Related Work

AdaptiveCache is distinct from existing approaches in a key way: it is the first system to model **context layout** as a decision variable under a **prefix-dependent cost model**. See [context-management.md](context-management.md) for a taxonomy of the space and [context-folding.md](context-folding.md) for the closest related work.

The strongest related work is **Context Folding** (ByteDance Seed, 2025), which uses a branch/return mechanism to collapse sub-trajectories. Key differences:
- Context Folding requires RL training; AdaptiveCache is heuristic/online
- Context Folding operates at the sub-trajectory level (coarse); AdaptiveCache operates at the token/item level (fine-grained)
- Context Folding is not prefix-cache-aware in its layout decisions; AdaptiveCache is designed around the prefix cost model

## Open Questions

**Eviction mechanics:**
- Anthropic's `cache_edits` API is internal — does a public equivalent exist, or must AdaptiveCache approximate hole-leaving by omitting items from the prompt? If items are omitted without renumbering, does the API correctly handle the positional gaps, or does it reindex?
- What is the right logical block granularity? Tool-call level is the natural unit for agentic contexts; finer-grained eviction within a tool result may not be worth the complexity.
- "Stateful KV Cache Management" (arXiv:2511.04686) shows positional integrity beats token count — how aggressive can eviction be before the holes themselves hurt performance?

**Importance scoring:**
- How noisy are attention signals as a proxy for long-term token importance?
- ScissorHands shows >95% persistence of importance — can this be exploited to reduce scoring frequency (score once per N steps rather than every step)?
- Can PyramidKV's per-layer entropy insight guide which layers' attention scores to trust most for importance estimation?

**Layout decisions:**
- When should the prefix zone be reorganized vs left alone? Too-frequent reorganization invalidates the cache; too-infrequent means stale pins.
- Can AdaptiveCache be combined with Context Folding (coarse sub-trajectory folding + fine-grained layout optimization on the main thread)?

**Empirical:**
- What is the prefix cache hit rate improvement from layout optimization on real SWE-bench traces?
- How does compaction aggressiveness trade off against task success rate?
