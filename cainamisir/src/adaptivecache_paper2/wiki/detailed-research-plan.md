---
title: AdaptiveCache — Detailed Research Plan with Compute Budget
type: synthesis
tags: [adaptivecache, research-plan, baselines, swe-bench, evaluation, compute-budget, a100]
sources: [research-plan, importance-scoring, kv-cache-architecture, overview]
date_created: 2026-04-05
date_updated: 2026-04-05
---

# AdaptiveCache: Detailed Research Plan with Compute Budget

**Total compute budget:**
- A100 GPU hours: ~500–700 (estimated usage; see per-phase breakdown)
- Anthropic API credits: $100

**Model strategy:**
- All development, ablations, and full-scale experiments: **Qwen2.5-7B-Instruct** (single A100) for speed and cost
- Final validation and publication-quality comparison: **Qwen2.5-72B-Instruct** (4×A100) and **Claude Sonnet 4.6** (API)
- Attention weight access: only possible with local models — this is the primary reason to use the A100 at all

**Why Qwen2.5-7B for development:**
Qwen2.5-7B runs at ~100 tok/sec generation on a single A100. Per SWE-bench instance (~15 steps, ~15K avg input, ~800 tok output): prefill ≈ 112 sec, generation ≈ 120 sec → ~4 min/instance. This makes 300 instances ≈ 20 hours on 1 A100, and a 50-instance ablation ≈ 3.5 hours. At 72B (4×A100): ~10 min/instance, 300 instances ≈ 50 hours = 200 A100 hours.

---

## Phase 0 — Environment Setup

**Goal:** Working infrastructure before any experiment runs.

### 0.1 Model serving

Set up vLLM with Qwen2.5-7B-Instruct:
```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --enable-prefix-caching \
  --gpu-memory-utilization 0.9 \
  --max-model-len 131072
```

Enable prefix caching in vLLM — this gives you a local proxy for Anthropic-style cache hit rates via vLLM's `cached_tokens` field in the response.

### 0.2 SWE-bench harness

Use OpenHands as the agent framework. Install SWE-bench Lite evaluation environment (Docker-based, one container per instance). Confirm that the harness:
- Returns `resolved` / `unresolved` per instance
- Exposes step-by-step context for logging
- Can be interrupted to inject eviction hooks between steps

### 0.3 Attention logging hook

Write a vLLM/transformers hook that intercepts each forward pass and logs:
```python
{
  "instance_id": str,
  "step": int,
  "block_id": int,          # logical block (tool call, turn) that this token belongs to
  "token_position": int,
  "attention_received": float,  # sum of attention received from all future tokens in this step
  "layer": int,
  "head": int
}
```

Log to a per-instance JSONL file. Compress after each run. Expected size: ~500MB per 300-instance run at 7B.

### 0.4 Block segmentation

Write a parser that takes the raw message list and segments it into logical blocks:
- Tool call + result = one block
- System prompt = one block  
- Each reasoning step = one block
- Assign block type (from the structural taxonomy in `importance-scoring.md`)

This parser is used by every downstream experiment.

### 0.5 Eviction policy interface

Define a clean interface that every eviction policy implements:
```python
class EvictionPolicy:
    def score_blocks(self, blocks: List[Block], step: int) -> Dict[int, float]
    def select_evict(self, blocks, scores, budget: int) -> Set[int]
    def reorder(self, blocks, scores) -> List[Block]  # for layout optimizer
```

All policies (FIFO, LRU, AdaptiveCache variants) implement this interface. This makes ablations a parameter swap, not a code rewrite.

**Compute: 0 GPU hours (setup only). Time: 3–5 days.**

---

## Phase 1 — Measurement and Baselines (→ 75% Milestone)

**Goal:** Understand the problem empirically. Establish the Pareto frontier for naive eviction. Validate structural priors. The research direction lives or dies here — measure before building.

### Experiment 1.1 — Vanilla ReAct Baseline (300 instances, full context)

**Model:** Qwen2.5-7B-Instruct  
**Instances:** All 300 SWE-bench Lite  
**Context budget:** Unlimited (full context, no eviction)

**What to log:**
- Per-step context length in tokens (breakdown: system prompt / existing context / new tool result)
- Attention weights per logical block per step (from hook in 0.3)
- `cached_tokens` from vLLM (proxy cache hit rate)
- Task success: `resolved` / `unresolved`
- Total tokens consumed per instance (input + output)

