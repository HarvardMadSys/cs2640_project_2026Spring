---
title: Prefix Caching and KV-Cache Reuse
type: concept
tags: [prefix-caching, kv-cache, cost-model, inference-efficiency]
sources: [proposal, context-folding, h2o, streamingllm, snapkv, scissorhands, pyramidkv, lost-in-middle, llmlingua, anthropic-compaction, infini-transformer]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Prefix Caching and KV-Cache Reuse

The foundational cost model behind [AdaptiveCache](overview.md).

## What is Prefix Caching?

In transformer inference, each token's attention requires key-value (KV) pairs from all preceding tokens. Computing these KV states is expensive. **Prefix caching** caches these computed KV states so that if the same prefix appears in a subsequent request, the computation is skipped.

**The rule:** the prefix must be byte-for-byte identical to get a cache hit. Any modification to the prefix — inserting, deleting, or reordering tokens — invalidates all downstream cached computation.

## Cost Asymmetry

This creates a strongly non-uniform cost model:

| Operation | Cost |
|---|---|
| Token in cached prefix | Very cheap (KV lookup) |
| Token beyond cached prefix (new suffix) | Full attention computation |
| Modifying early prefix tokens | Invalidates all downstream cache → very expensive |
| Appending to suffix | Cheap — existing prefix cache is preserved |

Empirically, cached tokens can be **~10× cheaper** than uncached tokens on systems like vLLM, SGLang, or Anthropic's API.

## Why This Matters for Agent Context Management

In an agent loop, the context grows with each step: system prompt + few-shot examples + conversation history + tool outputs. Across steps, the early portions of the context (system prompt, stable instructions) are identical — these get cached. But if the agent reorders or modifies items in the middle of the context, it destroys the cache.

**Key implication for AdaptiveCache:** item *position* in the context is a cost variable, not just item *presence*. This is the insight that existing context management approaches miss:

- LRU/FIFO: decides what to remove, but ignores ordering
- Summarization (ReSuM, SUPO, Anthropic compaction): generates new summary tokens — the resulting byte sequence has never appeared before, invalidating all downstream prefix KV-cache state
- Context Folding: uses KV rollback on branch return, but doesn't explicitly optimize the main-thread layout
- KV-cache eviction (H2O, StreamingLLM, ScissorHands, SnapKV, PyramidKV): selects which tokens to keep, but surviving tokens remain scattered at their original positions; different queries produce different scatter patterns, so no two consecutive calls share a common prefix
- Token-level prompt compression (LLMLingua, LongLLMLingua): produces a compressed byte sequence from scratch for each compression event; token boundaries shift; the output is always a new sequence unrecognizable by any prefix cache
- Architectural compressive memory (Infini-Transformer): the compressive memory matrix is a running recurrent state that changes every segment; not a byte-for-byte reproducible prefix sequence

## The Layout Gap

All existing eviction and compression methods share the same structural omission: they answer "what to keep" but not "where to put what survives." Layout is treated as a side effect — surviving tokens stay wherever they were, in whatever positions remain after eviction. This has two compounding costs:

**Computational cost:** A KV-cache serving system (vLLM, SGLang, Anthropic API prompt caching) can only reuse cached computation when the current request's prefix is byte-for-byte identical to a cached prefix. If different queries evict different token sets, the surviving token arrangements differ between calls, eliminating the possibility of prefix cache hits on the dynamic conversation portion. Only the immutable system prompt remains cacheable.

**Behavioral cost:** Liu et al. (Lost in the Middle, 2023) demonstrate that even when the right tokens survive eviction, their position determines whether the model uses them. Tokens in the middle of a long context are attended to far less reliably than tokens at the beginning (primacy bias) or end (recency bias). An eviction method that keeps the correct tokens but leaves them in mid-context positions loses much of the benefit — the model may fail to attend to them anyway. AdaptiveCache's layout step ensures surviving tokens land in the primacy zone (stable prefix at the beginning) or recency zone (volatile suffix at the end), exploiting both computational and behavioral advantages of position.

**The specific papers and their layout gap:**
- **StreamingLLM:** Keeps sink tokens + rolling recent window. Sinks are at position 0–3 (optimal for primacy). But mid-context tokens between sinks and the recent window are dropped entirely, and the recent window tokens have only recency bias, not primacy. No cross-call prefix reuse for the dynamic portion.
- **H2O:** Heavy hitters are selected by cumulative attention. Their original positions are scattered — a top-attended token might be at position 5,000 in a 20,000-token context, landing in the middle of the compressed context. No layout step moves it to the prefix.
- **ScissorHands:** Same gap as H2O. Persistence of importance holds, but the positions of persistent tokens are wherever they started.
- **SnapKV:** Selects top-k positions per head. Each head selects a different, query-dependent set. Two consecutive calls with nearly identical prompts produce different per-head selections — zero prefix cache overlap.
- **PyramidKV:** Inherits SnapKV's gap and adds layer-dimension variation. Different layers retain different positions; the combined effective context is a layer-specific irregular selection with no stable byte-for-byte reproducibility.
- **LLMLingua / LongLLMLingua:** Produces a compressed prompt as a new string — tokens are dropped mid-word in some cases, producing a byte sequence that has no prefix relationship with any prior call.
- **Anthropic compaction:** Generates a fresh natural-language summary. The summary is a new byte sequence every time — no prefix cache hit possible for the conversation portion.

AdaptiveCache's central bet: **optimizing layout for prefix stability is a major lever on token cost that has not been exploited.**

## Layout Strategy

Given the prefix-caching cost model, the optimal layout places items in decreasing order of stability:

```
[System prompt] → [Stable reference material] → [Semi-stable context] → [Volatile / recent] 
← cached across steps                                                      ← evicted first →
```

Stable items (function signatures, API docs, high-attention tokens) go early. Recent tool outputs and volatile state go late. When the volatile suffix is truncated or evicted, the stable prefix remains cached.

## Related Concepts

- [context-management.md](context-management.md) — taxonomy of approaches; layout gap appears in every category
- [overview.md](overview.md) — how AdaptiveCache exploits this
- [lost-in-middle.md](lost-in-middle.md) — behavioral evidence that position determines utilization, not just presence; primacy/recency bias
- [h2o.md](h2o.md) — heavy-hitter eviction without layout step
- [streamingllm.md](streamingllm.md) — attention sinks at position 0–3 (primacy zone), recent window (recency zone); the rest dropped
- [snapkv.md](snapkv.md) — observation window selection without layout step; per-head scatter
- [scissorhands.md](scissorhands.md) — persistence of importance without layout step
- [pyramidkv.md](pyramidkv.md) — per-layer variable budget without layout step
- [llmlingua.md](llmlingua.md) — token-level compression producing prefix-invalidating output
- [anthropic-compaction.md](anthropic-compaction.md) — production API compaction; system prompt cached but conversation portion is not
- [infini-transformer.md](infini-transformer.md) — compressive memory alternative; also not prefix-cacheable
- [context-folding.md](context-folding.md) — Context Folding's partial use of KV rollback
- [system-design.md](system-design.md) — formal cost model and three-zone layout exploiting prefix caching
