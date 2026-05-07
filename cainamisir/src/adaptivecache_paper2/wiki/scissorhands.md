---
title: ScissorHands — Exploiting the Persistence of Importance Hypothesis for LLM KV Cache Compression (Liu et al., NeurIPS 2023)
type: source
tags: [kv-cache, eviction, attention, inference-efficiency, long-context]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# ScissorHands: Exploiting the Persistence of Importance Hypothesis for LLM KV Cache Compression at Test Time

**Authors:** Zichang Liu, Aditya Desai, Fangshuo Liao, Weitao Wang, Victor Xie, Zhaozhuo Xu, Anastasios Kyrillidis, Anshumali Shrivastava  
**Affiliation:** Rice University  
**Date:** NeurIPS 2023 (submitted May 2023, revised August 2023)  
**arXiv:** 2305.17118  
**Source file:** `raw/papers/scissorhands.pdf`

## Problem

KV cache memory at inference time can dwarf model weights by 3–5×. OPT-175B at batch size 128 and sequence length 2048 requires 1,152 GB for KV cache vs. 325 GB for weights. Prior quantization and pruning approaches address weight size but not KV cache sequence-length scaling. The problem: can KV cache be reduced in the sequence-length dimension at inference time, without fine-tuning?

## Key Observation: Repetitive Attention Pattern

**The observation:** When visualizing attention maps at three different token positions (178, 228, 278) in a sentence, the same sparse set of positions (27, 63, 98, 121, 152, 177 in the example) receive high attention scores from all three positions. The pattern repeats — the same tokens are consistently important across different query positions.

**Persistence of Importance Hypothesis:** "Only pivotal tokens, which had a substantial influence at one previous step, will have a significant influence at a future step."

**Empirical verification (persistence ratio):** Define S_{0→t} as tokens in the first half of a sentence that received high attention from any position in the first half. Define S_{t+1→l} as tokens that receive high attention from the second half. The persistence ratio = |S_{t+1→l} ∩ S_{0→t}| / |S_{t+1→l}|. Across OPT-6B, 13B, 30B, 66B: persistence ratio exceeds 95% in most transformer layers. The pivotal token set from the first half covers virtually all pivotal tokens used in the second half. S_{0→t} is also substantially smaller than the full sequence length — confirming this is not trivially the case where all tokens matter.

