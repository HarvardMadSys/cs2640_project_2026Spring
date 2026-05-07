# AdaptiveCache: Context Management for LLM Agents
**Midterm Checkpoint**  
Vlad Cainamisir  
Harvard University

---

## 1 Progress Since Proposal

Since the proposal, I have (1) conducted an extensive literature survey across 15+ papers spanning KV cache eviction, context management, prompt compression, and agentic reasoning, (2) substantially refined the AdaptiveCache design based on findings in that literature, (3) identified the key technical constraints that the implementation must satisfy, and (4) developed a concrete evaluation plan with baselines and compute budget.

The most significant outcome is that the literature survey revealed a gap — which I now call **the layout gap** — that sharpens the AdaptiveCache thesis considerably beyond the original proposal.

---

## 2 The Layout Gap: A Sharpened Thesis

Every existing KV cache eviction system (H2O [1], ScissorHands [2], SnapKV [3], PyramidKV [4], StreamingLLM [5]) answers the question: *what should we keep?* None of them answers: *where should surviving tokens sit?*

After eviction, surviving tokens remain at their original absolute positions, scattered throughout the context. This has a concrete consequence for prefix caching: the byte sequence sent to the server changes at every step, because different blocks survive at different positions. Without a stable byte-identical prefix, the server cannot reuse any cached KV states across steps.

**Layout gap:** existing methods optimize selection but not placement; surviving tokens scatter, preventing cross-step prefix cache hits.

This is distinct from — and complementary to — summarization-based approaches (ReSuM [6], SUPO [7], Anthropic compaction [8]). Those generate new tokens to replace context, which also invalidates the prefix. AdaptiveCache avoids generating any new tokens at all.

The only production system that gets close to AdaptiveCache's mechanism is Claude Code's internal `cache_edits` primitive [9], which surgically removes cached tool results without invalidating the rest of the prefix. This validates the technical approach but is not publicly accessible.

---

## 3 Refined Design

The original proposal described AdaptiveCache as a single compaction pass. The literature survey revealed this conflates two distinct mechanisms that operate at different timescales and serve different purposes. The refined design decomposes them:

### 3.1 Mechanism 1: Layout Optimizer (runs infrequently)

Determines the **stable prefix composition** — which items are pinned at fixed absolute positions, and in what order. The purpose is cross-step prefix cache hits: for the server to reuse cached KV states, the byte sequence at the prefix must be byte-for-byte identical across consecutive steps. This only happens if high-value items sit at stable absolute positions step after step.

Items are scored on two independent dimensions:

**Importance** (should we keep this?):
- Cumulative attention across recent steps [1] — tokens that consistently attract high attention
- Reference count — how many subsequent tool calls cite this block (novel, agentic-specific signal)
- Low-entropy token protection [10] — factual anchors that, if evicted, cause disproportionate downstream errors

**Stability** (should we pin it early?):
- Structural type prior — function definitions (0.9) vs. bash outputs (0.2) vs. error messages (0.1)
- Importance variance — consistently important blocks are stable pin candidates; volatile important blocks belong in the suffix
- Tool-call dependency graph centrality — blocks upstream of many tool calls are load-bearing

**Why importance ≠ stability.** An error message is critically important right now but should be evicted once resolved. A function signature is moderately important at every step but belongs pinned forever. A single score cannot represent both.

**Layout:** `[sink tokens: 0–3] [highest importance × stability: 4–100] [remaining pinned: 100–N] [volatile suffix]`

The sink zone (positions 0–3) follows StreamingLLM's finding [5]: initial tokens attract disproportionate attention due to SoftMax normalization regardless of content. Placing highest-value items immediately after sinks (positions 4–100) exploits primacy bias for free.

### 3.2 Mechanism 2: In-Place Hole Eviction Engine (runs every step)

A key finding from the KV cache architecture literature: **mid-sequence KV eviction does not require recomputing downstream states**, provided:

1. Post-rotated RoPE keys are used — position rotation is baked into stored key tensors at compute time (standard in all modern LLMs: Llama, Qwen, Claude, GPT-4)
2. Evicted positions become **holes** — not attendable, but not renumbered
3. Eviction is aligned to logical block boundaries (tool calls, turns)

This is demonstrated in production by PagedEviction [11] (block-wise eviction in vLLM, 3.1× throughput with <3–5% accuracy drop) and Claude Code's `cache_edits`. The critical constraint: **never renumber surviving tokens after eviction.** Renumbering scrambles RoPE positional encodings and makes all downstream KV states incorrect — this is what makes naive compaction harmful.

Volatile suffix items can therefore be evicted every step at near-zero cost: no new tokens generated, no downstream recomputation.

### 3.3 Three-Tier Pipeline

| Tier | Operation | Cost | Frequency |
|---|---|---|---|
| 1 | In-place hole eviction of volatile suffix | ~free | Every step |
| 2 | Layout reorganization (rescore + re-pin) | Moderate | When importance structure changes |
| 3 | Full summarization (emergency fallback) | Expensive | Rarely / never |

