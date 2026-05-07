---
title: SnapKV — LLM Knows What You are Looking for Before Generation (Li et al., 2024)
type: source
tags: [kv-cache, eviction, attention, inference-efficiency, long-context, prompt-compression]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# SnapKV: LLM Knows What You are Looking for Before Generation

**Authors:** Yuhong Li, Yingbing Huang, Bowen Yang, Bharat Venkitesh, Acyr Locatelli, Hanchen Ye, Tianle Cai, Patrick Lewis, Deming Chen  
**Affiliations:** University of Illinois Urbana-Champaign, Cohere, Princeton University  
**Date:** 2024 (arXiv April 2024, revised June 2024; under review)  
**arXiv:** 2404.14469  
**Source file:** `raw/papers/snapkv.pdf`

## Problem

Prior KV cache eviction methods (H2O, StreamingLLM, ScissorHands) target the decoding phase: they compress KVs that accumulate one-by-one as new tokens are generated. But in practical chatbot and agent settings, **prompts are far longer than generated responses** — a 16K-token input may generate only 512 tokens. Prompt KVs dominate memory and latency, yet no prior work compresses them effectively before generation begins. H2O explicitly overlooks prompt KV compression. The challenge: how do you decide which prompt tokens to keep before you have even started generating?

## Key Observation

**Observation 1: The attention allocation pattern can be identified before generation.** Each attention head consistently focuses on specific subsets of prompt tokens during generation. These "important" positions can be identified by examining the attention weights from the last segment of the prompt (the "observation window") to all preceding prefix positions. The last window of the input sequence recognizes the actual generation attention pattern with high overlap (hit rate >0.7 in most layers across datasets).

**Observation 2: The pattern is consistent across all of generation.** Once identified using the observation window, the set of important prefix positions remains stable throughout the entire decoding process — across all generated tokens. Hit rates between the observation-window-identified set and the actual generation attention at every subsequent generation step are consistently high.

**Critical nuance:** The important tokens are **query-dependent**. Different instructions over the same document prioritize different prefix positions (hit rates between different instruction pairs are ~0.6–0.7, not 1.0). This invalidates static compression policies that weight tokens by position or frequency alone — a context-aware, per-query compression is needed. SnapKV is instruction-aware because the observation window includes the instruction text.

**Instruction position invariance:** Whether the instruction is placed at the beginning or end of the context, hit rates remain consistently high across all tested datasets. SnapKV works regardless of prompt template.

## Proposed Method

**SnapKV algorithm** (applied once during the prefill/prompt phase, before generation begins):

1. **Observation window (Lobs):** Define the last `Lobs` tokens of the prompt as the observation window. These tokens' queries are used to estimate what the prefix contains that is worth keeping.

2. **Voting:** Compute attention weights from the `Lobs` observation-window queries to all `Lprefix` preceding positions (across all N heads). Sum these weights across the query dimension to get a cumulative importance score per head per prefix position: `C = sum_{i} W_obs[:, i, :]`.

3. **Top-k selection:** For each head independently, select the top-k positions `I = Topk(C, k)` where k = floor(p × Lprefix) for compression rate p. Selection is per-head — different heads can keep different tokens.

4. **Clustering via 1D max pooling:** Apply 1D max pooling with kernel size 5 (default) to the importance scores before selecting top-k. This clusters nearby important positions together, preserving surrounding context tokens and preventing the "partial retrieval" failure mode where only the start of a multi-token entity is selected and the rest is hallucinated.

5. **Compressed KV cache:** Gather the selected prefix KVs and concatenate them with the full observation window KVs. The resulting compressed KV cache = (selected prefix KVs) + (observation window KVs). This is the fixed cache used for all subsequent generation. No further updates to the prompt KVs.

**Key pseudo-code structure (PyTorch style from the paper):**
- Compute attention weights of observation window queries over all prefix keys
- Sum along query dimension to get per-head importance scores per prefix position
- Apply 1D max pooling for clustering
- Select top-k indices per head
- Gather compressed past key/value states; concatenate with observation window KVs

## Key Results

- **Speed:** 3.6× generation speedup vs baseline at 16K input tokens (constant decoding latency regardless of input length, since compressed KV cache size is fixed)
- **Memory:** 8.2× memory reduction at 16K tokens (batch size 2); extends max processable length from 16K to 131K tokens at batch size 2 on A100-80GB
- **Extreme long context:** 380K tokens on single A100-80GB (380× compression ratio for LWM-Text-Chat-1M), with negligible accuracy drop on Needle-in-a-Haystack test up to 140K
- **LongBench accuracy:** Negligible drop across 16 diverse tasks (single/multi-document QA, summarization, few-shot, synthetic, code) for LWM-Chat, LongChat-32K, Mistral-7B, Mixtral-8x7B — even at 1024 token KV budget with ~13K average input length (92% compression)
- **vs H2O head-to-head:** SnapKV with 1024 KV budget outperforms H2O with 4096 KV budget on 11/16 LongBench benchmarks for Mistral-7B; H2O's generation-phase focus on prompt fails on long-context tasks
- **Clustering ablation:** Without pooling, performance degrades significantly — sparse token selection breaks induction head context copying mechanisms
- **RAG tasks (Command-R 35B):** At 5–32× compression, only -1.2% RAG citation F1 and -2.1% RAG end-to-end F1; actually +5.4% improvement at 200 documents (KV compression reduces noise from irrelevant documents)
- **Lost-in-the-middle:** SnapKV is robust — does not suffer from the lost-in-middle pathology even at high compression ratios
- **Parallel decoding compatibility:** Combining SnapKV with Medusa gives 2.2× speedup over naive decoding at 10K prompt length