**Output:** 300 JSONL files with full attention traces + summary CSV:

| instance_id | steps | final_context_len | task_success | cached_tokens_pct | total_input_tokens |
|---|---|---|---|---|---|

**Why this is the most important experiment in Phase 1:** it tells you whether the problem is real at 7B. If vanilla ReAct already has 70% prefix cache hit rate on 7B (unlikely, but possible), the layout hypothesis is less compelling. If it's 10–20% (expected), the opportunity is large.

**Compute:** 300 instances × 4 min = 20 hours on 1 A100.

---

### Experiment 1.2 — Structural Prior Validation (analysis, no compute)

Using the attention traces from 1.1, compute per-block-type attention statistics:

For each block type in the taxonomy (function defs, bash outputs, error messages, etc.):
1. **Mean cumulative attention** — averaged across steps and instances
2. **Attention variance** — within-block and across-step variance
3. **Survival rate** — how many steps does each block type persist in context before being evicted under FIFO?
4. **Reference rate** — for each block, count how many subsequent tool calls or grep patterns cite content from that block

**Hypothesis to validate:**
- Function definitions > bash outputs > error messages on cumulative attention (structural prior order from `importance-scoring.md`)
- Importance variance is lower for function defs than for error messages
- Reference count is correlated with cumulative attention (r > 0.5)

**If the hypothesis fails:** revise the structural stability priors before Phase 2. The scoring function is only as good as its priors.

**Output:** validation table comparing predicted vs empirical stability. File as an appendix in this wiki.

**Compute:** 0 GPU hours (pure analysis on logged data).

---

### Experiment 1.3 — Naive Eviction Baselines (4 conditions × 300 instances)

**Model:** Qwen2.5-7B-Instruct  
**Instances:** All 300 SWE-bench Lite  
**Budget levels:** 128K, 64K, 32K tokens (and unlimited as control = Experiment 1.1)

**Policies:**

**FIFO:** Evict oldest logical block when context > budget. Track blocks by insertion order.

**LRU:** Evict least-recently-referenced logical block. A block is "referenced" when any of its content appears in the model's subsequent output (detected via string matching).

**Fixed Window:** Keep the last N tokens (StreamingLLM-style). Drop everything outside the trailing window.

**Oracle FIFO (analysis only):** Post-hoc — using attention traces from 1.1, identify the optimal eviction set at each step (blocks that receive zero attention for the rest of the trajectory). Report what task success rate would be under oracle eviction. This establishes the theoretical ceiling for any eviction policy on this problem.

For each policy × budget:
- Task success rate (primary)
- Prefix cache hit rate (vLLM `cached_tokens` pct)
- Mean token cost per instance
- Per-instance: did the eviction happen at all? (some instances are short enough to never hit the budget)

**Compute:** 3 policies × 3 budgets × 300 instances × 4 min = 54 hours on 1 A100.  
Plus 1 unlimited run (Experiment 1.1) already counted.  
**Phase 1 total: ~74 A100 hours.**

---

### Experiment 1.4 — SWE-bench Instance Selection for Ablations

Using Phase 1 data, select a **fixed 50-instance ablation subset** for Phase 2:

Selection criteria:
- Instances that actually hit the 64K token budget (context management has an effect)
- Mix of resolved and unresolved in the full-context baseline
- Mix of easy / hard (by estimated token trajectory length)
- Reproducible: fix this subset before Phase 2 begins and never change it

This 50-instance set is used for all Phase 2 ablations. Full 300-instance runs are reserved for final comparisons only.

**Compute: 0 GPU hours (selection from existing data).**

---

## Phase 2 — Core AdaptiveCache (→ 100% Milestone)

**Goal:** Implement the layout optimizer + hole-leaving eviction. Isolate the contribution of layout. Ablate each scoring signal. Beat FIFO/LRU on cost-performance Pareto.

### Experiment 2.1 — Isolate the Layout Contribution

Before ablating scoring signals, confirm that layout optimization actually helps. This experiment separates the eviction policy from the layout policy.

**Two conditions on 50-instance subset at 64K budget:**

