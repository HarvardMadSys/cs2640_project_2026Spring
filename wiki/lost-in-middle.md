---
title: Lost in the Middle — How Language Models Use Long Contexts (Liu et al., 2023)
type: source
tags: [long-context, attention, positional-bias, inference-efficiency, context-management]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Lost in the Middle: How Language Models Use Long Contexts

**Authors:** Nelson F. Liu, Kevin Lin, John Hewitt, Ashwin Paranjape, Michele Bevilacqua, Fabio Petroni, Percy Liang  
**Affiliations:** Stanford University, University of California Berkeley, Samaya AI  
**Date:** 2023 (arXiv July 2023, revised November 2023; published in TACL)  
**arXiv:** 2307.03172  
**Source file:** `raw/papers/lost-in-middle.pdf`

## Problem

Language models are increasingly deployed with long input contexts — 4K to 100K tokens — for tasks like retrieval-augmented generation, document QA, and multi-turn dialogue. But simply extending a model's context window does not guarantee it can effectively use all the information in that window. The question: how does the position of relevant information within a long input context affect model performance?

## Key Observation: The U-Shaped Performance Curve

**The finding:** Performance is highest when relevant information appears at the very beginning (primacy bias) or very end (recency bias) of the input context. Performance degrades significantly when relevant information is placed in the middle of the context — even for models specifically designed for long-context use.

**Quantitative scale:** For GPT-3.5-Turbo on a 20-document QA task (~4K tokens), placing the answer document at positions 5–15 (middle) reduces accuracy to below closed-book performance (no documents at all: 56.1%). The worst-case performance drop exceeds 20 percentage points compared to best-case placement.

**Tasks tested:**
- **Multi-document question answering:** NaturalQuestions-Open with k Wikipedia passages; exactly one passage contains the answer; position of the answer passage varied systematically.
- **Synthetic key-value retrieval:** JSON-serialized UUID key-value pairs; model must return the value for a specified key; position of the relevant pair varied.

**Models showing this pattern:** GPT-3.5-Turbo, GPT-3.5-Turbo (16K), Claude-1.3, Claude-1.3 (100K), MPT-30B-Instruct, LongChat-13B (16K), GPT-4. The curve appears across all models tested, including explicitly long-context models. Extended-context models (e.g., GPT-3.5-Turbo vs. GPT-3.5-Turbo-16K) are nearly identical in performance when the input fits both context windows — a larger context window does not by itself improve utilization.

**Scaling behavior:** The U-shaped curve appears only in sufficiently large models. Llama-2-7B is solely recency-biased; Llama-2-13B and 70B exhibit the full U-shape. The curve exists in base (non-instruction-finetuned) models as well as instruction-finetuned variants, indicating it is a property of pretraining, not instruction tuning.

**Open-domain QA saturation:** Providing more than ~20 retrieved documents only marginally increases reader accuracy (~1.5% for GPT-3.5-Turbo), while significantly increasing context length, latency, and cost. Reader performance saturates far before retriever recall saturates.

## Analysis of Contributing Factors

**Architecture:** Encoder-decoder models (Flan-T5-XXL, Flan-UL2) are relatively robust to position changes when evaluated within their training-time context lengths, because bidirectional encoders can contextualize each document using future document tokens. But when evaluated on sequences longer than training, encoder-decoder models also show the U-shaped curve. Decoder-only models cannot attend to future tokens when contextualizing documents, contributing to positional sensitivity.

**Query-aware contextualization:** Placing the query before and after all documents dramatically improves key-value retrieval (near-perfect accuracy at 300 pairs) but minimally changes multi-document QA trends. The structured retrieval benefit does not transfer to natural language reasoning.

**Instruction fine-tuning:** Both base and instruction-finetuned MPT-30B show the U-shape. Instruction fine-tuning slightly reduces the worst-case performance disparity (from ~10% to ~4% for 20-document QA) but does not eliminate the positional sensitivity. The U-shape appears to be a pretraining artifact — instruction data is often formatted with instructions at the beginning, possibly reinforcing primacy bias.

**Connection to serial-position effect:** The observed pattern mirrors the human psychology serial-position effect (Ebbinghaus, 1913; Murdock, 1962), where free-recall performance is highest for first and last items in a list. In humans this is explained by short-term vs. long-term memory asymmetries; in LLMs the mechanism is different but the behavioral outcome is similar.

## Key Results

- **GPT-3.5-Turbo (20 docs):** 75.8% accuracy when answer at position 1; drops to 53.8% at position 9 (middle of 20); 63.2% at position 20 (end)
- **Claude-1.3 (20 docs):** 59.9% at position 1; 56.8% at position 9; 60.1% at position 20 — more robust than GPT-3.5-Turbo but still shows the pattern
- **GPT-4 (20 docs):** Higher absolute performance but still a U-shaped curve — positional sensitivity is model-scale independent
- **Extended-context models:** GPT-3.5-Turbo and GPT-3.5-Turbo (16K) have nearly superimposed performance curves for all tested input lengths — context window size alone does not fix positional sensitivity
- **Open-domain QA saturation:** Beyond 20 retrieved documents, ~1.5% gain per 30 additional documents; diminishing returns set in rapidly

