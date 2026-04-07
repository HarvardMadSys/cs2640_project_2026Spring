---
title: AdaptiveCache — System Design Specification
type: synthesis
tags: [adaptivecache, system-design, layout-optimization, eviction, prefix-caching, scoring, cost-model]
sources: [overview, research-plan, detailed-research-plan, importance-scoring, kv-cache-architecture, prefix-caching, context-management, claude-code-compaction, anthropic-compaction, openhands-condensation, streamingllm, h2o, scissorhands, snapkv, pyramidkv, lost-in-middle, llmlingua, infini-transformer, context-folding, resum, summarization-rl, mem1, memagent, react]
date_created: 2026-04-05
date_updated: 2026-04-05
---

# AdaptiveCache: System Design Specification

The authoritative technical specification for AdaptiveCache. This document bridges the project overview ([overview.md](overview.md)) and the experimental plan ([detailed-research-plan.md](detailed-research-plan.md)). Everything here is derived from the 23 literature sources surveyed in this wiki and first-principles reasoning about the [prefix-caching cost model](prefix-caching.md).

---

## 1. Design Principles

Three axioms constrain every design decision:

**Axiom 1: Prefix stability is the primary cost lever.** The cost of a token depends on whether it falls inside the cached prefix (~10x cheaper) or outside it. Token position is a first-class cost variable. Modifying early tokens invalidates all downstream KV computation; appending to the suffix preserves the cache. ([prefix-caching.md](prefix-caching.md))

**Axiom 2: Layout and eviction must be solved jointly.** Every existing system optimizes *what to keep* but ignores *where surviving items sit*. This is the "layout gap" — the central thesis of AdaptiveCache. Surviving items scatter at original positions, different queries produce different scatter patterns, and no two consecutive calls share a common prefix beyond the system prompt. ([context-management.md](context-management.md); [LongLLMLingua](llmlingua.md) reordering ablation validates empirically that layout matters at constant compression ratio)

**Axiom 3: No new token generation for eviction.** Summarization invalidates the prefix. Hole-leaving eviction is free. The system must never generate new tokens as part of its normal eviction pathway. ([kv-cache-architecture.md](kv-cache-architecture.md); Claude Code `cache_edits` as production precedent)

---

## 2. Architecture Overview

AdaptiveCache operates as a middleware layer between the agent loop ([ReAct](react.md)) and the LLM API. At each step boundary:

```
Agent Step N completes → Observation received
       │
  [1]  Block Segmentation ─── parse new observation into logical blocks
       │
  [2]  Scoring Pipeline ───── compute importance × stability for all blocks
       │
  [3]  Zone Assignment ────── classify each block: PIN / MIDDLE / SUFFIX / EVICT
       │
  [4]  Eviction Engine ────── leave holes for evicted blocks (every step, ~free)
       │
  [5]  Layout Optimizer ───── reorder if importance structure changed (conditional)
       │
  [6]  Prompt Construction ── assemble three-zone layout → LLM API call
       │
Agent Step N+1
```

**Timing:**
- Steps 1–4, 6: every step (cheap — no LLM calls, no recomputation)
- Step 5: only when importance structure changes significantly. [ScissorHands](scissorhands.md) >95% persistence ratio means the top-K set is stable across steps, so layout reorganization should be rare (~every 10–20 steps)

