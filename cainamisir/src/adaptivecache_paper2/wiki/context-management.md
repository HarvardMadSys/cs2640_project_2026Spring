---
title: Context Management — Taxonomy
type: concept
tags: [context-management, taxonomy, eviction, summarization, multi-agent, kv-cache, prompt-compression]
sources: [context-folding, react, summarization-rl, mem1, memagent, resum, h2o, streamingllm, snapkv, scissorhands, pyramidkv, lost-in-middle, llmlingua, infini-transformer, anthropic-compaction, proposal]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Context Management: Taxonomy of Approaches

A structured overview of the space AdaptiveCache operates in.

## The Problem

LLM agents accumulate context linearly: each step appends reasoning, tool calls, and observations. At some point the context exceeds the model's working window, or becomes too expensive. The question: **what to keep, what to discard, and where to put it?**

Existing approaches answer "what to keep/discard" but not "where to put it." AdaptiveCache addresses both.

## Taxonomy

### 1. Full Context (ReAct baseline)
Keep everything. No management. Works until the context window fills.
- Pro: no information loss, simple
- Con: quadratic attention cost, KV-cache grows unboundedly, hits context limit on long-horizon tasks
- Example: ReAct (Yao et al., 2022)
- See: [react.md](react.md)

### 2. Window-Based Eviction (LRU / FIFO)
Slide a window over the context, discarding oldest or least-recently-accessed items.
- Pro: simple, no training
- Con: recency bias may discard important early context; not layout-aware; not task-aware
- Status: baseline for AdaptiveCache (75% milestone)

### 3. Retrieval-Based Pruning
Keep a compressed representation; retrieve relevant chunks at query time. Closest to RAG.
- Pro: scales to large histories
- Con: retrieval latency; not prefix-cache-aware; discards items that may become relevant
- Status: baseline for AdaptiveCache

### 4. Summarization-Based Compression
When context fills, trigger an LLM summarization pass. Replace detail with summary.
- Pro: preserves semantic content in fewer tokens
- Con: disruptive (breaks reasoning flow at arbitrary points); irreversible information loss; not layout-aware; **invalidates prefix KV-cache** on every summarization event (new summary tokens have no cached KV state)
- Examples: OpenHands context condensation, ReSuM (Wu et al., 2025 — plug-and-play external summarizer), SUPO (Lu et al., 2025 — RL-trained summarization policy)
- See also: [context-folding.md](context-folding.md) positions itself as a structured alternative to this; [resum.md](resum.md); [summarization-rl.md](summarization-rl.md)

### 4a. RL-Trained Summarization Policy
Variant of summarization where the summarization decision and content are learned via RL from task outcomes.
- Pro: learns what to preserve for downstream task performance; jointly optimizes task + compression
- Con: requires RL training on task-specific rollouts; still invalidates prefix KV-cache; coarse-grained (session-level)
- Examples: SUPO / Lu et al. (2025) — summarization-augmented MDP with GRPO training
- See: [summarization-rl.md](summarization-rl.md)

### 4b. Constant-Memory via Internal State Replacement
Each turn, the model generates a new internal state block that replaces all prior context; memory is constant size regardless of trajectory length.
- Pro: O(1) peak memory; scales to arbitrarily long trajectories
- Con: requires RL training (PPO); new internal state text invalidates prefix KV-cache each turn; lossy (model chooses what to remember); must be trained — training-free application hurts performance
- Example: MEM1 (Zhou et al., 2025) — `<IS>` internal state replaced each turn; 2D attention mask for multi-turn training
- See: [mem1.md](mem1.md)

### 4c. Chunk-by-Chunk Processing with Overwrite Memory
Process long input in fixed-size chunks; maintain a fixed-size memory block overwritten after each chunk.
- Pro: linear O(N) complexity; extrapolates far beyond training context length
- Con: requires RL training (Multi-Conv DAPO); memory overwrite invalidates cross-chunk prefix caching; designed for static document reading, less suited to dynamic agentic loops
- Example: MemAgent (Yu et al., 2025) — 1024-token memory, 5000-token chunk, 8K total; trained on 32K, extrapolates to 3.5M
- See: [memagent.md](memagent.md)

### 4d. Automated Server-Side Compaction
Variant where the API provider handles summarization automatically when a token threshold is exceeded, triggered per-request without any client-side logic beyond enabling the feature.
- Pro: zero integration work; supports prompt caching for system prompts; streaming compatible
- Con: generates new summary tokens on every compaction event, invalidating conversation prefix KV-cache; lossy; reactive (only fires when threshold exceeded, not proactively each step); costs additional inference for the summary generation pass
- Example: Anthropic `compact-2026-01-12` beta — triggers at 150K tokens by default; system prompt preserved; `pause_after_compaction` hook available
- See: [anthropic-compaction.md](anthropic-compaction.md)

