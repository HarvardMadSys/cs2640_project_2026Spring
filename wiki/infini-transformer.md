---
title: Infini-Transformer — Efficient Infinite Context Transformers with Infini-attention (Munkhdalai et al., 2024)
type: source
tags: [kv-cache, long-context, compressive-memory, inference-efficiency, streaming, context-management]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Infini-Transformer: Efficient Infinite Context Transformers with Infini-attention

**Authors:** Tsendsuren Munkhdalai, Manaal Faruqui, Siddharth Gopal  
**Affiliation:** Google  
**Date:** April 2024 (arXiv April 10, 2024; revised August 9, 2024)  
**arXiv:** 2404.07143  
**Source file:** `raw/papers/infini-transformer.pdf`

## Problem

Standard transformer attention has O(N²) memory and compute cost in sequence length N, and KV cache memory grows linearly with sequence length. For LLMs deployed on long-horizon agent tasks — multi-turn dialogues, 1M-token document corpora, book-length inputs — this scaling is prohibitive. Two prior solutions both fail to scale infinitely:

- **Transformer-XL / recurrent caching:** extends context by caching and attending over recent KV states; memory still grows linearly with number of cached segments.
- **Retrieval-augmented memory (Memorizing Transformers):** stores full KV pairs in an external memory; memory footprint is 183M at 65K context; grows with sequence length; non-differentiable retrieval step.

The paper asks: can a transformer handle **unbounded** input sequences with **bounded** memory and compute?

## Key Observation

The standard dot-product attention mechanism in each transformer layer has no mechanism to aggregate information across segments — each pass through the attention layer can only see its own segment. Two key observations motivate the design:

1. **Associative memory can store KV bindings compactly:** A matrix M ∈ R^{d_key × d_value} can associate keys to values via outer products. Retrieving for a query Q is a matrix multiply, not a full sequence scan. This gives constant memory regardless of how many KV pairs have been stored (up to matrix rank limitations).

2. **Local dot-product and global compressive memory are complementary:** Recent tokens require precise, query-specific attention (high-resolution local window). Older context can be stored in a compressed associative form that is retrieved cheaply. Combining both in a single attention head gives the best of both regimes.

## Proposed Method: Infini-attention

Infini-attention replaces standard multi-head attention with a mechanism that maintains a **compressive memory matrix** alongside a local dot-product attention window. It processes input as a sequence of fixed-length segments S = [x_1, ..., x_N] and updates memory at each segment boundary.

### Memory Retrieval

For segment s, given query matrix Q_s computed from segment inputs:

```
A_mem = σ(Q_s) M_{s-1} / (σ(Q_s) z_{s-1})
```

where σ is ELU+1 (a positive-valued non-linearity ensuring valid memory addressing), M_{s-1} ∈ R^{d_key × d_value} is the previous segment's memory state, and z_{s-1} ∈ R^{d_key} is a normalizing term tracking the sum of all stored keys (prevents magnitude drift). The division is element-wise over the normalization term broadcast over the value dimension.

### Memory Update (Linear Rule)

After processing segment s:

```
M_s ← M_{s-1} + σ(K_s)^T V_s
z_s ← z_{s-1} + Σ_t σ(K_t)
```

This is a linear outer-product update — each new KV pair adds to the memory matrix. The cost is O(d_key × d_value) regardless of how many segments have been processed.

### Delta Rule (Improved Variant)

The linear update ignores that some KV bindings may already exist in M, leading to redundant overwriting. The delta rule first retrieves the existing memory value for each key, then updates only the residual:

```
M_s ← M_{s-1} + σ(K_s)^T (V_s − σ(K_s) M_{s-1} / σ(K_s) z_{s-1})
```