| Config | Eviction policy | Layout policy |
|---|---|---|
| AC-Score-NoLayout | Structural prior score | Original insertion order (no reordering) |
| AC-Score-Layout | Structural prior score | Reorder by importance × stability (pinned prefix zone) |

Both configs evict the same blocks. The only difference is whether surviving blocks are reordered to form a stable byte-identical prefix.

**Primary metric:** prefix cache hit rate per step (not task success — this directly measures the layout effect).

**Secondary metric:** task success rate (should be similar; if Layout hurts task performance, something is wrong).

**If Layout does not improve cache hit rate:** the "never renumber" constraint is already being satisfied incidentally by the baseline, or vLLM's prefix caching is doing something unexpected. Investigate before proceeding.

**Compute:** 2 configs × 50 instances × 4 min = ~7 hours on 1 A100.

---

### Experiment 2.2 — Scoring Signal Ablation

Build up the scorer incrementally. 6 conditions on 50-instance subset at 64K budget:

| Config | Signals active |
|---|---|
| A0 | Structural type prior only |
| A1 | + Reference count |
| A2 | + Importance variance |
| A3 | + Cumulative attention (requires attention hook) |
| A4 | + Dependency graph centrality |
| A5 (full) | All signals + sink-aware layout |

For each config:
- Task success rate
- Prefix cache hit rate per step
- Eviction decisions: what percentage of eviction decisions match the oracle (from Experiment 1.3)?

**Key finding to look for:** does adding cumulative attention (A3) meaningfully improve over A2? If A2 and A3 are within noise, the attention access requirement (which forces local models) may not be worth the infrastructure complexity.

**Compute:** 6 configs × 50 instances × 4 min = ~20 hours on 1 A100.

---

### Experiment 2.3 — Budget Sweep on Best Config

Using the best scoring config from Experiment 2.2, run the full budget sweep:

**Conditions:** 4 budget levels × 300 instances

| Budget | Expected behavior |
|---|---|
| 128K | Most instances unaffected; minimal eviction |
| 64K | Moderate pressure; differences start to emerge |
| 32K | Significant eviction; important vs. unimportant discrimination matters |
| 16K | Aggressive eviction; tests graceful degradation |

**For each condition:**
- Task success rate
- Prefix cache hit rate per step
- Mean token cost per instance
- Plot: cost vs. task success rate (Pareto curve)

**Compute:** 4 budgets × 300 instances × 4 min = ~80 hours on 1 A100.

---

### Experiment 2.4 — Hole-Leaving Options A/B/C

The key infrastructure question from `research-plan.md` (Options A/B/C):

**Option A — True hole-leaving:** omit evicted blocks from the prompt, keep positional metadata — surviving blocks stay at absolute positions. This is what `cache_edits` does.

**Option B — Prefix rebuild:** put pinned items at the prefix, omit evicted items entirely, append volatile suffix. Prefix bytes are identical across steps; suffix items shift positions.

**Option C — Placeholder replacement:** replace evicted blocks with `[EVICTED: <block_type> at position <N>]` (1 token). Changes byte sequence slightly but tells the model what was removed.

**Conditions:** A/B/C × 50 instances × 64K budget

**Primary metric:** prefix cache hit rate (A should be highest if true holes work; B should be next; C may vary)

**Secondary metric:** task success rate (do holes confuse the model?)

**How to implement A with a local model:** vLLM doesn't expose `cache_edits`. Approximate by passing the attention mask explicitly — mark evicted positions as non-attendable. This is non-standard but doable with vLLM's `attention_mask` parameter.

**Compute:** 3 options × 50 instances × 4 min = ~10 hours on 1 A100.

---

### Experiment 2.5 — Full Comparison vs All Baselines (300 instances, 64K budget)

Best AdaptiveCache config (from 2.2 + 2.3 + 2.4) vs all baselines at the 64K budget level:

| System | Source |
|---|---|
| Full context (ReAct) | Experiment 1.1 (reuse data) |
| FIFO | Experiment 1.3 (reuse data) |
| LRU | Experiment 1.3 (reuse data) |
| Fixed window | Experiment 1.3 (reuse data) |
| SnapKV | New run (needs implementation) |
| ReSuM training-free | New run (needs ReSumTool-30B or approximation) |
| AdaptiveCache (full config) | New run |

