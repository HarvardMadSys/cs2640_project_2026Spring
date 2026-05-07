---
title: H2O — Heavy-Hitter Oracle (Zhang et al., NeurIPS 2023)
type: source
tags: [kv-cache, eviction, attention, inference-efficiency, long-context]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models

**Authors:** Zhenyu Zhang, Ying Sheng, Tianyi Zhou, Tianlong Chen, Lianmin Zheng, Ruisi Cai, Zhao Song, Yuandong Tian, Christopher Ré, Clark Barrett, Zhangyang Wang, Beidi Chen  
**Date:** NeurIPS 2023 (submitted June 2023, revised December 2023)  
**arXiv:** 2306.14048  
**Source file:** `raw/papers/h2o.pdf`

## Problem

KV cache grows linearly with sequence length and batch size, creating severe memory bottlenecks for LLM inference. A 30B model with batch size 128 and sequence length 1024 requires 180GB of KV cache alone. Prior approaches (Reformer, FlashAttention, multi-query attention) reduce quadratic attention cost during training but don't solve the KV cache memory footprint during decoding. The core challenge: can KV cache be truncated without destroying generation quality?

## Proposed Method / Key Idea

**Key observation:** Attention matrices in pre-trained LLMs are >95% sparse at inference time. Accumulated attention scores across all tokens follow a power-law distribution — a small subset of "heavy hitter" (H2) tokens receive disproportionately large cumulative attention.

**H2O eviction policy:** Maintain a fixed-size KV cache budget split evenly between:
1. **Heavy Hitters (H2):** tokens with high accumulated attention scores (tracked via running sum)
2. **Recent tokens:** the most recent K entries (recency bias for local coherence)

At each decoding step, when the cache is full and a new token is added, evict the token with the lowest cumulative attention score (excluding the newly-added token). This is formulated as a dynamic submodular maximization problem, with a theoretical guarantee of `(1 - 1/e)(1 - α)·opt - β` approximation.

**Key insight from ablations:** Using local statistics (summing attention scores of only preceding tokens) is as effective as using global statistics (requiring future tokens). This makes H2O feasible at inference time.

**System implementation:** Maintains two separate circular buffers — one for H2 tokens (fixed positions), one for recent tokens (rolling). No memory swapping; newly-added KV directly replaces evicted entries. Built on top of FlexGen.

## Key Results

- **Memory:** 5-10× KV cache reduction with comparable accuracy across OPT, LLaMA, GPT-NeoX (6.7B–175B) on 8 HELM and lm-eval-harness tasks
- **Throughput:** 29× over DeepSpeed Zero-Inference, 29× over HuggingFace Accelerate, 3× over FlexGen with 20% KV budget (T4 GPU)
- **Latency:** 1.1–1.9× lower latency at same batch size vs FlexGen (A100, 4K–10K sequences)
- **Infinite-length streaming:** H2O extended with position rolling handles 4M+ token inputs, outperforming StreamingLLM in perplexity
- **Compatibility:** Combines multiplicatively with 4-bit quantization
- **Ablation (Q4):** Neither H2 alone nor recency alone maintains quality; combining both is necessary. H2 contributes more than recency alone.
- **Diversity bonus:** H2O slightly increases text generation diversity (lower Self-BLEU)

H2 tokens show strong correlation with token co-occurrence frequency in training data, suggesting they capture frequent syntactic/semantic anchors.

## Relationship to AdaptiveCache

H2O is the foundational paper for attention-signal-based KV cache eviction — directly relevant to AdaptiveCache's 125% milestone which proposes using attention signals to identify high-value tokens.

**Points of contact:**
- H2O's core insight (accumulated attention scores as a proxy for token importance) is exactly the signal AdaptiveCache proposes to use for its "identify reusable items" step
- H2O's observation that H2 tokens correlate with co-occurrence frequency suggests H2 tokens are structurally stable — aligning with AdaptiveCache's hypothesis that stable, frequently-attended tokens belong in the prefix
- H2O proves a theoretical near-optimality guarantee for greedy attention-based eviction under submodularity assumptions — provides theoretical grounding for AdaptiveCache's attention signal

**Key differences from AdaptiveCache:**
1. **No layout awareness:** H2O evicts tokens from a flat budget. It does not reason about prefix vs. suffix position. Evicted tokens are simply dropped; surviving tokens remain in their original positions. H2O cannot optimize prefix cache reuse across steps.
2. **Applied during generation, not between agent steps:** H2O operates token-by-token during the decoding phase of a single generation. AdaptiveCache operates at the inter-step boundary, reorganizing multi-turn conversation context before the next LLM call.
3. **Not prefix-cache-preserving:** H2O modifies which tokens are present in the KV cache (by eviction), which can invalidate downstream prefix caches in serving systems that rely on prompt prefix identity. AdaptiveCache preserves the stable prefix.
4. **No training required, but single-session only:** H2O is training-free like AdaptiveCache, but it doesn't persist state across separate API calls with prefix caching.

**AdaptiveCache synthesis:** AdaptiveCache can treat H2O's accumulated-attention eviction score as its signal for "volatile vs. stable" item classification. Items with consistently high H2 scores get promoted toward the prefix; items with low scores get demoted to the suffix for eviction. The key AdaptiveCache contribution beyond H2O is the layout optimization step — ensuring that surviving tokens are placed in prefix-cache-preserving order.

## Related Pages / Citations to Follow Up

- [prefix-caching.md](prefix-caching.md) — why layout matters beyond just which tokens survive
- [snapkv.md](snapkv.md) — extends H2O to prompt-phase KV compression with observation windows
- [streamingllm.md](streamingllm.md) — contemporaneous; H2O explicitly compares to and outperforms StreamingLLM on summarization tasks
- [scissorhands.md](scissorhands.md) — contemporaneous; "persistence of importance" hypothesis, similar to H2
- [pyramidkv.md](pyramidkv.md) — extends with per-layer budget allocation
- ScissorHands [arXiv:2305.17118] — cited in SnapKV as prior work
- FastGen / Adaptive KV Compression [arXiv:2310.01801] — cited in SnapKV as follow-up