### 4e. Token-Level Prompt Compression
Selectively drop individual tokens (not whole turns) from the prompt before sending to the LLM. Uses a small compressor model to assess token importance via perplexity. Produces a compressed prompt with fewer tokens but grammatically distorted text that LLMs can parse but humans cannot.
- Pro: extreme compression ratios (up to 20×); no LLM fine-tuning required; works with black-box APIs; 70–94% cost reductions demonstrated
- Con: produces prefix-invalidating output (compressed byte sequence is always new); requires separate small model (~7B) for perplexity scoring; grammatically incoherent (cannot be used as agent memory humans inspect); per-query compression cannot be cached for reuse across different queries
- Examples: LLMLingua (EMNLP 2023) — coarse-to-fine compression with budget controller + ITPC + distribution alignment; LongLLMLingua (2024) — question-aware extension with contrastive perplexity, document reordering, dynamic ratios
- See: [llmlingua.md](llmlingua.md)

### 5. KV-Cache Eviction (Serving-Layer Compression)
Keep all prompt tokens in the context but selectively discard KV entries during the forward pass or between inference calls. The model's effective context is shorter due to missing KV states, but the original tokens are not dropped from the request. Methods differ in how they identify which KV pairs to evict.

**Common structural gap:** All eviction methods decide WHAT to keep but not WHERE surviving tokens sit. Surviving KVs remain scattered at their original positions — a different scatter pattern per query. No two consecutive calls share a common prefix, so standard prefix KV-cache reuse is impossible. AdaptiveCache adds the layout step that eviction methods lack.

- **Attention sink + rolling window (StreamingLLM, 2023):** Keep 4 initial "sink" tokens + a rolling window of recent tokens. Initial tokens are empirically essential due to attention sink behavior; StreamingLLM enables infinite streaming generation at 22.2× speedup. Gap: drops all middle tokens regardless of importance.
- **Heavy-hitter eviction (H2O, 2023):** Maintain a "heavy hitter" set of tokens with highest cumulative attention scores via greedy submodular eviction, plus a recent window. Power-law attention distribution means a small fraction of tokens captures most attention mass. Gap: uniform budget per layer; no layout step.
- **Persistence-of-importance eviction (ScissorHands, NeurIPS 2023):** History window tracks which tokens have been consistently unimportant (below-average attention); evicts highest-count losers. >95% persistence ratio proven empirically. Gap: operates per-generation only; no cross-call persistence.
- **Observation-window prompt compression (SnapKV, 2024):** Uses last Lobs tokens of prompt as a query to score all prefix positions before generation begins. Per-head top-k selection; 1D max pooling for cluster coherence. Fills gap H2O leaves (prompt-phase compression). Gap: no layout step; per-query selection invalidates prefix caching.
- **Per-layer variable budget (PyramidKV, 2024):** Attention entropy decreases monotonically from lower to upper transformer layers. Allocates larger KV budgets to lower layers (high entropy) and smaller to upper layers (low entropy). Uses SnapKV's observation window within each layer. Gap: orthogonal to layout; still no cross-step stable prefix.

See: [h2o.md](h2o.md), [streamingllm.md](streamingllm.md), [scissorhands.md](scissorhands.md), [snapkv.md](snapkv.md), [pyramidkv.md](pyramidkv.md)

### 5a. Architectural Compressive Memory
Modify the transformer attention mechanism to maintain a bounded-size associative memory matrix updated at each segment boundary. New KV bindings are compressed into the matrix via outer-product associative updates, enabling unbounded context in constant memory.
- Pro: truly infinite context in O(1) memory; 100% passkey accuracy at 1M tokens; no position-bias (query-driven retrieval, not position-indexed); 114× compression vs. Memorizing Transformers
- Con: requires architectural modification (cannot use existing LLMs); requires at least continual pre-training; lossy (matrix rank saturation causes forgetting); recurrent memory state not prefix-cacheable
- Example: Infini-Transformer / Infini-attention (Munkhdalai et al., Google 2024) — compressive memory matrix + local dot-product attention + learned gating scalar β per head; delta rule for error-correction updates
- See: [infini-transformer.md](infini-transformer.md)