**Note on SnapKV:** SnapKV is a prompt-phase eviction method (run once before generation). Adapt it to agentic multi-step: at each step boundary, re-run SnapKV's observation window scoring on the accumulated context. This is the fairest comparison.

**Note on ReSuM training-free:** ReSumTool-30B is the fine-tuned Qwen model. If unavailable, approximate with Qwen2.5-7B prompted as a summarizer (explicit disclaimer in paper). Alternatively, use a prompted Llama-3.1-8B-Instruct as the summarizer — the key is to measure whether summarization (new tokens, cache invalidated) vs eviction (no new tokens, cache preserved) is the dominant effect.

**Compute:**
- SnapKV (new): 300 instances × 4 min = 20 hours
- ReSuM approx (new): 300 instances × 4 min + summarizer overhead = ~30 hours (summarizer adds ~1 min/step)
- AdaptiveCache full (new): 300 instances × 4 min = 20 hours
- All others: reuse Phase 1 data

**Phase 2 total: ~187 A100 hours.**

---

## Phase 3 — Learned Signals (→ 125% Milestone)

**Goal:** Replace hand-tuned weights with learned weights. Add Expected Attention and low-entropy protection. Show that training improves over heuristic.

### Experiment 3.1 — Oracle Label Collection (from Phase 1 traces, no compute)

From Phase 1's attention traces, compute oracle importance labels:

```python
# For block b at step t, oracle importance =
# max attention received from any future step t' > t
oracle_importance(b, t) = max(attention_received(b, t') for t' > t in trajectory)
```

This gives (block_features, oracle_importance) pairs for all 300 × ~15 steps × ~10 blocks per step ≈ 45,000 training examples. Split 250-instance / 50-instance train / val.

**Block features to extract:**
- Structural type (one-hot, 10 categories)
- Token length
- Position in context (normalized)
- Age in steps
- Reference count so far
- Cumulative attention so far (running mean)
- Importance variance so far
- Dependency graph centrality (in-degree)

**Compute: 0 GPU hours (post-processing of logged data).**

---

### Experiment 3.2 — Train Lightweight Scorer

Train a small MLP (3 layers, 256 hidden units) on the oracle labels:

```python
# Loss: pairwise ranking loss
# For blocks i, j at same step:
# if oracle_importance(i) > oracle_importance(j):
#   loss += max(0, margin - (score(i) - score(j)))
```

This is a standard learning-to-rank setup. The model is tiny — training takes minutes on a single A100.

**Training setup:**
- 45,000 training pairs (sample 1 pair per block per step)
- 5,000 val pairs
- Batch size 512, Adam, 50 epochs, early stopping on val ranking accuracy
- Expected training time: ~30 minutes on 1 A100

**Ablation:** train with all features vs. without attention features (to quantify how much attention access helps the learned model vs. purely structural features).

**Compute: ~0.5 A100 hours.**

---

### Experiment 3.3 — GRPO Refinement (Advantage Broadcasting)

After the supervised scorer is trained, refine it with GRPO using task outcome as reward:

**Setup:**
- 200 training instances from SWE-bench Lite (not in test set)
- K=4 rollouts per instance with different random seeds (temperature=0.7)
- Reward = 1.0 if resolved, 0.0 if unresolved, + 0.1 × token_savings_fraction
- For each eviction decision in the rollout: advantage = reward - mean(reward across K rollouts)
- GRPO update on the scorer MLP using the advantages

**Batching:** run all K rollouts for all 200 instances in parallel (if GPU memory allows). With 7B and K=4: 200 instances × 4 × 4 min ≈ 53 hours.

**Checkpoint at each epoch.** Run val set (50 instances) after each epoch. Early stop when val task success plateaus.

Expected training: 3–5 epochs. ~160–265 hours at 7B.

This is the expensive part. Scope it: start with K=2 (halves compute) and 100 training instances if needed.

**Compute (aggressive): 200 instances × K=4 × 4 epochs × 4 min = ~213 A100 hours.**  
**Compute (scoped): 100 instances × K=2 × 3 epochs × 4 min = ~40 A100 hours.**

**Recommendation:** start scoped. Run 1 epoch to confirm GRPO loss is decreasing before committing to full training.

---

### Experiment 3.4 — Learned vs Heuristic Comparison

