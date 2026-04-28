# Lifetime Cost of Context-Managed Agents

**Question.** What does context compaction actually cost an agent over its full lifetime, once you account for the prefix-cache invalidation it causes on the very next step?

**Bet.** Existing compaction work (Claude Code microcompact, OpenHands condensation, ReSuM, MEM1, MemAgent) measures cost at peak context or "tokens saved per compaction event." None measure $ per resolved task across the full trajectory including the cache cliff that fires the moment a compacted prompt is sent. We think that cliff is large enough that current compaction algorithms are optimizing the wrong objective.

This study is **deliberately model-agnostic**. Everything below is computed from the OpenAI-style `usage` block (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`) plus a per-model price sheet. No vLLM internals, no LMCache, no kernel hooks. Anthropic, OpenAI, vLLM, Together — all instrumentable identically.

---

## 1. Metric

For a single task trajectory of T LLM calls:

```
lifetime_cost(task)  =  Σ_t  [  uncached_prompt_t  · p_in
                              +  cached_prompt_t    · p_cached
                              +  completion_t       · p_out
                              +  compaction_call_t  · p_compaction ]

uncached_prompt_t  = prompt_tokens_t  −  cached_tokens_t
```

Reported normalized as `$ per resolved instance` (resolve rate measured separately via `sb-cli`). Numbers are reported per `(model, policy)` cell so the result generalizes across providers.

## 2. The Cliff

For each compaction event at step k, define:

```
cliff_k  =  uncached_prompt_{k+1}  /  uncached_prompt_{k−1}
```

A compactor with no cache awareness produces `cliff_k ≈ |new_prompt| / |delta_at_step_k−1|` — easily 10–50× on a long trajectory. A prefix-preserving compactor should keep `cliff_k ≈ 1`. The cliff plot is the headline figure.

## 3. Cheap First Experiment (no new runs needed)

Before committing to a full study we want to know if the cliff is even real. We can answer that **from existing trajectory logs**, no GPU time required.

Inputs: any saved trajectory containing per-step messages + `usage` block. We have these in `results/` from prior SWE-bench runs.

For each trajectory:
1. Replay each step's messages through a tokenizer (model-agnostic via `tiktoken` / `transformers.AutoTokenizer`).
2. Detect compaction events: a step where `len(messages_t) < len(messages_{t-1})` or message content at index i changed for some i < len.
3. For each step, compute the byte-level common prefix with step `t−1` → that's the upper bound on what a perfect prefix cache could have hit.
4. Plot `(step index, prefix-cache hit ceiling)` for every trajectory; mark compaction events with vertical lines.

If we see the cliff (and we will), the project is real. If the cliff is small (<2×), the framing is wrong and we pivot.

Output of this experiment: one `.png` per trajectory + an aggregate `cliff_distribution.png`. **Target: 1 day of work, zero $ in inference.**

## 4. Compaction Policies (later, once cliff confirmed)

All policies expressed as `compact(messages, budget) -> messages`. No model-specific code.

| Policy | Description |
|---|---|
| `none` | No compaction. OOM if context overflows. Upper bound on quality, no cliff. |
| `naive_summary` | When `tokens(messages) > budget`: replace turns 1..N−recent with one summary message. Standard baseline; produces large cliff. |
| `microcompact` | Per-tool-result summarization in place. Approximates Claude Code. |
| `prefix_preserving` (ours) | Keep `[system + first K turns]` byte-identical. Summarize only the middle. Recent M turns intact. Parameter K chosen to maximize `|preserved_prefix| · (steps_until_next_compaction)`. |
| `boundary_aware` (ours, stretch) | Defer compaction until detected sub-task boundary (heuristic: tool call returns to a previously-touched file, or agent emits a planning message). |

## 5. Model-Agnostic Pricing

`pricing.yaml` (to be written) — one entry per model:

```yaml
anthropic/claude-sonnet-4-6:
  input_uncached_per_mtok:  3.00
  input_cached_per_mtok:    0.30
  output_per_mtok:         15.00
openai/gpt-4.1:
  input_uncached_per_mtok:  2.00
  input_cached_per_mtok:    0.50
  output_per_mtok:          8.00
qwen/qwen3-30b-a3b:    # self-hosted on Modal; price = amortized GPU $
  input_uncached_per_mtok:  0.10
  input_cached_per_mtok:    0.01
  output_per_mtok:          0.30
```

Costs are reported in $ but the *ranking* of policies should be invariant across the price sheet — that's the falsifiable claim that makes the study model-agnostic.

## 6. Out of Scope (v0)

- Anything that touches KV blocks directly (no LMCache, no kernel work)
- Attention-based importance scoring (the wiki's 100% milestone)
- Layout reorganization (separate study; this one isolates compaction)
- Training anything

## 7. What Lives Where (planned)

```
studies/lifetime_cost/
  README.md              ← this file
  pricing.yaml           ← per-model price sheet
  replay/                ← cheap first experiment: cliff detection from existing logs
    detect_cliff.py
  policies/              ← compact(messages, budget) implementations
    naive_summary.py
    microcompact.py
    prefix_preserving.py
  harness/               ← runs policies × models × tasks, emits lifetime_cost.json
    run.py
  analysis/              ← plots and tables
```

Nothing in this list exists yet. Step 1 is `replay/detect_cliff.py` against `../../results/`.

---

## Decision Gates

- **Gate A (after replay):** is `median cliff_k > 3×` across existing trajectories? If no → kill the study, reframe.
- **Gate B (after first policy run):** does `prefix_preserving` reduce lifetime $ by ≥20% at equal resolve rate, on at least one model? If no → the cliff is real but not actionable; downgrade to a measurement paper without an algorithmic contribution.
- **Gate C (after multi-model run):** does the policy ranking hold across ≥3 providers? If no → the contribution is provider-specific, not a general result.