The design goal: never reach Tier 3. Tier 1 keeps memory bounded; Tier 2 keeps layout optimal. If Tier 2 is triggered too frequently, it invalidates the cache through reorganization — so triggering policy matters.

---

## 4 Related Work Positioning

Table 1 situates AdaptiveCache in the space of context management approaches.

**Table 1: Context management approaches**

| System | Mechanism | Layout-aware | Prefix-cache-aware | Training |
|---|---|---|---|---|
| ReAct [12] | Full context | No | No | No |
| H2O [1] | Attention eviction | No | No | No |
| SnapKV [3] | Prompt-phase eviction | No | No | No |
| StreamingLLM [5] | Sink + window | No | No | No |
| LLMLingua [13] | Perplexity compression | No | No | No |
| ReSuM [6] | Plug-in summarizer | No | No | Optional |
| Context Folding [14] | Branch/return sub-trajectories | No | Partial | Yes (RL) |
| SUPO [7] | RL summarization | No | No | Yes (GRPO) |
| MEM1 [15] | Constant-memory state replacement | No | No | Yes (PPO) |
| **AdaptiveCache** | Layout + eviction | **Yes** | **Yes** | No (heuristic) |

The two most important comparisons:
- **ReSuM training-free** [6]: same modularity profile (drop-in, no agent retraining), both training-free, but ReSuM generates new summary tokens that invalidate the prefix. Direct apples-to-apples comparison at the mechanism level.
- **Context Folding + FoldGRPO** [14]: best task performance (58% SWE-bench Verified), but requires RL training. Performance ceiling.

**Novel agentic signals.** No existing paper uses reference counting or tool-call dependency graph centrality as importance signals. These are unique to agentic contexts and appear significantly more informative than generic attention metrics for coding tasks: a file opened 15 steps ago with zero recent attention is still critical if it's being actively edited.

---

## 5 Key Technical Challenges

**Challenge 1: Hole-leaving without `cache_edits`.**  
True positional-hole eviction requires the server to expose a mechanism to delete specific cached positions without touching the rest. Anthropic's `cache_edits` does this but is internal. Three approximation options:

- *Option A (True holes):* Pass explicit attention masks that mark evicted positions as non-attendable. Supported by vLLM via custom attention masks.
- *Option B (Prefix rebuild):* Reconstruct prompt with pinned items at prefix, omit evicted items. Prefix bytes stay identical; suffix positions shift.
- *Option C (Placeholders):* Replace evicted blocks with `[EVICTED]` token. Changes byte sequence.

Options A and B are both prefix-cache-friendly; the gap between them quantifies how much the within-step hole mechanism matters vs. the cross-step layout mechanism.

**Challenge 2: Attention weight access.**  
Standard API calls do not return attention weights. Option A (cumulative attention signal) requires a local open-weight model. For API-based deployment, reference counting and structural priors are the only available signals — which may be sufficient for the 100% milestone.

**Challenge 3: Layout reorganization cost.**  
Tier 2 reorganization changes the byte sequence of the prefix, triggering a cache miss on the next step. The reorganization must be infrequent enough that it pays for itself through subsequent cache hits. The break-even point depends on how many steps elapse between reorganizations — an empirical question.

**Challenge 4: ScissorHands persistence question.**  
ScissorHands [2] shows >95% overlap between first-half and second-half important token sets on language modeling benchmarks. If this holds on SWE-bench coding tasks, the importance structure is stable enough to score once per N steps rather than every step — significantly reducing overhead. This needs empirical validation on coding traces.

---

## 6 Evaluation Plan

### 6.1 Setup

**Primary model:** Qwen2.5-7B-Instruct (single A100, ~4 min/instance)  
**Benchmark:** SWE-bench Lite [16] — 300 instances, real GitHub issues requiring multi-step code edits  
**Fixed evaluation subset:** 100 stratified instances (selected after baseline run to ensure context budget pressure)  
**Agent framework:** OpenHands with custom eviction hooks between steps

**Why 7B:** SWE-bench results on 7B are lower in absolute terms but the *relative* differences between context management strategies are the metric of interest. 7B enables rapid iteration within compute budget.

### 6.2 Metrics

- **Task success rate** — `resolved` / `unresolved` on SWE-bench (primary)
- **Prefix cache hit rate** — `cached_tokens / total_input_tokens` per step (core AdaptiveCache claim)
- **Token cost per instance** — total input + output tokens × price
- **Eviction decision quality** — oracle recall (do we keep the blocks that later receive attention?)

### 6.3 Experiments

**Phase 1 — Measurement (→ 75% milestone):**

*Experiment 1.1:* Vanilla ReAct on 100 instances, full context, with attention logging. Establishes baseline prefix cache hit rate. **If cache hit rate is already >60%, the layout hypothesis is weaker than expected.**

*Experiment 1.2:* FIFO, LRU, and Fixed Window at 64K and 32K token budgets. Establishes naive eviction Pareto frontier. Task success rate vs. token cost at each budget.

**Phase 2 — Core AdaptiveCache (→ 100% milestone):**