## Relationship to AdaptiveCache

SnapKV is the most directly technically relevant paper to AdaptiveCache's core token importance signal. It proves that the observation window — the last segment of a prompt — is the best available proxy for what will be attended to during generation.

**Points of contact:**
- **Observation window as the AdaptiveCache signal source:** AdaptiveCache's compaction step runs between agent steps, at the boundary between "current turn's output" and "next turn's input." This boundary is structurally identical to SnapKV's observation window: the most recently generated tokens (the last action/observation) form the natural query set for deciding which prior context tokens are worth keeping. SnapKV proves this window reliably predicts generation-time attention patterns with >70% hit rate — validating AdaptiveCache's plan to use the last step's tokens as the "query" for deciding what to promote to the stable prefix.
- **Per-head selection matches AdaptiveCache's fine-grained token granularity:** SnapKV's per-head top-k selection means different attention heads keep different tokens. This is fine-grained (item-level) like AdaptiveCache's target, not coarse-grained like summarization approaches.
- **Clustering insight for AdaptiveCache:** SnapKV's pooling result is critical — naive top-k selection breaks induction heads and causes hallucination. AdaptiveCache must similarly preserve token neighborhoods around high-importance positions, not just the individual peak-attention tokens. This is an implementation constraint, not an algorithmic choice.
- **Query-dependency of importance:** SnapKV's finding that different queries prioritize different prefix tokens is a direct argument for AdaptiveCache's online, inter-step reordering — the stable prefix composition must update as the agent's task context evolves.
- **Prompt phase vs. decoding phase:** SnapKV fills the gap H2O leaves: prompt KV compression. AdaptiveCache's inter-step compaction is more like SnapKV's prompt-phase compression than H2O's decoding-phase eviction, because AdaptiveCache runs once per agent step boundary, reorganizing the accumulated context before it becomes the prompt for the next step.

**Key differences from AdaptiveCache:**
1. **No layout awareness:** SnapKV compresses which tokens survive, not where they sit. A prefix that drops tokens at arbitrary positions cannot be reused as a prefix cache — the byte sequence changes every time, invalidating any downstream prefix KV cache from a serving system. AdaptiveCache ensures surviving tokens are arranged to maximize prefix-cache-preserving layout.
2. **Applied per-query, not cross-step:** SnapKV runs once before each generation. It does not maintain persistent state across agent steps. AdaptiveCache tracks token importance across steps (like H2O's running sum) to build a stable prefix that persists across multiple agent turns.
3. **Not prefix-cache-preserving by design:** SnapKV explicitly selects different tokens per head and per query — two adjacent calls with slightly different prompts will produce different compressed KV caches with no common prefix. This is opposite to AdaptiveCache's goal.
4. **Observation window always included in full:** SnapKV always keeps the entire observation window. AdaptiveCache similarly keeps recent tokens (the latest action/observation) as the "recent" suffix component.

**AdaptiveCache synthesis:** AdaptiveCache can use SnapKV's voting formula as its importance signal, applied at each step boundary using the newly-generated observation as the observation window. After voting to identify high-importance prefix tokens, AdaptiveCache's layout step ensures those tokens are relocated to a contiguous stable prefix (for prefix cache reuse), rather than kept in their original scattered positions. This layout step is what SnapKV lacks and what AdaptiveCache uniquely provides.

## Related Pages / Citations to Follow Up

- [h2o.md](h2o.md) — SnapKV's complement: H2O handles decoding-phase KV eviction; SnapKV handles prompt-phase; AdaptiveCache integrates both
- [streamingllm.md](streamingllm.md) — SnapKV explicitly critiques StreamingLLM for losing middle tokens; SnapKV's observation window recovers them
- [scissorhands.md](scissorhands.md) — contemporaneous; cited in SnapKV's related work; focused on generation-phase persistence of important tokens
- [pyramidkv.md](pyramidkv.md) — extends SnapKV's approach with per-layer varying budget sizes
- [lost-in-middle.md](lost-in-middle.md) — Liu et al. (2023); cited in SnapKV RAG experiments; SnapKV is explicitly robust to lost-in-middle pathology
- [prefix-caching.md](prefix-caching.md) — the layout gap that SnapKV does not address; where AdaptiveCache goes beyond SnapKV