This mirrors [Claude Code's](claude-code-compaction.md) cheapest-first hierarchy (Snip → Microcompact → Context Collapse → Autocompact), where the cheapest mechanisms run every step and expensive ones fire rarely.

---

## 3. Block Model

### 3.1 Block Definition

A block is the atomic unit of context management — a logically coherent unit of content with metadata:

```python
@dataclass
class Block:
    block_id: int                     # unique, monotonically increasing
    block_type: BlockType             # structural taxonomy (see below)
    content: str                      # raw text
    token_span: Tuple[int, int]       # (start, end) absolute positions in context
    step_created: int                 # agent step when this block entered context
    importance: float                 # current importance score (updated each step)
    stability: float                  # current stability score (updated each step)
    zone: Zone                        # PIN | MIDDLE | SUFFIX | EVICT
    reference_count: int              # citations by subsequent tool calls
    attention_history: List[float]    # running window of attention received
```

### 3.2 Block Segmentation Rules

| Agent output | Segmentation | Block type |
|---|---|---|
| System prompt | 1 block | `SYSTEM` |
| Task description / issue | 1 block | `TASK` |
| Thought (reasoning) | 1 block per reasoning step | `THOUGHT` |
| Action (tool call) | 1 block per tool invocation | `ACTION` |
| Observation — file read | 1 block per file; large files may split at function/class boundaries | `OBS_FILE` |
| Observation — shell output | 1 block per command | `OBS_SHELL` |
| Observation — grep/search | 1 block per search result set | `OBS_GREP` |
| Observation — error | 1 block per error | `OBS_ERROR` |

Eviction must be at block boundaries — partial eviction of a logical unit creates disorienting positional gaps. [PagedEviction](kv-cache-architecture.md) aligns to page boundaries for exactly this reason. Block type maps directly to the structural type prior in [importance-scoring.md](importance-scoring.md).

**Open question (Experiment 2.4):** Is tool-call-level granularity sufficient, or does sub-tool-call segmentation (e.g., per-function within a large file read) improve results?

---

## 4. Three-Zone Layout

The context is divided into three contiguous zones:

```
[──── Zone 1: PINNED PREFIX ────][── Zone 2: MIDDLE ──][── Zone 3: VOLATILE SUFFIX ──]
  positions 0 ─────────────── P    P ─────────── M       M ────────────────── end
```

### 4.1 Zone Definitions

| Zone | Contents | Eviction policy | Position stability |
|---|---|---|---|
| **Pinned Prefix** (Zone 1) | Sink tokens (pos 0–3) + system prompt + high importance×stability blocks | Never evicted; only demoted if importance drops below threshold for K consecutive steps | Absolute positions fixed across steps (byte-identical prefix) |
| **Middle** (Zone 2) | Blocks with moderate scores; promotion/demotion candidates | Evicted under budget pressure after Zone 3 exhausted | Positions may shift on layout reorganization |
| **Volatile Suffix** (Zone 3) | Most recent observations; high-importance but low-stability blocks (hot but volatile) | Evicted first via hole-leaving; newest appended at end | New content always appended here |

### 4.2 Placement Rules

1. **Sink tokens (positions 0–3):** Always present. Never moved. SoftMax attention dump positions. ([StreamingLLM](streamingllm.md): first 4 tokens receive disproportionate attention; removal causes perplexity collapse)

2. **System prompt (positions 4–N):** Immediately after sinks. Byte-identical across all steps. Already cached by default on all API providers.

3. **Pinned high-value blocks (positions N+1 to P):** Ordered by `importance × stability` descending within the zone. Highest-value content placed at positions immediately after system prompt (~positions 4–100) to exploit primacy bias. ([Lost in the Middle](lost-in-middle.md): 75.8% accuracy at position 1 vs 53.8% at middle)

4. **Middle zone (positions P+1 to M):** Blocks whose zone assignment is uncertain — candidates for promotion (if score stabilizes upward) or demotion (if score drops). Ordered by importance descending.

5. **Volatile suffix (positions M+1 to end):** Most recent agent steps. New observations always appended here. Ordered chronologically (newest last) to exploit recency bias. (Lost in the Middle: 63.2% accuracy at final position)

### 4.3 The "Never Renumber" Invariant

Once a block is placed at an absolute position range, it stays there or becomes a hole. Renumbering surviving tokens after eviction scrambles RoPE embeddings and breaks downstream KV states. [Stateful KV Cache Management](kv-cache-architecture.md) (arXiv:2511.04686): retaining first 2,000 contiguous tokens outperforms sophisticated attention-based eviction retaining 99% of tokens from longer context when positions are scrambled.

### 4.4 Layout Reorganization Triggers

The layout optimizer fires when ANY of:
- More than T blocks change zone assignment in one step
- The top-K importance set changes by >30% since last reorganization
- Hole ratio exceeds compaction threshold (see §6.4)

When it fires, the entire prompt is reconstructed, invalidating the prefix cache for one step. The new layout becomes the stable prefix for all subsequent steps. The goal: reorganize rarely enough that the cache-hit benefit exceeds the one-time invalidation cost. Break-even analysis in §9 shows this requires only ~3 steps of stability.

**Open question (Experiment 2.1):** What is the optimal reorganization frequency? Too frequent (every 3 steps) = marginal cache hits. Too infrequent (every 20 steps) = stale layout.

---

## 5. Scoring Pipeline

### 5.1 Two-Dimensional Framework

Every block is scored on two independent axes — importance (what to keep) and stability (what to pin early). The 2×2 matrix determines zone assignment:

```
                    HIGH STABILITY
                         │
    PIN (Zone 1)         │   PIN (Zone 1)
    (stable, modest)     │   (stable + critical)
                         │
LOW ─────────────────────┼───────────────────── HIGH
IMPORTANCE               │                IMPORTANCE
                         │
    EVICT                │   SUFFIX (Zone 3)
    (low value)          │   (hot but volatile)
                         │
                    LOW STABILITY
```

See [importance-scoring.md](importance-scoring.md) for full signal descriptions.

### 5.2 Signal Table (by milestone)

| # | Signal | Cost | Milestone | Literature |
|---|---|---|---|---|
| 5 | Structural type prior | Zero (regex/classifier) | 75% | First principles |
| 3 | Reference count | Cheap (string match) | 75% | Novel (agentic) |
| 6 | Importance variance | Cheap (running variance) | 75% | Operationalizes [ScissorHands](scissorhands.md) persistence |
| 2 | Cumulative past attention | Moderate (attention access) | 100% | [H2O](h2o.md) heavy-hitter scoring |
| 7 | Dependency graph centrality | Cheap (DAG in-degree) | 100% | Novel (agentic) |
| 1 | Expected Attention | Moderate (activation stats) | 125% | arXiv:2510.00636 |
| 4 | Low-entropy protection | Moderate (perplexity) | 125% | ForesightKV (arXiv:2602.03203) |

### 5.3 Combined Scoring and Zone Assignment

```python
def importance(block, history):
    return (w1 * structural_prior(block.type)
          + w2 * reference_count(block)
          + w3 * cumulative_attention(block)        # 100% milestone
          + w4 * low_entropy_boost(block))           # 125% milestone

def stability(block, history):
    return (w5 * type_stability(block.type)
          + w6 * (1 - importance_variance(block))
          + w7 * dependency_centrality(block))       # 100% milestone

def assign_zone(block):
    imp, sta = importance(block), stability(block)
    if imp * sta > PIN_THRESHOLD:       return Zone.PIN
    if imp > IMPORTANCE_THRESHOLD:      return Zone.SUFFIX
    if imp * sta > MIDDLE_THRESHOLD:    return Zone.MIDDLE
    return Zone.EVICT
```

Weights are hyperparameters tuned on SWE-bench traces (125% milestone).

### 5.4 Contrastive Deduplication

Before final zone assignment, check semantic overlap: if block B is largely redundant with already-pinned block A (cosine similarity > threshold), downgrade B regardless of absolute importance. Only marginal information value matters. Inspired by [LongLLMLingua](llmlingua.md)'s contrastive perplexity — AdaptiveCache uses embedding similarity as a cheaper proxy.

---

## 6. Eviction Engine

### 6.1 Hole-Leaving Mechanics

When a block is marked for eviction:
1. Its token positions become "holes" — non-attendable, never renumbered
2. Post-rotated RoPE keys mean downstream KV states remain valid
3. No new tokens generated; no recomputation of any kind

Equivalent to Claude Code's `cache_edits` or [PagedEviction](kv-cache-architecture.md)'s block-wise approach.

### 6.2 Eviction Order

Within the EVICT set:
1. Lowest `importance × (1 − stability)` first (least-valuable volatile blocks)
2. Among ties, evict oldest first (FIFO tiebreaker)
3. Never evict Zone 1 blocks

### 6.3 Budget Thresholds

| Condition | Action | Tier |
|---|---|---|
| Context < soft budget (80% of max) | No eviction | — |
| Context ≥ soft budget | Evict Zone.EVICT blocks via hole-leaving | 1 |
| Still over after Zone.EVICT | Evict lowest-scoring Zone.SUFFIX blocks | 1 |
| Still over after all eviction | Compact holes + layout reorganization | 2 |
| Still over after compaction | Emergency summarization (should never happen) | 3 |

### 6.4 Hole Accumulation and Compaction

Over many steps, holes accumulate. When the hole ratio (hole tokens / total allocated positions) exceeds a threshold (e.g., 40%), surviving blocks are re-placed at contiguous positions. This is equivalent to a layout reorganization and invalidates the prefix cache — so it should be infrequent.

**Open question (Experiment 2.4, Options A/B/C):** What hole ratio triggers compaction? The Stateful KV Cache Management result suggests positional integrity tolerates high hole ratios — so the threshold may be higher than intuition suggests.

---

## 7. Layout Optimizer

### 7.1 When It Fires

- The importance structure has changed significantly (>30% of top-K set changed)
- Hole ratio exceeds compaction threshold
- A block has been in the wrong zone for >K consecutive steps

### 7.2 What It Does

1. Re-score all surviving blocks
2. Re-assign zones
3. Construct new prompt layout:
   - Zone 1: `[sinks][system][pinned blocks sorted by imp×sta descending]`
   - Zone 2: `[middle blocks sorted by importance descending]`
   - Zone 3: `[suffix blocks in chronological order, newest last]`
4. All blocks receive new absolute positions
5. Prefix cache invalidated for exactly one step

### 7.3 Prefix Stability Constraint

To maximize byte-level prefix identity across steps:
- Blocks already in Zone 1 that remain in Zone 1 **keep the same absolute positions**
- New promotions to Zone 1 are **inserted at the end of the pinned zone** (before Zone 2 boundary), not in the middle of existing pins
- Demotions from Zone 1 leave holes (not renumbered) until next full compaction

This minimizes the number of prefix bytes that change on reorganization — only the tail of Zone 1 and everything after it changes. Existing pinned content stays byte-identical.

---

## 8. Three-Tier Cost Model

| Tier | Operation | Cost | Frequency | Mechanism |
|---|---|---|---|---|
| 1 | Hole-leaving eviction | ~0 (no recompute, no new tokens) | Every step | Eviction Engine (§6) |
| 2 | Layout reorganization | Moderate (one step of prefix cache miss) | Every ~10–20 steps | Layout Optimizer (§7) |
| 3 | Emergency summarization | Expensive (LLM call, full prefix invalidation, irreversible info loss) | Rarely / never | Fallback only |

**Tier 1 is free** because post-rotated RoPE keys are stored in the KV cache; evicting a position makes it non-attendable but affects no other position. Production evidence: PagedEviction achieves 3.1× throughput; Claude Code `cache_edits` does this for tool results.

**Tier 2 costs one step of cache miss** because the byte sequence of the prefix changes when blocks are reordered. All subsequent steps benefit from the new stable prefix. Amortized cost: (1 miss) / (N steps until next reorg) — small if N is large.

**Tier 3 should never fire** because Tiers 1 and 2 should keep context within budget.

---

## 9. Formal Cost Model

### 9.1 Per-Step Token Cost

```
Cost(step_t) = P_cached × |prefix_hit_t| + P_uncached × |prefix_miss_t + suffix_t|
```

Where:
- `P_cached` = cost per cached token (e.g., $0.30/MTok Anthropic cached input)
- `P_uncached` = cost per uncached token (e.g., $3.00/MTok Anthropic uncached input)
- `|prefix_hit_t|` = tokens in the byte-identical prefix (Zone 1 content matching previous step)
- `|prefix_miss_t + suffix_t|` = everything else (changed Zone 1 + Zone 2 + Zone 3)

### 9.2 Multi-Step Savings

**Without layout optimization:** `H(t) = |system_prompt|` — only the system prompt is byte-identical across steps. All dynamic context is uncached.

**With AdaptiveCache:** `H(t) = |system_prompt| + |pinned_prefix(t)|` — the stable Zone 1 content is also cached. If Zone 1 is stable across M consecutive steps:

```
Savings = (P_uncached − P_cached) × |Zone_1| × M
```

At a 10× cost ratio: savings ≈ `0.9 × P_uncached × |Zone_1| × M`.

### 9.3 Break-Even Analysis

Layout reorganization costs one step of full uncached prefix. Break-even requires:

```
(P_uncached − P_cached) × |Zone_1| × (M − 1) > P_uncached × |Zone_1|
→ M > P_uncached / (P_uncached − P_cached) + 1
→ M > 10/9 + 1 ≈ 2.1 steps
```

**At a 10× cost ratio, layout reorganization pays for itself after just 3 steps of stability.** This is extremely favorable — even frequent reorganization (every 5 steps) yields net savings.

### 9.4 Combined Effect

Hole-leaving eviction additionally reduces total context size `C(t)`, reducing both cached and uncached token costs. The combined effect — smaller context + higher cache hit rate — is **multiplicative**.

---

## 10. Integration Points

### 10.1 ReAct Loop Integration

AdaptiveCache is a drop-in middleware. The [ReAct](react.md) loop is unmodified:

```python
while not done:
    thought = llm(context)
    action = extract_action(thought)
    observation = execute(action)
    context = adaptive_cache.update(context, thought, action, observation)  # ← HERE
```

The `update()` method performs block segmentation, scoring, zone assignment, eviction, and (conditionally) layout optimization, then returns a flat token sequence for the next LLM call.

### 10.2 API-Level Prompt Construction

The prompt sent to the LLM API at each step:

```
[system prompt]                              ← always cached by API
[pinned prefix blocks]                       ← byte-identical to previous step
  ↑ cache_control breakpoint here
[middle zone blocks]
[volatile suffix blocks, incl. new obs]      ← newest content at end
```

For Anthropic API: use `cache_control` breakpoints to mark the end of the pinned prefix. For vLLM: prefix caching is automatic on byte-prefix match.

### 10.3 Attention Access Modes

| Mode | Attention access | Signals available | Use case |
|---|---|---|---|
| **Local model** (Qwen2.5-7B via vLLM) | Full (forward-pass hooks) | All 7 signals | Ablation experiments |
| **API model** (Claude Sonnet) | None | Structural prior + ref count + variance (75% signals only) | Real-world validation |

For API mode, monitor `cache_read_input_tokens` in the usage response to measure actual prefix cache hit rate.

### 10.4 Framework Compatibility

AdaptiveCache's middleware pattern is compatible with:
- **[OpenHands](openhands-condensation.md):** inject between condenser registry and API call
- **Custom ReAct loops:** wrap the prompt construction step
- **Any framework** that exposes the prompt before it's sent to the LLM

---

## 11. Worked Example

A 3-step SWE-bench walkthrough. Task: fix a bug in `utils/parser.py`.

**Step 1:** System prompt + task description + first tool call (grep for error pattern).

```
[Zone 1: SYSTEM + TASK                               ]  [Zone 3: grep result    ]
[sys_prompt][issue: fix parser bug in utils/parser.py ]  [grep: 3 matches found  ]
 pos 0─────────────────────────────────────── 500       500 ─────────────── 800
```

No eviction needed. Zone 1 = system + task (stable by type prior).

**Step 2:** Agent reads `utils/parser.py`. The function definition scores high on importance×stability; promoted to Zone 1. The grep output stays in suffix.

```
[Zone 1: SYSTEM + TASK + parse_input()             ]  [Zone 3: grep + file read     ]
[sys][issue][def parse_input(data): ...            ]  [grep: 3 matches][parser.py...]
 pos 0─────────────────────────────────── 1200       1200 ──────────────────── 3500
```

Prefix cache hits: |system + task| = 500 tokens cached from Step 1.

**Step 3:** Agent edits the file and runs tests. Budget pressure triggers eviction. Grep output evicted (hole left). Function def remains pinned. New observation appended.

```
[Zone 1: SYSTEM + TASK + parse_input()             ]  [  hole  ][Zone 3: edit + test ]
[sys][issue][def parse_input(data): ...            ]  [        ][edit OK][tests pass ]
 pos 0─────────────────────────────────── 1200       1200──1500 1500 ─────────── 2800
```

Prefix cache hits: |system + task + parse_input()| = **1200 tokens cached** — byte-identical to Step 2. Without layout optimization, only the system prompt (~200 tokens) would be cached.

---

## 12. Open Design Questions

| # | Question | Experiment | Notes |
|---|---|---|---|
| 1 | Block granularity: tool-call level vs sub-tool-call? | Phase 2, Exp 2.4 | Tool-call default; function-level split as 125% enhancement |
| 2 | Layout reorganization frequency: what triggers, how often? | Phase 2, Exp 2.1 | Break-even at ~3 steps; 30% top-K change as starting heuristic |
| 3 | Hole ratio tolerance before compaction? | Phase 2, Exp 2.4 (Options A/B/C) | Stateful KV paper suggests high ratios tolerable |
| 4 | PIN_THRESHOLD and IMPORTANCE_THRESHOLD values? | Phase 2, signal ablation | Hyperparameter sweep on SWE-bench traces |
| 5 | Attention vs structural-only scoring improvement? | Phase 2, Exp 2.2 | Does cumulative attention beat structural priors alone? |
| 6 | API hole-leaving: public API support or prompt-rebuild approximation? | Phase 2, Exp 2.4 | `cache_edits` is internal; must test workarounds |
| 7 | Three zones vs two zones (pin + suffix only)? | Phase 2, ablation | Middle zone adds complexity; may not be needed |
| 8 | Primacy zone size: how many positions in the sweet spot? | Phase 1, measurement | [Lost in the Middle](lost-in-middle.md) shows early is best; exact decay curve is model-dependent |

All questions map to specific experiments in [detailed-research-plan.md](detailed-research-plan.md).

---

## 13. Literature Grounding Table

| Design Decision | Primary Source | Key Finding |
|---|---|---|
| Sink tokens at positions 0–3, never evicted | [StreamingLLM](streamingllm.md) (Xiao et al., ICLR 2024) | Attention sinks mandatory; removal causes perplexity collapse |
| Stable prefix hypothesis (top tokens persist) | [ScissorHands](scissorhands.md) (Liu et al., NeurIPS 2023) | >95% persistence ratio of pivotal tokens across steps |
| Observation window for scoring | [SnapKV](snapkv.md) (Li et al., 2024) | Last L_obs tokens predict generation attention >70% |
| Per-layer budget awareness | [PyramidKV](pyramidkv.md) (Cai et al., 2024) | Upper-layer entropy concentrates on few tokens |
| Primacy + recency layout | [Lost in the Middle](lost-in-middle.md) (Liu et al., TACL 2023) | U-shaped: 75.8% → 53.8% → 63.2% |
| Cumulative attention scoring | [H2O](h2o.md) (Zhang et al., NeurIPS 2023) | Power-law heavy-hitter distribution |
| Hole-leaving eviction | [KV Cache Architecture](kv-cache-architecture.md) | Post-rotated RoPE; PagedEviction; `cache_edits` |
| Contrastive deduplication | [LongLLMLingua](llmlingua.md) (Jiang et al., 2024) | Contrastive perplexity + reordering validates layout |
| Cheapest-first tier hierarchy | [Claude Code](claude-code-compaction.md) (Anthropic, 2025) | Snip → Microcompact → Collapse → Autocompact |
| Training-free performance bar | [ReSuM](resum.md) (Wu et al., 2025) | +4.5% training-free; must match or beat |
| Coarse-grained complement | [Context Folding](context-folding.md) (Sun et al., 2025) | RL-trained branch/return; combinable with AdaptiveCache |

---

## Related Pages

- [overview.md](overview.md) — project overview, milestones, positioning
- [research-plan.md](research-plan.md) — experimental design, baseline table
- [detailed-research-plan.md](detailed-research-plan.md) — per-experiment compute budget, 13-week timeline
- [importance-scoring.md](importance-scoring.md) — full signal descriptions, 2D framework
- [kv-cache-architecture.md](kv-cache-architecture.md) — hole-leaving mechanics, RoPE, production evidence
- [prefix-caching.md](prefix-caching.md) — cost model, layout gap analysis
- [context-management.md](context-management.md) — taxonomy of all approaches
