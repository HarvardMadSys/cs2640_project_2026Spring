---
title: StreamingLLM — Efficient Streaming Language Models with Attention Sinks (Xiao et al., ICLR 2024)
type: source
tags: [kv-cache, eviction, attention, inference-efficiency, long-context, streaming]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Efficient Streaming Language Models with Attention Sinks

**Authors:** Guangxuan Xiao, Yuandong Tian, Beidi Chen, Song Han, Mike Lewis  
**Affiliations:** MIT, Meta AI, Carnegie Mellon University, NVIDIA  
**Date:** ICLR 2024 (submitted September 2023)  
**arXiv:** 2309.17453  
**Source file:** `raw/papers/streamingllm.pdf`

## Problem

Deploying LLMs in streaming applications (multi-round dialogue, continuous operation) requires handling arbitrarily long input sequences. Two failure modes exist:

1. **Dense attention:** O(T²) time and unboundedly growing KV cache — unusable at scale
2. **Window attention (sliding window):** O(TL) and constant memory, but **collapses catastrophically the moment any initial token is evicted** from the cache — even a single-token eviction of the first position spikes perplexity from ~5 to >5000 on Llama-2-13B

The re-computation baseline (rebuild KV for recent L tokens each step) has O(TL²) complexity — too slow for real-time streaming. The core question: why does evicting initial tokens break everything, and can the fix be simple?

## Key Observation: Attention Sinks

**The phenomenon:** LLMs disproportionately attend to initial tokens across all layers and heads, regardless of those tokens' semantic content. This is not about the meaning of the initial tokens — substituting the first four tokens with the linebreak character `\n` restores near-identical perplexity (PPL 5.40 vs 5.60 vs 5158 for no-sink window attention).

**The mechanism:** The SoftMax function requires attention scores to sum to 1 across all attended tokens. Even when the current query has strong self-contained information, the model must allocate attention mass somewhere. Initial tokens are visible to all subsequent tokens (due to autoregressive structure), making them the most globally visible and thus the most "trained" to absorb excess attention. The model learns to use them as receptacles for surplus attention weight — "sinks."

**Quantitative scale:** In Llama-2-7B with sequence length 4096, the first token's attention score often exceeds 0.6–0.8 of the total attention budget in most layers (excluding the bottom two, which show local attention patterns).

**Universality:** Attention sinks appear across decoder models (Llama-2-[7,13,70]B, MPT-[7,30]B, Falcon-[7,40]B, Pythia-[2.9,6.9,12]B), and also in encoder models (BERT uses `[SEP]` as a sink) and Vision Transformers (patch tokens as "registers" — Darcet et al., 2023). This is a property of the SoftMax attention mechanism, not a specific model.

## Proposed Method

**StreamingLLM:** Keep the KV cache split into two parts:
1. **Attention sink tokens** (4 initial tokens): held fixed in the cache, never evicted
2. **Rolling KV cache** (recent L−4 tokens): standard sliding window

When a new token is generated, the oldest non-sink token is evicted. Total cache size stays constant at L.

**Positional encoding:** StreamingLLM uses positions *within the cache* rather than positions in the original text. For a cache containing tokens [0,1,2,3,10,11,12], the positions are assigned [0,1,2,3,4,5,6] — contiguous within the cache. For RoPE models, keys are cached before the rotary transformation is applied, then re-transformed at each decoding step using cache positions. For ALiBi models, the linear bias is applied contiguously.

**Pre-training improvement:** A learnable "sink token" prepended to every training sample can consolidate the sink function into a single token, eliminating the need for 4 initial tokens. With one learned sink token, streaming performance is fully stable with only 1 sink + recent window, versus 4 initial tokens needed in vanilla models. Normal NLP benchmark performance is unaffected by adding the sink token during training.

## Key Results

- **Perplexity:** StreamingLLM matches the oracle baseline (sliding window with recomputation) on 20K-token PG19 text. Window attention collapses; dense attention fails beyond pre-training length.
- **Scale:** Stable language modeling on 4M+ token inputs across Llama-2-[7,13,70]B, Falcon-[7,40]B, Pythia-[2.8,6.9,12]B, MPT-[7,30]B — without any fine-tuning
- **Speed:** Up to 22.2× speedup over sliding window with recomputation (single A6000 GPU, Llama-2-7B/13B), with comparable memory footprint
- **Multi-round QA:** On ARC-[Easy,Challenge] streamed as continuous dialogue, StreamingLLM achieves 71.34% / 55.03% (Llama-2-7B-Chat) vs 3.58% / 1.39% for window attention and OOM for dense — comparable to one-shot per-sample baseline
- **StreamEval:** Custom benchmark querying the model every 10 lines; StreamingLLM maintains accuracy as long as the query-answer distance is within cache size
- **LongBench caveat:** With 4+3496 config, StreamingLLM underperforms truncation baseline on QA/summarization tasks because it loses crucial initial prompt tokens. Setting sink count = 1750 restores performance — showing the limit of naive sink+window for document tasks
- **Ablation (sink count):** 1–2 initial tokens insufficient for most models; 4 generally suffices; diminishing returns beyond 4. Models that did not have a consistent `<s>` starting token (most open models) rely on multiple initial positions as distributed sinks.

