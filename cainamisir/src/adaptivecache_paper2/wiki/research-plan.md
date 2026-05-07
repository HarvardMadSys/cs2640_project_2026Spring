---
title: AdaptiveCache — Research Plan and Baselines
type: synthesis
tags: [adaptivecache, research-plan, baselines, swe-bench, evaluation]
date_created: 2026-04-05
date_updated: 2026-04-05
---

# AdaptiveCache: Research Plan and Baselines

What to build, in what order, and what to measure it against.

---

## Phase 1 — Instrumentation and Measurement (→ 75% milestone)

**Goal:** Understand the problem empirically before building a solution. Most proposals fail because they optimize for the wrong thing. Measure first.

### 1.1 Build the Measurement Infrastructure

Instrument a baseline ReAct agent on SWE-bench to log:

- **Prefix cache hit rate per step** — what fraction of tokens are served from cache vs recomputed? This is the primary metric AdaptiveCache claims to improve. Establish the current baseline (likely very low for dynamic context).
- **Attention patterns per block** — log attention weights over blocks at each step. Validate the H2O/ScissorHands power-law distribution on real coding traces.
- **Reference counts** — for each block in context, count how many subsequent tool calls or reasoning steps cite it. Build the empirical distribution.
- **Block survival rate** — how long does each block type stay in context before being evicted under a simple FIFO baseline?
- **Token cost breakdown** — what fraction of cost comes from the system prompt (cached), recent tool results (uncached), vs middle context?

This logging infrastructure is necessary for all subsequent experiments. Without it, the 100% milestone is building blind.

### 1.2 Validate Structural Priors

Using the logged attention data, check whether the structural taxonomy stability priors in [importance-scoring.md](importance-scoring.md) hold empirically on SWE-bench:

- Do function definitions get higher cumulative attention than bash outputs?
- Does importance variance track stability as predicted?
- Are reference counts correlated with attention scores?

If not, revise the priors before building the full scorer.

### 1.3 Implement FIFO/LRU Baselines

Build simple eviction baselines as the 75% deliverable:
- **Full context (no eviction)** — measure cost + task success rate
- **FIFO** — evict oldest block when budget exceeded
- **LRU** — evict least-recently-referenced block
- **Fixed window** — keep last N tokens (StreamingLLM-style)

Measure: token cost, prefix cache hit rate, task success rate (SWE-bench Lite).

**Key question to answer at 75%:** How much does task success rate degrade as you reduce context budget? What is the cost-performance Pareto frontier for naive eviction?

---

## Phase 2 — Core AdaptiveCache (→ 100% milestone)

**Goal:** Implement the layout optimizer + hole-leaving eviction. Beat naive eviction and ReSuM on cost at equal task performance.

### 2.1 Implement the Scoring Pipeline

Build the scoring function from [importance-scoring.md](importance-scoring.md), adding signals incrementally:

**Step 1 (cheapest):** Structural type prior + reference count + importance variance  
**Step 2:** + Cumulative attention (requires attention weight logging)  
**Step 3:** + Dependency graph centrality  

Ablate each signal: measure how much each contributes to prefix cache hit rate.

### 2.2 Implement the Layout Optimizer

Given scored blocks, produce the layout:
```
[sink zone: pos 0–3]
[pinned prefix zone: high importance × stability, ordered by score]
[volatile suffix zone: everything else, newest last]
```

Key implementation decision: **how to handle the "never renumber" constraint with standard LLM APIs.**

Option A — True hole-leaving (requires `cache_edits`-style API): omit evicted blocks from the API request while keeping positional metadata intact. Check if Anthropic's prompt caching supports this.

Option B — Approximation: rebuild the prompt with pinned items at the prefix, omit evicted items entirely, append volatile suffix. This changes byte positions for suffix items but keeps the prefix byte-identical across steps. Measure how much this approximation hurts.

Option C — Placeholder replacement: replace evicted blocks with `[evicted]` token. Cheap but changes the byte sequence, partially invalidating the cache.

**Experiment:** Compare Options A/B/C on prefix cache hit rate. The gap between A and B tells you how much the "never renumber" constraint matters in practice on the Anthropic API.

### 2.3 Measure and Compare

Primary comparison targets for 100% milestone:

| Baseline | Why it matters |
|---|---|
| Full context (ReAct) | Upper bound on task performance; cost baseline |
| FIFO / LRU | Shows AdaptiveCache beats naive eviction |
| ReSuM training-free | Closest modular competitor; +4.5% over ReAct |
| SnapKV | Best KV eviction baseline (prompt-phase) |
| OpenHands condensation | Most deployed production system |

Metrics: token cost, prefix cache hit rate, latency, task success (SWE-bench Lite).

**Target:** Match ReSuM task performance at lower token cost, or exceed ReSuM task performance at equal cost. The prefix cache hit rate should be substantially higher than all baselines.

---

## Phase 3 — Learned Signals (→ 125% milestone)

**Goal:** Push performance further with stronger importance signals. Analyze the tradeoffs.

### 3.1 Expected Attention (training-free)

