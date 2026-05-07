---
title: AdaptiveCache — Importance and Stability Scoring
type: concept
tags: [adaptivecache, importance-scoring, kv-cache, attention, heuristics, layout]
sources: [h2o, scissorhands, snapkv, streamingllm, lost-in-middle, llmlingua, context-folding]
date_created: 2026-04-05
date_updated: 2026-04-05
---

# AdaptiveCache: Importance and Stability Scoring

How AdaptiveCache decides what goes early in the context (pinned prefix zone) vs what stays in the volatile suffix vs what gets evicted. The key insight: **importance ≠ stability**, and the layout decision requires both dimensions.

## The 2D Framework

A single importance score is insufficient. Two blocks can both be "important" but belong in completely different zones:

- An **error message** is critically important right now but belongs in the suffix — once resolved, it should be evicted
- A **function signature** is moderately important at any given step but belongs pinned early forever

The correct decision requires two independent axes:

```
                    HIGH STABILITY
                         │
    pin_zone             │   pin_zone
    (stable, modest)     │   (stable + critical)
                         │
LOW ─────────────────────┼───────────────────── HIGH
IMPORTANCE               │                IMPORTANCE
                         │
    evict                │   suffix_zone
    (low value)          │   (hot but volatile)
                         │
                    LOW STABILITY
```

**Pin score** = importance × stability → high → goes early in prefix  
**Suffix score** = importance × (1 − stability) → high → stays in suffix, evicted when pressure rises  
**Evict score** = (1 − importance) × (1 − stability) → high → evict now  

---

## Importance Signals

### Signal 1: Expected Attention (best training-free signal)

**Paper:** Expected Attention (arXiv:2510.00636)

Computes, in closed form, how much future queries will attend to each existing KV pair — without generating tokens. Exploits distributional properties of LLM activations. Directly answers: "will the model attend to this block in future steps?"

This is the theoretically cleanest signal. Implementation cost: moderate (requires access to activation statistics).

### Signal 2: Cumulative Past Attention (H2O-style)

Track a running mean of attention received by each block across the last K steps. H2O shows attention follows a power-law: a small "heavy hitter" set captures most attention mass and is stable (ScissorHands: >95% persistence ratio across steps).

Cheaper than Expected Attention. Works well for blocks that have already accumulated a history. Cold start problem: new blocks have no history.

### Signal 3: Reference Count (agentic-specific, novel)

Track how many subsequent tool calls, reasoning steps, or grep patterns *reference or cite* each block. Operationalizes: "is the agent actively building on this block?"

Examples:
- A file appears in 5 bash commands → high reference count → important
- A grep result never followed up → zero reference count → evict
- A function name appears repeatedly in reasoning → high importance

This signal is unique to agentic contexts. No paper uses it. Cheap to compute, interpretable, and directly measures "load-bearing" blocks.

### Signal 4: Low-Entropy Token Protection (ForesightKV)

**Paper:** ForesightKV (arXiv:2602.03203)

Critical finding: evicting KV pairs for low-entropy tokens (tokens the model is very confident about in generation) causes disproportionate factual errors downstream. These tokens are factual anchors — their KV states ground the model's reasoning.

For coding agents, low-entropy anchors include:
- The issue title / function name being fixed
- Test assertion values
- API function signatures already established in reasoning

These should receive an importance boost regardless of attention score. Identify via: tokens with perplexity < threshold in recent generation.

---

## Stability Signals

### Signal 5: Structural Type Prior (fast heuristic)

Assign stability priors based on block type before any scoring. Requires only a lightweight classifier or regex patterns:

| Block type | Stability prior | Reasoning |
|---|---|---|
| Task description / issue statement | 1.0 | Always relevant to every step |
| Function / class definitions | 0.9 | Architectural constants of the codebase |
| Import statements | 0.8 | Define the available API surface |
| File currently being edited | 0.7 | Relevant until edit is committed |
| Test file content | 0.7 | Verification anchor throughout |
| API documentation | 0.8 | Referenced repeatedly |
| Directory / file listing | 0.4 | High value during exploration, depreciates |
| Error messages | 0.1 | Volatile: resolved and obsolete quickly |
| Raw bash outputs (non-error) | 0.2 | Usually one-time lookups |
| Grep / search results | 0.3 | Mostly consumed once, occasional anchors |
| Previous reasoning/thoughts | 0.4 | Some decisions persist, most are transient |

