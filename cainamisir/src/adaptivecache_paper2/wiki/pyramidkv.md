---
title: PyramidKV — Dynamic KV Cache Compression Based on Pyramid Visual Information (Cai et al., 2024)
type: source
tags: [kv-cache, eviction, attention, inference-efficiency, long-context, per-layer]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# PyramidKV: Dynamic KV Cache Compression Based on Pyramid Visual Information

**Authors:** Zefan Cai, Yichi Zhang, Bofei Gao, Yuliang Liu, Tianyu Liu, Keming Lu, Wayne Xiong, Yue Dong, Baobao Chang, Junjie Hu, Wen Xiao  
**Affiliations:** University of Wisconsin-Madison, Peking University, Microsoft  
**Date:** 2024 (arXiv February 2024, revised multiple times through late 2024)  
**arXiv:** 2406.02069  
**Source file:** `raw/papers/pyramidkv.pdf`

## Problem

Prior KV cache compression methods (H2O, SnapKV, StreamingLLM, ScissorHands) allocate a **uniform** KV cache budget across all transformer layers. Each layer is given the same number of KV slots to fill. This ignores empirical evidence that different layers exhibit radically different attention patterns: lower layers disperse attention broadly across many tokens while higher layers concentrate attention on a small set of tokens. A uniform budget is wasteful for upper layers (which can get by with fewer KVs) and too tight for lower layers (which genuinely need more). The question: can per-layer budget allocation match the actual information distribution of transformer attention?

## Key Observation: Attention Entropy Decreases Monotonically Across Layers

**Layer-wise entropy variation:** Attention entropy (measuring how dispersed or concentrated attention is) decreases monotonically from the lowest transformer layers to the highest. Lower layers attend broadly and uniformly — high entropy, meaning many tokens carry non-trivial weight. Upper layers attend sparsely to a small set of pivotal tokens — low entropy, meaning the distribution is concentrated and most tokens are near-zero.

**Quantitative pattern:** In experiments on Llama-2-7B and Mistral-7B across LongBench tasks, lower layers exhibit attention entropy > 4 nats while upper layers drop to < 1 nat. The gradient is smooth and monotonic, consistent across model architectures and input domains.

**Visual analogy:** The pattern mirrors a pyramid: wide base (many important tokens in lower layers) tapering to a narrow apex (few important tokens in upper layers). This gives the method its name.

**Why uniform budgets fail:** A uniform budget wastes slots on upper layers (which only need a handful of KVs to represent concentrated attention) while under-serving lower layers (which need many KVs for their dispersed patterns). The cost is double: memory wasted in upper layers; accuracy degraded in lower layers.

**Retention rate analysis:** For each layer, PyramidKV computes the minimum budget that covers a fixed fraction of cumulative attention mass. Upper layers reach the threshold with far fewer tokens; lower layers require many more. The per-layer budget follows the measured entropy curve.

## Proposed Method

**PyramidKV algorithm:**

1. **Observation window (borrowed from SnapKV):** Use the last `Lobs` tokens of the prompt as an observation window. Their queries are used to compute importance scores for all preceding prefix positions, exactly as in SnapKV.

2. **Layer-wise entropy measurement:** For each transformer layer l, compute the average attention entropy H_l of the observation-window queries over the prefix. H_l = -Σ p·log(p) summed over all prefix positions and averaged over heads and observation tokens.

3. **Budget allocation:** Set the KV budget for layer l proportional to H_l: B_l = B_total × (H_l / Σ_l H_l). This allocates more budget to high-entropy (lower) layers and less to low-entropy (upper) layers, subject to a minimum per-layer floor to prevent complete collapse in upper layers.

4. **Per-layer top-k selection:** Within each layer, use SnapKV's voting procedure (cumulative attention from observation window, with 1D max pooling for clustering) to select the top-B_l positions. Selection is per-head within each layer.

5. **Compressed KV cache:** Gather selected KVs for each layer at their allocated budget; concatenate with observation window KVs. The resulting cache is pyramidal: larger at lower layers, smaller at upper layers.

**Budget allocation formula variants:** The paper evaluates arithmetic, geometric, and entropy-proportional schedules. Entropy-proportional matching the measured distribution performs best; simpler pyramid schedules (linearly decreasing) are competitive and avoid requiring entropy measurement.

**Relation to SnapKV:** PyramidKV uses SnapKV as its intra-layer selection method. The contribution is the inter-layer budget allocation. SnapKV = uniform budget per layer + observation window. PyramidKV = variable budget per layer (pyramid-shaped) + observation window.

## Key Results

- **LongBench:** PyramidKV outperforms SnapKV, H2O, and StreamingLLM across all 16 LongBench tasks at equivalent average KV budget (1024 tokens across layers). Largest gains on long-document retrieval and multi-hop QA tasks.
- **Needle-in-a-Haystack:** PyramidKV maintains high retrieval accuracy at all needle depths while SnapKV and H2O show failure modes at specific depth ranges — the per-layer variable budget avoids the uniform-budget failure at lower layers.
- **Memory reduction:** With PyramidKV's pyramid shape, total KV memory is reduced ~20–30% beyond SnapKV at matched accuracy, because upper layers use far smaller caches.
- **Robustness across architectures:** Consistent improvements on Llama-2-7B/13B, Mistral-7B, and Mixtral-8x7B, suggesting the monotonic entropy gradient is a universal property of decoder-only transformers.
- **Overhead:** Entropy measurement requires one additional forward pass over the observation window per layer, but this is dominated by the prefill computation. Net latency increase is negligible.
- **Ablation (linear vs. entropy-proportional):** Linear pyramid schedule is within 1% of entropy-proportional on most tasks; entropy-proportional gives better accuracy on hard retrieval tasks at high compression.