Implement Expected Attention (arXiv:2510.00636): closed-form estimate of future query attention over existing KV pairs. Directly integrates into the importance scorer as Signal 1.

Measure: does Expected Attention improve layout decisions over cumulative past attention alone?

### 3.2 Low-Entropy Token Protection

Implement ForesightKV's finding: identify low-entropy tokens (model is very confident → these are factual anchors) and boost their importance score. On SWE-bench: the function name, test assertion values, specific error line numbers.

Measure: does protecting low-entropy tokens reduce task performance degradation at aggressive eviction budgets?

### 3.3 Learned Importance Weights (ForesightKV-style)

Collect oracle eviction decisions on SWE-bench training traces:
- For each step, compute the optimal eviction set (minimize task performance drop for a given budget)
- Use as supervision signal for a lightweight scorer trained via pairwise ranking loss
- Apply GRPO to handle edge cases (low-entropy token protection)

This is the 125% stretch goal. Requires SWE-bench training split access.

### 3.4 Compaction Aggressiveness Analysis

Sweep the eviction budget (keep top K% of blocks by score) and measure the cost-performance curve. Key question: where is the knee? The "Stateful KV Cache Management" paper found positional integrity beats token count at high compression ratios — does AdaptiveCache's layout discipline shift this knee significantly?

---

## Full Baseline Table

Ordered by relevance to AdaptiveCache's claims:

| System | Type | Layout-aware | Prefix-cache-aware | Training | Task perf (SWE-bench) |
|---|---|---|---|---|---|
| **Full context (ReAct)** | None | — | No | No | ~43–55% (model-dependent) |
| **FIFO / LRU** | Window eviction | No | No | No | Degrades with budget |
| **StreamingLLM** | Sink + window | No | No | No | Degrades; fast |
| **H2O** | Attention eviction | No | No | No | ~H2O paper baselines |
| **SnapKV** | Prompt-phase eviction | No | No | No | +3–8% over H2O |
| **ReSuM (training-free)** | Plug-in summarizer | No | No | No | +4.5% over ReAct |
| **OpenHands condensation** | LLM summarizer | No | No | No | Production standard |
| **Context Folding + FoldGRPO** | Branch/return | No | Partial | Yes (RL) | 58% SWE-bench Verified |
| **SUPO / Lu et al.** | RL summarization | No | No | Yes (GRPO) | +14% BrowseComp |
| **AdaptiveCache (ours)** | Layout + eviction | **Yes** | **Yes** | No | Target: ≥ ReSuM |

The two most important comparisons:
1. **ReSuM training-free** — same modularity profile (drop-in, no agent retraining), but no layout awareness. Direct apples-to-apples.
2. **Context Folding** — best task performance, but requires RL training. Shows what's achievable with more investment. AdaptiveCache's target: match or approach this without training.

---

## Key Experiments Summary

| Experiment | Answers | Phase |
|---|---|---|
| Prefix cache hit rate on vanilla ReAct traces | Is the baseline really low? | 1 |
| Attention distribution on SWE-bench blocks | Does power-law hold for coding tasks? | 1 |
| Reference count vs attention correlation | Is reference count a valid proxy? | 1 |
| Task success vs eviction budget curve | Where is the performance cliff? | 1–2 |
| Options A/B/C for hole-leaving approximation | How much does renumbering matter? | 2 |
| Ablation: which scoring signals matter most | Justify the 2D framework | 2 |
| Layout optimization vs no layout (same eviction policy) | Isolates the layout contribution | 2 |
| Expected Attention vs cumulative attention | Is closed-form future prediction worth it? | 3 |
| Compaction aggressiveness sweep | Cost-performance Pareto frontier | 3 |
| AdaptiveCache + Context Folding combined | Do coarse + fine grained stack? | 3 |

---

## Open Infrastructure Questions

1. **API access for prefix cache hit rate:** Does the Anthropic API expose per-request cache hit metrics? (`cache_read_input_tokens` in the usage object — yes, this exists). Can we measure this per step in an agent loop?

2. **Attention weight access:** Standard API calls don't return attention weights. Options: (a) use an open-weight model locally (Llama-3, Qwen) where you control the forward pass; (b) proxy with Expected Attention (no attention access needed); (c) use reference counts as a proxy. The 75% milestone should use (c); the 125% milestone needs (a) or (b).

3. **SWE-bench environment setup:** The 75% milestone requires a working SWE-bench agent environment. OpenHands or a custom ReAct loop both work. Budget: ~$X per full eval run at full context prices — AdaptiveCache's whole point is to reduce this.

## Related Pages

- [overview.md](overview.md) — system design
- [importance-scoring.md](importance-scoring.md) — scoring signals
- [kv-cache-architecture.md](kv-cache-architecture.md) — eviction mechanism
- [context-management.md](context-management.md) — full taxonomy of baselines
- [resum.md](resum.md) — primary modular baseline
- [context-folding.md](context-folding.md) — primary performance ceiling baseline
- [snapkv.md](snapkv.md) — best eviction-only baseline
