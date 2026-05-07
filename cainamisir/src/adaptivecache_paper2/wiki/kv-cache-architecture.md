---
title: KV Cache Architecture — Non-Contiguous Eviction and Positional Encoding
type: concept
tags: [kv-cache, prefix-caching, positional-encoding, rope, eviction, pagedattention, inference-efficiency]
sources: [h2o, snapkv, scissorhands, streamingllm, claude-code-compaction, pyramidkv]
date_created: 2026-04-05
date_updated: 2026-04-05
---

# KV Cache Architecture: Non-Contiguous Eviction and Positional Encoding

The key technical question for AdaptiveCache: **can you evict tokens from the middle of a cached KV sequence without recomputing downstream states?** The literature says yes — under specific conditions — and this has significant design implications.

## The Apparent Problem

Standard prefix caching works as a trie. KV states for token at position `t` are computed by attending over all positions `0...t-1`. If you delete a token at position 500 in a 2,000-token sequence, every KV state from 501 onward was computed attending to position 500. Delete it and those states seem stale.

## Why It's Solvable: Post-Rotated Keys

Modern LLMs (Llama, Claude, GPT-4) use **Rotary Positional Embeddings (RoPE)**, which encode position by rotating the query and key vectors. The critical implementation detail:

> SnapKV, R-KV, and most production systems cache **post-rotated keys** — the position rotation is applied at compute time and baked into the stored key tensor.

This means:
- A key at position 1,100 is stored with position-1,100's rotation already applied
- If positions 1,000–1,099 are deleted from the cache, position 1,100 **stays at position 1,100**
- A future token at position 2,001 attending to position 1,100 computes the correct relative distance: `2001 − 1100 = 901`
- The evicted positions become **holes** — not attendable, but not scrambling anything
- **No downstream KV states need recomputation**

## The Critical Constraint: Don't Renumber

The failure mode is **compaction after eviction** — renumbering the surviving tokens to close the gaps:

```
Evict positions 1000–1099, then renumber:
  Old: [0...999][1100...2000]  → positions intact, fine
  Bad: [0...999][1000...1900]  → positions shifted, RoPE scrambled
```

Renumbering makes adjacent KV states have wildly mismatched original positional indices, creating contradictory signals for attention heads. The paper "Stateful KV Cache Management" (arXiv:2511.04686) demonstrates this empirically:

> A simple strategy retaining the first 2,000 **contiguous** tokens outperformed sophisticated attention-based eviction retaining 99% of tokens from a longer context. Positional integrity matters more than token count.

**Rule: evict by leaving holes, never by renumbering.**

## Production Evidence

**PagedEviction** (arXiv:2509.04377, EACL 2026) implements exactly this in vLLM:
- Stores KV cache in non-contiguous physical memory pages (PagedAttention)
- Evicts entire pages based on aggregated importance scores
- Integrates without modifying CUDA attention kernels — downstream states remain valid
- 3.1× throughput over full cache with <3–5% accuracy degradation on LongBench

**Claude Code's cached microcompact** (`cache_edits`):
- Sends cache deletion instructions to the Anthropic API for specific tool result blocks
- Local messages unchanged — those positions become holes in the server's KV cache
- The rest of the cached prefix stays valid
- This is hole-leaving eviction, not summarization

**H2O, SnapKV, ScissorHands** — all evict KV states from the middle of the sequence, leaving positional gaps, and the model skips those positions during attention. This is the standard approach in KV cache eviction literature.

## Block Coherence Matters

Evict **logically coherent units** — tool calls, conversation turns, complete tool results — not scattered individual tokens. Partial eviction of a logical unit creates positional gaps mid-unit that the model may find more disorienting than evicting the whole unit. PagedEviction aligns eviction to vLLM's page boundaries for exactly this reason.

## The Two Distinct Cache Mechanisms

It's important to distinguish two different things that are both called "prefix caching":

| Mechanism | What it does | Relevant for |
|---|---|---|
| **KV eviction** | Remove specific cached states from the KV store; future tokens don't attend to those positions | Reducing memory / token cost within a single session |
| **Prefix cache hit** | Reuse the entire computed KV prefix from a previous request because the byte sequence matches | Reducing cost across requests / steps |

AdaptiveCache targets both. Layout optimization primarily addresses **prefix cache hits across steps**. Hole-leaving eviction addresses **KV memory within a step**. They are complementary mechanisms.

## Implications for AdaptiveCache Design

See [overview.md](overview.md) for the refined system design. Key takeaways:

1. **In-place eviction is free** — the eviction mechanism doesn't require generating new tokens or recomputing downstream states. This validates AdaptiveCache's "no new token generation" design.

2. **Layout optimization is still necessary** — but its purpose is specifically to ensure cross-step prefix cache hits, not to make eviction cheap (eviction is already cheap via hole-leaving).

3. **Operate at block granularity** — align eviction to logical units (tool calls, turns), not individual tokens.

4. **Commit to absolute positions** — once an item is placed at a position, it stays there or becomes a hole. Never renumber.

5. **The stable prefix is a pinned zone** — items promoted to the prefix are pinned (same absolute positions, step after step). Items in the suffix zone are hole-evicted as needed.

## Related Pages

- [prefix-caching.md](prefix-caching.md) — the cost model; layout gap
- [h2o.md](h2o.md) — attention-based eviction in practice
- [snapkv.md](snapkv.md) — post-rotated key caching; observation window
- [claude-code-compaction.md](claude-code-compaction.md) — `cache_edits` as production hole-leaving eviction
- [overview.md](overview.md) — AdaptiveCache refined design
- [system-design.md](system-design.md) — full eviction engine and layout optimizer design using these principles