## Limitations

The experiments focus on tasks requiring point lookup (identifying one relevant item among many), which may over-index on the positional sensitivity relative to tasks with more diffuse relevance. The paper does not propose a remedy — it is a diagnostic study. Remedies (reranking, order optimization) are suggested as future work but not implemented.

## Relationship to AdaptiveCache

Lost in the Middle provides the empirical foundation for AdaptiveCache's layout hypothesis: **where you put information matters as much as whether you include it.**

**Points of contact:**
- **The primacy effect validates AdaptiveCache's stable prefix design:** Liu et al. find that information placed at the beginning of the context is used most reliably. AdaptiveCache's explicit layout optimization — placing stable, high-value items early in the context (the stable prefix) — directly exploits this empirical regularity. Tokens in the stable prefix benefit from both (a) prefix KV-cache reuse (computational savings) and (b) the primacy effect (higher probability of being used by the model). Both effects favor the same token placement strategy.
- **The recency effect validates AdaptiveCache's suffix design:** The finding that end-of-context information is also used reliably (recency bias) supports AdaptiveCache's design to keep recent, volatile tokens in the suffix. Freshly-generated agent observations placed at the end of the context benefit from the model's recency bias, maximizing the likelihood that recent tool outputs are used in the next action.
- **The middle-context failure justifies aggressive compaction:** Liu et al. demonstrate that middle-context tokens are routinely ignored — worse than closed-book in some configurations. Tokens that cannot be promoted to the stable prefix or recent suffix are sitting in the dead zone where models fail to use them regardless. AdaptiveCache's eviction of mid-context tokens that do not qualify for the stable prefix is not just a cost optimization — it also avoids the middle-context attention failure. Dropping them does not lose effective utility if the model would not have used them anyway.
- **Primacy + recency = AdaptiveCache's two anchor regions:** The U-shaped curve directly maps onto AdaptiveCache's two-zone layout: (stable prefix = primacy zone) + (recent suffix = recency zone). Items that fall outside these zones are both computationally expensive (no prefix cache hit) and behaviorally ineffective (middle-context ignore region). AdaptiveCache eliminates the expensive dead zone.
- **Motivation for ordered layout vs. unordered eviction:** Prior eviction methods (H2O, ScissorHands, SnapKV) decide WHAT to keep but leave surviving tokens scattered at their original positions. Liu et al.'s finding shows this is insufficient: even keeping the right tokens, if they end up in middle positions, the model may fail to use them. AdaptiveCache's layout step is needed precisely because eviction alone cannot guarantee that surviving tokens land in the primacy or recency zone.

**Key differences from AdaptiveCache:**
1. **Diagnostic, not mechanistic:** Liu et al. characterize the problem but do not propose a solution. AdaptiveCache is the solution.
2. **No attention-signal analysis:** Lost in the Middle does not connect positional sensitivity to KV cache dynamics or attention distributions. The H2O/SnapKV/StreamingLLM papers supply the mechanistic account; Lost in the Middle supplies the behavioral motivation.
3. **Static context:** The paper studies fixed, pre-constructed contexts, not dynamic agent contexts that grow step-by-step. The positional sensitivity observed for static long prompts applies with even greater force to agent trajectories, where the relevant observation from step t=3 may be buried under steps 4–15 by the time the agent needs to act on it at step t=16.

**AdaptiveCache synthesis:** Lost in the Middle is AdaptiveCache's behavioral justification. It shows that (a) the beginning of context is most reliably used, (b) the end is second most reliably used, and (c) the middle is least reliably used. AdaptiveCache's layout optimization converts the token positioning problem from an unoptimized side-effect into a first-class design choice, ensuring high-value tokens are placed where models are empirically shown to use them.

## Related Pages / Citations to Follow Up

- [prefix-caching.md](prefix-caching.md) — the cost model that makes stable-prefix layout computationally advantageous; Lost in the Middle provides the behavioral motivation to complement the computational motivation
- [h2o.md](h2o.md) — H2O identifies which tokens are most attended to; Lost in the Middle explains why placing those tokens early further amplifies their utility
- [snapkv.md](snapkv.md) — SnapKV explicitly cites Liu et al. and claims robustness to the lost-in-middle pathology; per-head top-k selection recovers relevant tokens regardless of original position
- [streamingllm.md](streamingllm.md) — StreamingLLM explicitly cites Liu et al. as motivation; attention sinks at initial positions align with primacy bias
- [context-management.md](context-management.md) — taxonomy of approaches; Lost in the Middle is cited as motivation for layout-aware approaches