Zero computation. Strong prior. Good enough for the 75% milestone.

### Signal 6: Importance Variance Tracking (novel)

Track the variance of a block's importance score across the last K steps. Low variance = the block is consistently important = stable = good pin candidate. High variance = importance fluctuates = volatile = keep in suffix.

Directly operationalizes stability using whatever importance signal is already being computed. Cheap: just maintain a running variance alongside the running mean.

### Signal 7: Tool-Call Dependency Graph (novel, agentic-specific)

In agentic coding tasks, tool calls form a DAG:
```
grep(pattern) → open(foo.py) → read(class Foo) → edit(foo.py, line 42) → run_tests()
```

Blocks that are *upstream* of many other tool calls are structurally load-bearing. A file that was opened to understand the bug, then edited, then tested against — it's a dependency anchor. Measure graph centrality (in-degree in the tool-call dependency graph) as a stability signal.

Novel: not in any paper. Particularly strong for multi-step coding tasks.

---

## Combined Scoring Function

```python
def importance(block, step_history):
    return (
        w1 * expected_attention(block)          # closed-form future attention
      + w2 * cumulative_attention(block)         # H2O-style running mean
      + w3 * reference_count(block)              # agentic: how often cited
      + w4 * low_entropy_boost(block)            # ForesightKV protection
    )

def stability(block, step_history):
    return (
        w5 * structural_prior(block.type)        # fast heuristic prior
      + w6 * (1 - importance_variance(block))    # consistency across steps
      + w7 * dependency_centrality(block)        # graph-theoretic load-bearing
    )

def zone(block):
    imp = importance(block)
    sta = stability(block)
    if imp * sta > PIN_THRESHOLD:      return "prefix_pin"
    if imp > IMPORTANCE_THRESHOLD:     return "suffix_keep"
    return "evict"
```

Weights are hyperparameters tuned on SWE-bench traces (125% milestone).

---

## Two Additional Ideas

### Contrastive Deduplication

Multiple tool results often contain redundant information — three grep outputs all showing the same function definition. Before scoring for importance, check semantic overlap between blocks. If block B is largely redundant with already-pinned block A, B is a strong eviction candidate regardless of absolute importance. Inspired by LongLLMLingua's contrastive perplexity: the *marginal* information value of a block, not its absolute value.

Implementation: embedding similarity between blocks; evict if cosine similarity > threshold with any pinned block.

### Sink-Aware Prefix Construction

StreamingLLM: first 4 tokens always attract disproportionate attention regardless of content (SoftMax "dump" behavior). Implication: positions 0–3 are always attended to. Place your highest-importance pinned content at positions 4–100 (immediately after the sinks) to exploit primacy bias. This is free — you get the attention benefit just from positioning.

Layout order for the prefix zone:
```
[pos 0–3: sink tokens / system preamble]
[pos 4–100: highest importance + highest stability items]
[pos 100–N: remaining pinned items, ordered by importance]
[suffix zone: volatile items, newest last]
```

---

## Milestone Mapping

| Milestone | Signals implemented |
|---|---|
| **75%** | Structural type prior (Signal 5) + reference counting (Signal 3) + importance variance (Signal 6) |
| **100%** | + Cumulative attention (Signal 2) + dependency graph (Signal 7) + sink-aware layout |
| **125%** | + Expected Attention (Signal 1) + low-entropy protection (Signal 4) + learned weights via ForesightKV-style distillation on SWE-bench traces |

## Related Pages

- [overview.md](overview.md) — system design using these signals
- [kv-cache-architecture.md](kv-cache-architecture.md) — how eviction works (hole-leaving)
- [h2o.md](h2o.md) — cumulative attention scoring
- [scissorhands.md](scissorhands.md) — persistence of importance hypothesis
- [snapkv.md](snapkv.md) — observation window as importance predictor
- [streamingllm.md](streamingllm.md) — sink tokens and primacy zone
- [lost-in-middle.md](lost-in-middle.md) — behavioral basis for layout importance
- [llmlingua.md](llmlingua.md) — contrastive perplexity / marginal information value
- [system-design.md](system-design.md) — how these signals are used in the full pipeline
- [research-plan.md](research-plan.md) — what to build and measure next