## Limitations (stated explicitly in paper)

StreamingLLM does **not** extend the model's context window or provide long-term memory. Accuracy drops to zero when query-answer distance exceeds cache size. Not suitable for long-document QA or summarization requiring access to the full document. Excels specifically in scenarios where only recent context matters (conversational agents, short document QA).

## Relationship to AdaptiveCache

StreamingLLM introduces the concept that is most critical for AdaptiveCache's correctness: **sink tokens are mandatory and must never be evicted.**

**Points of contact:**
- **Sink tokens must be in the prefix:** AdaptiveCache's layout optimization must treat sink tokens (the 4 initial tokens, or 1 learned sink token) as immovable anchors at the very beginning of the prefix. Any eviction or reordering policy that displaces these tokens will catastrophically degrade model performance — the paper proves this with the window attention collapse experiment. This is a hard constraint on AdaptiveCache's compaction step.
- **Sink tokens as "free" prefix cache entries:** From a prefix caching perspective, sink tokens are the ideal prefix cache entries: they are always present, never change, and have the highest attention weights. AdaptiveCache naturally preserves them since they will always rank highest in any attention-signal-based importance score.
- **Decomposition insight:** StreamingLLM shows that the effective KV cache for streaming decomposes into (sink) + (recent). AdaptiveCache proposes a more general decomposition: (stable high-attention tokens, including sinks) + (volatile/recent tokens). AdaptiveCache's stable prefix generalizes StreamingLLM's fixed-4-token sink to include any token that consistently accumulates high attention across steps.
- **Middle-context blind spot:** StreamingLLM explicitly sacrifices all middle-context tokens — everything outside the recent window is gone. AdaptiveCache's H2O-style attention signal can preserve high-value middle-context tokens that StreamingLLM discards. This is AdaptiveCache's core advantage over StreamingLLM for agentic tasks where important early-step observations must be retained.

**Key differences from AdaptiveCache:**
1. **No layout awareness beyond sink+recent:** StreamingLLM's two-component layout is fixed. It does not reason about which non-sink tokens are most valuable for task performance — all non-recent tokens are evicted equally.
2. **Not prefix-cache-preserving for agents:** In an agentic setting where the system prompt, task description, and tool schemas form the stable prefix, StreamingLLM treats these the same as any other early token — keeping only 4 sinks plus recent. This invalidates the semantically meaningful prefix on every agent step.
3. **Token ordering unchanged but context meaningless:** StreamingLLM does not reorder tokens. The 4 sinks are at the front; recent tokens follow. But because middle-context is gone, there is no cross-step prefix cache reuse of agent observations.
4. **Training-free advantage shared:** Like AdaptiveCache, StreamingLLM requires no fine-tuning and works on existing pretrained models.

**AdaptiveCache synthesis:** AdaptiveCache must implement StreamingLLM's constraint as a first-order rule: sink tokens (initial tokens / learned sink token) are always rank-1 for the stable prefix and are never evicted. Beyond that constraint, AdaptiveCache uses H2O-style cumulative attention to promote additional high-value tokens into the prefix, and StreamingLLM's recent-window heuristic for recency. The three components — sinks, H2 heavy hitters, and recent tokens — together define AdaptiveCache's stable prefix composition rule.

## Related Pages / Citations to Follow Up

- [h2o.md](h2o.md) — complementary; H2O tracks cumulative attention to identify heavy hitters; StreamingLLM identifies structural sinks; combining both gives AdaptiveCache's full token importance signal
- [snapkv.md](snapkv.md) — extends the sink/heavy-hitter insight to prompt compression with an observation window
- [prefix-caching.md](prefix-caching.md) — why stable sink + heavy hitter prefix enables prefix KV-cache reuse across agent steps
- [lost-in-middle.md](lost-in-middle.md) — Liu et al. (2023), cited by StreamingLLM; shows that models fail to use middle context — motivates StreamingLLM's design and also provides a counterargument about how much middle context actually matters
- ScissorHands [arXiv:2305.17118] — contemporaneous; similar observation about token importance persistence