*Experiment 2.1 (layout isolation):* Same scoring policy, two layout strategies (original order vs. importance-ranked prefix). Primary metric: prefix cache hit rate per step. This isolates the layout contribution from the selection contribution.

*Experiment 2.2 (signal ablation):* Three configs — structural prior only, + reference count + variance, full scorer — on 50-instance subset at 64K budget.

*Experiment 2.3 (full comparison):* Best AdaptiveCache config vs. FIFO / LRU / SnapKV / ReSuM on 100 instances at 64K and 32K budgets. Pareto curve: token cost vs. task success rate.

**Phase 3 — Learned Signals (→ 125% milestone):**

*Experiment 3.1:* Oracle label collection from Phase 1 attention traces. Oracle importance = max attention received from any future step. Used as supervision for MLP scorer.

*Experiment 3.2:* Lightweight MLP scorer (3 layers, 256 hidden, pairwise ranking loss) trained on oracle labels. Compare to heuristic scorer on eviction decision quality and task success.

### 6.4 Baselines

| Baseline | Why |
|---|---|
| Full context (ReAct) | Upper bound on task performance |
| FIFO / LRU | Naive eviction; 75% milestone |
| Fixed window | StreamingLLM-style recency; zero importance awareness |
| SnapKV | Best training-free KV eviction; isolates layout contribution |
| ReSuM training-free | Primary competitor; same modularity, summarization mechanism |

### 6.5 Compute Budget

~89 A100 hours total across all experiments (see Table 2). $100 Anthropic API credits reserved for: (a) real `cache_read_input_tokens` measurement on 10 Sonnet instances — the only way to observe actual server-side prefix cache hit rates, and (b) ~20 Haiku instances for frontier model sanity check.

**Table 2: Compute budget**

| Experiment | Instances | Hours (7B, 1×A100) |
|---|---|---|
| 1.1 Vanilla baseline | 100 | 7 |
| 1.2 FIFO + LRU + FixedWindow | 3 × 100 | 21 |
| 2.1 Layout isolation | 2 × 50 | 7 |
| 2.2 Signal ablation (3 configs) | 3 × 50 | 10 |
| 2.3 Hole-leaving options A/B/C | 3 × 50 | 10 |
| 2.4 Full comparison (+ SnapKV, ReSuM) | 5 × 100 | 34 |
| 3.1 Oracle labels | — | 0 |
| 3.2 MLP scorer training | — | 0.5 |
| 3.3 Learned vs. heuristic | 2 × 50 | 7 |
| **Total** | | **~89 hours** |

---

## 7 Challenges and Risks

**Primary risk: layout hypothesis may not hold.** If vanilla ReAct already achieves high prefix cache hit rate (Experiment 1.1), the layout optimization contributes little. Mitigated by measuring first before building.

**Secondary risk: 7B capability floor.** If Qwen2.5-7B cannot solve enough SWE-bench instances to produce meaningful task success rate differences, results may be noise-dominated. Mitigated by reporting both task success rate and prefix cache hit rate — the latter is a direct measurement independent of model capability.

**Implementation risk: hole-leaving approximation.** If Option A (true holes via attention masks) is not stable with vLLM, fall back to Option B (prefix rebuild). Option B is sufficient for the 100% milestone.

---

## 8 Preliminary Results

Implementation is in progress. The evaluation harness (vLLM serving, OpenHands agent loop, attention logging hook, block segmentation parser, eviction policy interface) is being set up. Experiment 1.1 (vanilla baseline) will run first; preliminary cache hit rate numbers are expected within the next week.

The literature synthesis constitutes substantial preliminary work: the 2D importance × stability framework, the three-tier pipeline, and the identified layout gap are novel framings not present in any individual paper. Table 1 is a contribution of the survey itself.

---

## 9 References

[1] Zhang et al., "H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models," NeurIPS 2023.  
[2] Liu et al., "ScissorHands: Exploiting the Persistence of Importance Hypothesis for LLM KV Cache Compression," NeurIPS 2023.  
[3] Li et al., "SnapKV: LLM Knows What You are Looking for Before Generation," 2024.  
[4] Cai et al., "PyramidKV: Dynamic KV Cache Compression based on Pyramidal Information Funneling," 2024.  
[5] Xiao et al., "Efficient Streaming Language Models with Attention Sinks," ICLR 2024.  
[6] Wu et al., "ReSuM: Plug-and-Play Context Management for LLM Agents," 2025.  
[7] Lu et al., "Scaling LLM Multi-Turn RL with End-to-End Summarization," 2025.  
[8] Anthropic, "Server-Side Compaction API," Beta 2026.  
[9] Anthropic, "Claude Code Compaction Pipeline," Internal Technical Reference, 2025.  
[10] ForesightKV, arXiv:2602.03203.  
[11] PagedEviction, arXiv:2509.04377, EACL 2026.  
[12] Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," ICLR 2023.  
[13] Jiang et al., "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models," EMNLP 2023.  
[14] ByteDance Seed, "Context Folding," arXiv:2510.11967, 2025.  
[15] Zhou et al., "MEM1: Memory-Efficient Reasoning for Long-Horizon Agents," 2025.  
[16] Jimenez et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024.