### 6. Multi-Agent Distribution
Route subtasks to specialized agents with focused context windows. Each agent sees only relevant history.
- Pro: scales naturally; specialized agents can be fine-tuned
- Con: requires handcrafted routing workflows; no end-to-end optimization; inter-agent communication overhead
- Examples: Anthropic multi-agent research system (2025)

### 7. Context Folding (branch/return)
Agent actively manages context by branching into sub-trajectories and folding them upon completion, retaining only a summary.
- Pro: preserves reasoning continuity; learnable via RL; strong empirical results
- Con: requires RL training (FoldGRPO); coarse-grained (sub-trajectory level, not token level); not layout-aware
- See: [context-folding.md](context-folding.md)

### 8. AdaptiveCache (proposed — layout + eviction)
Online compaction pass at each step: identify high-value items via attention + heuristics, promote to prefix, demote volatile items to suffix for eviction. Explicit layout optimization for prefix cache reuse.
- Pro: training-free; directly optimizes prefix cache hit rate; fine-grained (item level)
- Con: unproven; coupling between eviction and layout decisions is a key challenge; attention signals are noisy
- See: [overview.md](overview.md)

## Comparison Matrix

| Approach | Layout-aware | Prefix-cache-aware | Training needed | Granularity | Online |
|---|---|---|---|---|---|
| Full context (ReAct) | — | No | No | — | — |
| LRU / FIFO | No | No | No | Token | Yes |
| Retrieval pruning | No | No | No | Chunk | Yes |
| Summarization (generic) | No | No | Optional | Turn/session | No (post-hoc) |
| SUPO / Lu et al. (2025) | No | No | Yes (GRPO-RL) | Session | No (post-hoc) |
| ReSuM / Wu et al. (2025) | No | No | Optional (RL for +gains) | Turn/session | No (post-hoc) |
| MEM1 / Zhou et al. (2025) | No | No | Yes (PPO) | Turn | No (post-hoc) |
| MemAgent / Yu et al. (2025) | No | No | Yes (Multi-Conv DAPO) | Chunk | No (post-hoc) |
| Anthropic compaction (2026) | No | No | No | Turn/session | No (threshold-triggered) |
| LLMLingua / LongLLMLingua | No | No | No | Token | No (per-query) |
| StreamingLLM (2023) | No | No | No | Token (sink+recent) | Yes |
| H2O (2023) | No | No | No | Token | Yes |
| ScissorHands (NeurIPS 2023) | No | No | No | Token | Yes (per-gen) |
| SnapKV (2024) | No | No | No | Token (per-head) | No (per-query prompt) |
| PyramidKV (2024) | No | No | No | Token (per-layer) | No (per-query prompt) |
| Infini-Transformer (2024) | No | No | Yes (continual pre-train) | Segment (matrix) | Yes |
| Multi-agent | No | No | No | Task | No |
| Context Folding | No | Partially | Yes (RL) | Sub-trajectory | Yes |
| **AdaptiveCache** | **Yes** | **Yes** | **No** | **Item (token)** | **Yes** |

## Key Open Question

Can layout optimization and eviction decisions be decoupled, or must they be solved jointly? AdaptiveCache's hypothesis: they must be solved jointly, because the cost of eviction depends on the position of what remains after eviction.

## Key Insight: The Layout Gap

All approaches in categories 1–7 share a structural omission: they decide WHAT to keep but not WHERE to place what survives. Even the eviction methods that correctly identify the most important tokens leave them scattered at their original positions. A scattered surviving set cannot be prefix-cached — each query produces a different scatter, so no two consecutive calls share a common prefix. Lost in the Middle (Liu et al., 2023) further shows that tokens in middle positions are attended to significantly less reliably than tokens at the beginning or end, so scattered survivors also suffer behavioral degradation. AdaptiveCache is the only approach in this taxonomy that addresses both the computational and behavioral consequences of token position.

## Related Pages

- [prefix-caching.md](prefix-caching.md) — the cost model underlying layout-aware approaches
- [lost-in-middle.md](lost-in-middle.md) — behavioral evidence that position matters, not just presence
- [h2o.md](h2o.md), [streamingllm.md](streamingllm.md), [snapkv.md](snapkv.md), [scissorhands.md](scissorhands.md), [pyramidkv.md](pyramidkv.md) — KV-cache eviction cluster
- [llmlingua.md](llmlingua.md) — token-level prompt compression
- [infini-transformer.md](infini-transformer.md) — architectural compressive memory
- [anthropic-compaction.md](anthropic-compaction.md) — production API compaction baseline
- [context-folding.md](context-folding.md) — most relevant prior work for RL-based approach
- [overview.md](overview.md) — AdaptiveCache proposal