## Limitations

PyramidKV inherits SnapKV's limitation: compression is applied once at the prompt-phase boundary, before generation. It is not designed for incremental eviction during decoding. The entropy measurement adds a dependency on the observation window size and prompt content — for very short prompts the entropy estimates may be noisy. Like all eviction-based methods, PyramidKV does not address prefix-cache-preserving layout: selected tokens remain at their original positions and the surviving KV set changes with each query, preventing downstream prefix KV cache reuse.

## Relationship to AdaptiveCache

**Points of contact:**
- **Per-layer budget allocation is composable with AdaptiveCache:** PyramidKV's core contribution — allocating larger KV budgets to lower layers and smaller budgets to upper layers — is orthogonal to AdaptiveCache's cross-step layout optimization. AdaptiveCache can directly adopt PyramidKV's entropy-based allocation schedule as its intra-step budget distribution strategy. The two operate at different axes: PyramidKV controls layer-wise budget shape; AdaptiveCache controls the cross-step prefix layout. They do not interfere.
- **Upper-layer concentration supports stable prefix hypothesis:** PyramidKV's finding that upper layers have low-entropy (concentrated) attention directly supports AdaptiveCache's hypothesis that a small stable prefix can capture the bulk of upper-layer attention. If upper layers concentrate on a few pivotal tokens (low entropy), then those tokens are exactly AdaptiveCache's stable prefix candidates — high cumulative attention, query-independent, worth locking into the prefix across agent steps. The entropy data quantifies how few tokens need to be in the stable prefix for upper-layer coverage.
- **Lower-layer dispersed attention as a caution:** PyramidKV's finding that lower layers need more tokens (high entropy, many tokens matter) is a caution for AdaptiveCache's compaction step: aggressive KV reduction that drops tokens retained for lower-layer coverage will hurt performance. AdaptiveCache's minimum-floor constraint for each layer should mirror PyramidKV's lower-layer budget requirement.
- **Shared SnapKV observation window:** PyramidKV uses SnapKV's observation window within each layer. AdaptiveCache's importance signal (the newly-generated observation as query window for the next step) is the same mechanism. PyramidKV validates that the observation window generalizes across layers, giving AdaptiveCache confidence that the same per-step observation window can drive per-layer importance scoring.
- **Attention entropy as a diagnostic:** PyramidKV's per-layer entropy metric is a natural diagnostic AdaptiveCache can log during compaction — tracking how concentrated the attention is at each layer for the current step tells AdaptiveCache how much prefix compression headroom exists without risking lower-layer coverage.

**Key differences from AdaptiveCache:**
1. **No layout awareness:** PyramidKV selects which tokens to keep per layer, but surviving tokens remain at their original positions in the KV cache. A compressed cache with scattered gaps at layer-dependent positions cannot be reused as a prefix cache. Different queries produce different per-layer selection sets, so there is no stable shared prefix between sequential calls.
2. **Applied once per LLM call, not across calls:** PyramidKV runs at prompt-phase within a single generation. It does not persist token importance information across separate agent steps or API calls. AdaptiveCache accumulates importance across the entire agent trajectory to build a persistent stable prefix.
3. **No cross-step promotion:** PyramidKV has no mechanism to "promote" a token into a stable position that will survive across multiple agent steps. Each call starts fresh. AdaptiveCache's stable prefix is exactly the set of tokens that PyramidKV would select in every call — but AdaptiveCache also ensures they appear first in the sequence to enable prefix KV cache reuse.
4. **Budget allocation vs. layout optimization:** PyramidKV's innovation is the budget shape across layers (how many KVs per layer). AdaptiveCache's innovation is the token ordering within the context (which tokens come first). These are complementary dimensions of the same resource optimization problem.

**AdaptiveCache synthesis:** AdaptiveCache should adopt PyramidKV's entropy-proportional budget allocation as the layer-wise dimension of its compaction policy. When AdaptiveCache's stable prefix contains N tokens, those N tokens should be drawn from the PyramidKV-guided budget per layer — larger selection sets at lower layers, smaller at upper layers. This gives AdaptiveCache the best of both: PyramidKV's empirically-grounded layer-wise budget efficiency, plus the layout step (contiguous prefix ordering) that enables cross-step prefix KV cache reuse that PyramidKV cannot provide.

## Related Pages / Citations to Follow Up

- [snapkv.md](snapkv.md) — PyramidKV's intra-layer selection method; PyramidKV is SnapKV + per-layer variable budget
- [h2o.md](h2o.md) — compared against PyramidKV; uniform budget baseline; H2O's flat budget is the direct foil for PyramidKV's pyramid budget
- [streamingllm.md](streamingllm.md) — sink + recent window; compared against PyramidKV; fails at lower layers under tight budget
- [scissorhands.md](scissorhands.md) — also allocates more budget to later layers (where persistence ratio dips); complementary per-layer observation
- [prefix-caching.md](prefix-caching.md) — the layout gap that PyramidKV shares with all eviction methods
- [lost-in-middle.md](lost-in-middle.md) — Liu et al. (2023); cited in PyramidKV; motivation for why uniform compression loses middle context