**Theoretical grounding:** For a single-layer transformer with skip connections, the attention score α_{t,l} (position l's influence on token t) propagates to α_{t+1,l} with bounded perturbation, because MLP skip connections keep the input and output cosine-similar (empirically >0.9 for all layers). A token important now remains important later because the next token's representation is close to the current one's, so the key-query product barely changes.

**Critical fact:** The repetitive pattern does not exist in randomly initialized models — it is learned during training, not an architectural artifact.

## Proposed Method

**ScissorHands algorithm:**

1. Maintain a KV cache budget B (number of tokens).
2. During generation, track token importance using a **history window** of the last w recent tokens (w=400 in experiments): for each token in the history window, count how often it scored below the average attention threshold (1/t). An importance record I counts low-score events.
3. Always preserve the **r most recent tokens** (r=10) regardless of importance, since they lack enough history.
4. When cache exceeds B: drop the m tokens with the highest "low-score count" in I — i.e., tokens that have most consistently been unimportant in recent steps. Keep only the top-B survivors.
5. Budget allocation across layers: allocate more budget to later layers (where persistence ratio dips lower) to compensate.

**Efficient overhead:** Compression is triggered only periodically (every B/2 steps), not at every generation step. The compressed cache reduces subsequent attention computation, partially offsetting the compression cost.

**Theoretical guarantee:** Theorem 4.1 bounds the expected error between tokens generated with compressed vs. full KV cache. The bound scales with (1 - B/T_max) — when B = T_max (no compression), error is zero. Tighter bounds for stronger power-law attention distributions.

## Key Results

- **KV cache reduction:** Up to 5× compression with no accuracy drop on OPT-[6B, 13B, 30B, 66B] on language modeling (C4) and few-shot tasks (Hellaswag, MathQA, PIQA, Winogrande)
- **Scaling with model size:** Larger models tolerate more compression without accuracy loss — OPT-66B maintains perplexity until 75% original KV cache size vs. 50% for OPT-6B. Encouraging for production deployment.
- **4-bit quantization compatibility:** Combining ScissorHands (2×) + 4-bit quantization does not introduce compounded errors; Hellaswag accuracy maintained at 0.704 vs. 0.702 baseline for OPT-6B.
- **Attention score fidelity:** ScissorHands-compressed attention scores are nearly identical to full-cache scores (change ratio centered at 0), confirming the algorithm retains the high-scoring tokens.

## Limitations

ScissorHands focuses exclusively on the decoding-phase KV cache. It does not address prompt KV compression (this gap was later identified by SnapKV). The history window lookback window is a hyperparameter (w=400) that adds slight memory overhead if tracking is maintained during generation rather than recomputed at compression events. Evaluation was limited to OPT models due to compute constraints.

## Relationship to AdaptiveCache

ScissorHands provides the foundational hypothesis — persistence of importance — that underlies AdaptiveCache's entire design.

**Points of contact:**
- **Persistence of importance = AdaptiveCache's stable prefix hypothesis:** ScissorHands proves empirically (>95% persistence ratio) and theoretically (Theorem 3.1, Theorem 4.1) that important tokens remain important across future steps. This is exactly AdaptiveCache's premise: tokens that have been consistently attended to across prior agent steps belong in the stable prefix. AdaptiveCache's "promote to prefix" decision is justified by the same empirical regularity ScissorHands quantifies.
- **History window concept:** ScissorHands uses a history window (last w tokens) to measure recent importance. AdaptiveCache's inter-step compaction uses the most recent observation (last agent step's output) as the query window for measuring which prior context tokens are worth promoting — a direct operational analog.
- **Recency as separate signal:** ScissorHands always keeps the r most recent tokens regardless of importance score. AdaptiveCache has the same structural commitment: recent tokens (current step's output) always stay in the suffix as potentially volatile but immediately relevant. The H2O paper makes the same design choice.
- **Power-law attention distribution:** ScissorHands's theoretical guarantee relies on power-law attention score distributions — the same distribution H2O observes. The three papers (H2O, ScissorHands, SnapKV) converge on the same empirical regularity.

**Key differences from AdaptiveCache:**
1. **No layout awareness:** ScissorHands drops tokens from a flat budget. The surviving tokens remain at their original positions in the KV cache. There is no mechanism to reorganize which tokens come first in the sequence to enable prefix cache reuse. A compressed cache with scattered gaps cannot be prefix-cached.
2. **Decoding-phase only:** ScissorHands operates token-by-token during generation within a single LLM call. AdaptiveCache operates at the inter-step boundary of an agent loop — between separate LLM calls — organizing context before the next call begins.
3. **No cross-call persistence:** ScissorHands's importance record is per-generation; it does not persist across separate API calls. AdaptiveCache maintains a persistent layout across the agent's entire trajectory.
4. **Training-free (shared advantage):** Both ScissorHands and AdaptiveCache require no fine-tuning. ScissorHands is the earliest major paper establishing that training-free, attention-signal-based KV cache compression is viable.

**AdaptiveCache synthesis:** ScissorHands's persistence ratio measurement (>95% overlap between first-half and second-half pivotal token sets) is the strongest published empirical evidence for AdaptiveCache's layout hypothesis: the set of high-value tokens is stable enough across a session that it can be identified in advance and locked into the stable prefix. AdaptiveCache adds the layout step ScissorHands lacks — ensuring those stable tokens form a contiguous prefix that survives across agent step boundaries without invalidating prefix KV caches.

## Related Pages / Citations to Follow Up

- [h2o.md](h2o.md) — contemporaneous; same eviction principle (attention-based importance) but uses cumulative scores; ScissorHands uses a shorter history window with low-score counting
- [snapkv.md](snapkv.md) — extends persistence of importance insight to prompt-phase compression with observation window; explicitly cites and improves on ScissorHands
- [streamingllm.md](streamingllm.md) — contemporaneous; attention sinks as special case of persistent tokens
- [pyramidkv.md](pyramidkv.md) — extends with per-layer budget allocation based on attention entropy patterns
- [prefix-caching.md](prefix-caching.md) — the layout gap that ScissorHands does not address