Using the trained scorer (post-GRPO), compare against heuristic AdaptiveCache on the held-out 50-instance test set:

| Config | Scorer | Budget |
|---|---|---|
| Heuristic AdaptiveCache | Hand-tuned weights | 64K |
| Learned AdaptiveCache (supervised only) | MLP, pairwise ranking loss | 64K |
| Learned AdaptiveCache (GRPO-refined) | MLP + GRPO | 64K |
| ForesightKV (closest learned competitor) | As described in arXiv:2602.03203 | 64K |
| ReSuM training-free | Prompted summarizer | 64K |

**Primary metric:** task success rate delta over heuristic AdaptiveCache.

**Compute:** 5 configs × 50 instances × 4 min = ~17 A100 hours.

---

### Experiment 3.5 — Compaction Aggressiveness Sweep

Using the best learned scorer, sweep the eviction aggressiveness parameter:

```
Keep top {90%, 75%, 50%, 25%, 10%} of blocks by score at each step
```

For each aggressiveness level: task success rate + token cost. This produces the final cost-performance Pareto curve for the learned system.

**Compute:** 5 levels × 300 instances × 4 min = ~100 A100 hours.

---

### Experiment 3.6 — 72B Validation

Run the best heuristic and best learned AdaptiveCache config on Qwen2.5-72B to confirm that results transfer from 7B to 72B.

Use the 50-instance ablation subset only (full 300 instances too expensive).

**Comparison conditions:**
- Full context (ReAct) at 72B
- FIFO at 64K budget at 72B
- AdaptiveCache heuristic at 64K budget at 72B
- AdaptiveCache learned at 64K budget at 72B

**Compute:** 4 configs × 50 instances × 10 min = ~33 wall-clock hours × 4 GPUs = **133 A100 hours.**

**Phase 3 total (aggressive): ~465 A100 hours.**  
**Phase 3 total (scoped): ~295 A100 hours.**

---

## API Validation ($100 budget)

Two things the local model cannot provide:

### API Experiment A — Real Prefix Cache Hit Rate ($20)

Run 10 instances on Anthropic Sonnet 4.6. For each instance:
- Full context (ReAct) baseline
- AdaptiveCache heuristic at 64K budget

Log `cache_read_input_tokens` from the usage object at each step. This is the only way to measure actual server-side prefix cache hits.

**Expected finding:** AdaptiveCache should show higher `cache_read_input_tokens / total_input_tokens` ratio across steps, especially after step 3 once the prefix stabilizes.

**Cost estimate:** 10 instances × 2 conditions × $2.50/instance (Sonnet, assuming partial caching) ≈ **$50.**

### API Experiment B — Frontier Model Task Performance ($50)

Run the headline comparison on a fixed 20-instance subset using Haiku 4.5 (cheaper, still frontier):

| System | Cost/instance |
|---|---|
| Full context (ReAct) | ~$0.80 |
| FIFO 64K | ~$0.50 |
| AdaptiveCache heuristic 64K | ~$0.45 |
| AdaptiveCache learned 64K | ~$0.45 |
| ReSuM training-free 64K | ~$0.60 (summarizer adds tokens) |

20 instances × 5 conditions × $0.56 avg ≈ **$56.**

**Total API: ~$106 — slightly over. Cut to 18 instances, or use Haiku for A, not Sonnet.**

---

## Full Compute Budget Summary

| Phase | Experiments | A100 Hours | Notes |
|---|---|---|---|
| 0 | Setup | 0 | Dev time only |
| 1 | Baseline measurement | 74 | 1 A100, 7B model |
| 2 | Core AdaptiveCache | 187 | 1 A100, 7B model |
| 3 (scoped) | Learned signals + 72B validation | 295 | Mix of 1×A100 (7B) and 4×A100 (72B) |
| **Total (scoped)** | | **~556 A100 hours** | |
| 3 (aggressive) | Learned signals + 72B validation | 465 | Full GRPO training |
| **Total (aggressive)** | | **~726 A100 hours** | |

**Recommendation: plan for 600 A100 hours, use scoped GRPO (K=2, 100 instances) unless early results justify expansion.**

The 72B validation (133 A100 hours) is the single most expensive item. If compute is tight, cut the 72B run to 20 instances (53 hours) — enough to confirm the trend holds.