The term in parentheses is (new value − current memory's prediction for that key). This is a form of associative memory with error-correction, analogous to Hopfield networks. The delta rule consistently outperforms the linear rule in experiments.

### Gating: Combining Local and Memory Attention

For each attention head, the local dot-product attention output A_dot (computed over the current segment only) and the memory retrieval output A_mem are combined via a learned scalar gate β:

```
A = sigmoid(β) ⊙ A_mem + (1 − sigmoid(β)) ⊙ A_dot
```

β is per-head and learned during training. This allows heads to specialize: some favor recent local context (β → 0), others favor compressed long-range memory (β → 1), and intermediate values create mixers. The gating is the only additional learned parameter per head — one scalar per head, totaling H × L additional parameters per model.

### Segment Processing

Input is split into non-overlapping segments of length N (N=2048 in language modeling experiments, N=2000 for continual pre-training). Each segment is processed with standard causal masked attention over its own tokens (the local window), then the compressive memory M is updated before passing to the next segment. The memory state is passed as a recurrent state between segments; gradients flow through the memory update during backpropagation.

### Multi-Head Structure

H parallel compressive memories are maintained, one per head. Each head independently computes A_mem and A_dot, then gates them. Outputs are concatenated and projected: [A¹; ...; A^H] W_O.

### Memory Footprint

The total memory footprint for Infini-attention's compressive store is:

```
d_key × (d_value + 1) × H × L parameters
```

across all L layers and H heads, regardless of sequence length. For Llama-style 8B models, this is approximately 1.6M parameters — constant no matter how many tokens have been processed. This compares to:

- Transformer-XL: linear growth with number of cached segments
- Memorizing Transformers: 183M at 65K context (grows linearly with context)
- Infini-Transformer: 1.6M at any context length (114× compression vs Memorizing Transformers)

## Key Results

**Long-context language modeling (PG19 and Arxiv-math):**
- Infini-Transformer (Linear+Delta): 9.67 perplexity (PG19), 2.23 (Arxiv-math)
- Outperforms Transformer-XL (11.88 / 2.42) and RMT (13.27 / 2.55)
- 114× memory compression ratio relative to Memorizing Transformers at equivalent context

**Passkey retrieval at 1M context:**
- 1B model fine-tuned on 5K sequences achieves 100% accuracy on passkey retrieval across all positions (start, middle, end) at 1M-token context length
- Directly contradicts the "lost in the middle" pathology for this architecture — compressive memory does not exhibit the U-shaped position-bias curve because retrieval is query-driven, not position-dependent

**Book summarization (BookSum, 500K context):**
- 8B model achieves ROUGE 18.5 (new state-of-the-art at time of submission)
- Outperforms BART+Unlimiformer (16.9) and PRIMERA+Unlimiformer (17.2)
- 500K tokens processed in a single forward pass via segment-by-segment streaming

**Training efficiency:**
- 100K-length continual pre-training reduces Arxiv-math perplexity to 2.20–2.21 from models originally trained on shorter contexts
- Plug-and-play continual pre-training adapts existing LLMs to long contexts without full retraining

## Limitations

Infini-attention's compressive memory is a fixed-rank matrix (d_key × d_value). As more segments are processed, new KV bindings overwrite old ones (interference). For very long sequences with many distinct facts, the memory matrix saturates and older information is gradually lost — this is the same forgetting problem as classic Hopfield networks. The linear segment-by-segment processing means early context cannot be revisited using local dot-product attention; only the lossy compressive retrieval is available.

The method requires at least lightweight continual pre-training to learn the gating parameters β and to adapt the model to use compressive memory. Training-free application is not evaluated. The segment length N is a manual hyperparameter that interacts with both local attention quality and memory update frequency — short segments update memory frequently (more compression) while long segments give richer local attention but coarser memory updates.

Position embeddings are applied only within segments for local attention; the compressive memory retrieval uses un-positioned queries and keys, which may limit fine-grained positional reasoning over long-range retrieved content.

## Relationship to AdaptiveCache

Infini-Transformer is architecturally distinct from AdaptiveCache but highly relevant as both a competitor and a source of design insights.

**Points of contact:**

- **Compressive memory as the alternative paradigm:** Infini-Transformer represents the limit of the "compress everything into a fixed matrix" approach — the opposite end of the spectrum from AdaptiveCache's "preserve selected tokens exactly in their original form." Both approaches solve the same problem (unbounded context in bounded memory) but with fundamentally different fidelity guarantees. AdaptiveCache preserves exact token content for selected items; Infini-Transformer lossy-compresses all past content into a matrix. For agent tasks requiring precise recall of tool outputs (API responses, code, file contents), exact preservation may matter.

- **Gating mechanism is analogous to AdaptiveCache's stable/volatile split:** Infini-attention's learned gate β per head controls whether each head prioritizes recent local content (β → 0) or compressed long-range memory (β → 1). This is structurally similar to AdaptiveCache's division between the stable prefix (long-range, high-value, persisted across steps) and the volatile suffix (recent, locally relevant). The β gate in Infini-attention is learned; AdaptiveCache's split is determined online by attention importance scores — both achieve the same architectural role.

- **Segment-boundary processing maps to AdaptiveCache's inter-step compaction:** Infini-Transformer updates its compressive memory at each segment boundary. AdaptiveCache performs its compaction pass at each agent step boundary. The structural parallel is exact — both use step/segment boundaries as the natural unit of context reorganization. The difference is the mechanism: Infini-Transformer writes to a lossy matrix; AdaptiveCache rewrites the token layout of an exact KV cache.

- **Memory saturation validates AdaptiveCache's eviction necessity:** Infini-Transformer's known limitation — the compressive memory matrix saturates and forgets old information as more segments are processed — empirically validates AdaptiveCache's argument that context management is necessary. Both approaches agree that keeping everything exactly is infeasible; they differ only in how they handle the surplus. Infini-Transformer uses lossy compression; AdaptiveCache uses selective eviction with layout optimization.

- **100% passkey accuracy at 1M tokens vs. AdaptiveCache's target:** Infini-Transformer achieves perfect positional accuracy at 1M tokens by completely bypassing the position-bias problem (compressive memory retrieval is query-driven, not position-indexed). AdaptiveCache must achieve reliable recall through layout optimization (placing important tokens early for primacy effect) rather than architectural redesign. AdaptiveCache is lighter (no model changes) but sacrifices the full positional robustness Infini-attention provides.

**Key differences from AdaptiveCache:**

1. **Requires model modification:** Infini-attention replaces standard attention — existing off-the-shelf LLMs cannot use it without architectural changes and at minimum lightweight continual pre-training. AdaptiveCache is a serving-layer optimization compatible with any existing LLM, applied externally without touching model weights.

2. **Lossy compression vs. exact token preservation:** Infini-Transformer stores all past context in a fixed matrix (lossy). AdaptiveCache selectively retains high-value tokens in their original form (lossless for selected tokens). For agent tasks where precise verbatim recall of tool outputs is required, AdaptiveCache's exact preservation is higher fidelity.

3. **Not prefix-cache-compatible:** Infini-attention's compressive memory is a running recurrent state that changes with every segment. The effective "prompt" that the LLM processes is not byte-for-byte reproducible from session to session, so standard prefix KV-cache systems cannot reuse Infini-Transformer's memory states across separate API calls. AdaptiveCache's stable prefix is explicitly designed to be identical across consecutive agent steps, maximizing prefix KV-cache reuse.

4. **No token-level importance signal:** Infini-Transformer updates memory with all tokens in each segment equally (weighted by the outer-product associative update, not by attention importance scores). There is no mechanism to preferentially preserve high-value tokens at higher fidelity than low-value tokens — everything gets compressed into the same matrix. AdaptiveCache uses attention importance to selectively preserve high-value tokens exactly and evict low-value tokens.

5. **Training dependency:** Infini-Transformer requires training the gating parameters (and ideally the full model for optimal performance). The plug-and-play continual pre-training requirement is lighter than full training but is still a barrier for drop-in deployment. AdaptiveCache requires zero training.

**AdaptiveCache synthesis:** Infini-Transformer demonstrates that compressive memory as an alternative to KV cache retention is architecturally viable and can scale to 1M+ tokens. However, its architectural modification requirement, lossy compression, and incompatibility with prefix KV-cache systems make it unsuitable as a drop-in inference-time optimization. AdaptiveCache occupies the complementary niche: training-free, exact-preservation, prefix-cache-preserving context management that works with any existing LLM. Infini-Transformer is the natural comparison point for AdaptiveCache when discussing the "integrate memory into the architecture" vs. "optimize layout at the serving layer" design axis.

## Related Pages / Citations to Follow Up

- [streamingllm.md](streamingllm.md) — StreamingLLM's attention sinks + rolling window is a simpler predecessor; both are streaming approaches but StreamingLLM discards middle context entirely while Infini-Transformer compresses it
- [h2o.md](h2o.md) — H2O and Infini-Transformer both address long-context KV memory; H2O evicts exact tokens while Infini-Transformer compresses into a matrix
- [prefix-caching.md](prefix-caching.md) — the gap Infini-Transformer cannot address; recurrent memory state is not prefix-cacheable
- [context-management.md](context-management.md) — taxonomy: Infini-Transformer fits as a separate category (compressive memory / architectural modification)
- [lost-in-middle.md](lost-in-middle.md) — Infini-Transformer's passkey results (100% at all positions) suggest its retrieval mechanism is not susceptible to the U-shaped position bias
- [scissorhands.md](scissorhands.md) — Persistence of importance hypothesis complements Infini-Transformer's approach: ScissorHands identifies which tokens survive; Infini-Transformer compresses all of them
