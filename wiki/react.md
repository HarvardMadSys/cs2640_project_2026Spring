---
title: "ReAct: Synergizing Reasoning and Acting in Language Models (Yao et al., 2022/ICLR 2023)"
type: source
tags: [context-management, agent, reasoning, full-context, baseline]
date_created: 2026-04-04
date_updated: 2026-04-04
---

## Problem

LLM agents that reason (chain-of-thought) and act (tool use) were developed as separate paradigms. Reasoning-only approaches like chain-of-thought lack grounding in external information; acting-only approaches lack the internal reasoning that supports error recovery and goal decomposition. The authors ask: can interleaving reasoning traces with action steps improve agent performance on knowledge-intensive and decision-making tasks?

## Proposed Method

ReAct introduces a simple loop where the model alternately generates:

1. **Thought** — a free-text reasoning step interpreting recent observations and planning next actions
2. **Action** — a structured call to an external tool (search engine, API, environment step)
3. **Observation** — the result returned by the tool, appended verbatim to context

The full context at step t is the concatenation of all prior (Thought, Action, Observation) triples:

```
ct = (task, Th1, A1, Ob1, Th2, A2, Ob2, ..., Tht-1, At-1, Obt-1)
```

No context compression, eviction, or summarization is applied. Context grows monotonically at every step. The model is prompted with a few-shot demonstration of the Thought-Action-Observation format; no fine-tuning is required.

ReAct is evaluated on:
- **HotpotQA** and **Fever** (multi-hop QA, fact verification) using Wikipedia search
- **ALFWorld** (text-based household tasks)
- **WebShop** (product search and purchase simulation)

Human-in-the-loop error correction experiments show that a single in-context edit mid-trajectory can redirect the agent, demonstrating the value of interpretable reasoning traces.

## Key Results / Findings

- ReAct outperforms chain-of-thought (CoT) on HotpotQA (27.4 → 29.6 EM with ReAct + CoT) and Fever (CoT 56.3 → ReAct 60.9 accuracy) when combined.
- On ALFWorld, ReAct achieves 71% success rate across 6 task types vs. BUTLER (45%) and SayCan (0% on unseen tasks).
- On WebShop, ReAct reaches 40.4% success, outperforming IL+RL (28.7%) and approaching human performance (59.6%).
- Reasoning traces substantially reduce "hallucination" errors relative to pure CoT — the agent can look up facts rather than fabricate them.
- The interleaved Thought-Action-Observation loop is now the de facto baseline for agentic benchmarks including SWE-Bench, BrowseComp, and GAIA.

## Relationship to AdaptiveCache

ReAct is the **primary baseline** that AdaptiveCache is designed to improve upon. Its context model has three properties that AdaptiveCache directly addresses:

**1. Unbounded context growth.** ReAct appends every observation verbatim. Observations (web pages, code outputs, API responses) can be large and are often redundant or no longer relevant. AdaptiveCache adds a selection/reordering layer that controls what persists and where.

**2. No prefix-cache awareness.** ReAct is entirely naive about KV-cache behavior. As context grows, early tokens remain stable (the original task description and few-shot demonstrations), but middle tokens (prior observations) are inserted in append order. AdaptiveCache promotes stable items toward the prefix and demotes volatile/stale items toward the suffix, maximizing the fraction of tokens that hit the KV cache on subsequent steps.

**3. No layout awareness.** ReAct makes no distinction between high-reuse tokens (system prompt, task framing) and low-reuse tokens (transient observations). All tokens are treated identically. AdaptiveCache's layout-awareness is the core contribution that ReAct lacks.

**What AdaptiveCache preserves from ReAct.** The Thought-Action-Observation loop itself is unchanged. AdaptiveCache is a drop-in context manager that sits between the loop iterations — it does not alter the agent's reasoning format or require any model fine-tuning. This is the key advantage over training-based successors like SUPO, MEM1, and MemAgent.

**Cost model contrast.** At step t, ReAct pays full attention over ct at every forward pass. With prefix caching enabled, only new tokens since the last call are recomputed — but any reordering (e.g., to surface a relevant older observation) would invalidate the cache and trigger recomputation of everything after the moved token. AdaptiveCache's layout policy avoids such invalidations by keeping stable tokens at fixed prefix positions.

## Citations to Follow Up

- Wei et al. (2022) — Chain-of-thought prompting (foundational reasoning baseline)
- Nakano et al. (2021) — WebGPT (web-grounded QA)
- Shridhar et al. (2020) — ALFWorld environment
- Yao et al. (2020) — WebShop environment
- Context-Folding (ByteDance Seed, 2025) — extends ReAct with branch/return active context management; `wiki/context-folding.md`
- SUPO / Lu et al. (2025) — RL-trained summarization over ReAct trajectories; `wiki/summarization-rl.md`