---

## Timeline

| Week | Phase | Deliverable |
|---|---|---|
| 1 | 0 | Working harness, attention hook, block parser, eviction interface |
| 2–3 | 1 | Full vanilla ReAct baseline (300 instances logged) |
| 3 | 1 | Structural prior validation (analysis) |
| 4–5 | 1 | FIFO/LRU/FixedWindow baselines + Pareto curves → **75% milestone** |
| 5–6 | 2.1–2.2 | Layout isolation + scoring signal ablation (50-instance subset) |
| 6–7 | 2.3–2.4 | Budget sweep + hole-leaving options |
| 7–8 | 2.5 | Full comparison vs all baselines → **100% milestone** |
| 8–9 | 3.1–3.2 | Oracle label collection + MLP scorer training |
| 9–11 | 3.3 | GRPO training (most time-consuming step) |
| 11–12 | 3.4–3.5 | Learned vs heuristic comparison + aggressiveness sweep |
| 12–13 | 3.6 | 72B validation → **125% milestone** |
| 13 | API | Anthropic cache hit rate measurement + frontier model validation |

---

## What to Cut If Compute Is Short

Priority order (cut from bottom):

1. **Keep:** Experiment 1.1 (vanilla baseline) — everything depends on this
2. **Keep:** Experiment 1.3 (FIFO/LRU/FixedWindow) — these are the 75% milestone
3. **Keep:** Experiment 2.1 (layout isolation) — validates the core hypothesis
4. **Keep:** Experiment 2.2 (scoring signal ablation, 50 instances) — justifies the scoring function
5. **Keep:** Experiment 2.5 (full comparison vs baselines, 300 instances) — headline result
6. **Reduce:** Experiment 2.3 (budget sweep) — cut to 2 budget levels instead of 4
7. **Reduce:** Experiment 3.3 (GRPO) — use scoped version (K=2, 100 instances)
8. **Cut if needed:** Experiment 3.5 (aggressiveness sweep) — informative but not essential
9. **Cut if needed:** Experiment 3.6 (72B validation) — strong to have, not required for 125% milestone

---

## Dependencies Graph

```
1.1 (baseline) ──→ 1.2 (prior validation) ──→ 2.2 (signal ablation)
              ↘                                         ↓
               1.3 (FIFO/LRU) ──→ 1.4 (subset selection) ──→ 2.1, 2.3, 2.4
                                                              ↓
                                                        2.5 (full comparison)
                                                              ↓
1.1 traces ──→ 3.1 (oracle labels) ──→ 3.2 (MLP training) ──→ 3.3 (GRPO) ──→ 3.4
                                                                              ↓
                                                                         3.5, 3.6
```

Nothing in Phase 2 or 3 can start until Phase 1 is complete. Within Phase 2, Experiments 2.1 and 2.2 can run in parallel (both use the 50-instance subset). Experiment 2.5 requires 2.2 to be complete (to select the best config).

---

## Key Decision Points

**After Experiment 1.1:** Is the baseline prefix cache hit rate low (< 30%)? If yes, proceed. If > 60%, reconsider whether layout optimization is the right lever.

**After Experiment 1.2:** Do structural priors match empirical attention? If not, revise the stability table in `importance-scoring.md` before running Phase 2.

**After Experiment 2.1:** Does layout optimization actually improve cache hit rate? If not (< 5% improvement), there is a fundamental implementation issue to debug before proceeding.

**After Experiment 2.2:** Which signals contribute? If cumulative attention (A3) does not improve over A2, consider whether attention access is worth the infrastructure cost for Phase 3.

**After Experiment 3.2:** Does the learned scorer outperform A2 on oracle recall? If not, the training signal is noisy and GRPO training (Experiment 3.3) is unlikely to help — skip it.

---

## Related Pages

- [research-plan.md](research-plan.md) — original three-phase plan (this document supersedes it)
- [importance-scoring.md](importance-scoring.md) — scoring signals being ablated in 2.2
- [kv-cache-architecture.md](kv-cache-architecture.md) — hole-leaving eviction options A/B/C
- [overview.md](overview.md) — system design
- [resum.md](resum.md) — primary modular baseline
- [context-folding.md](context-folding.md) — performance ceiling baseline
