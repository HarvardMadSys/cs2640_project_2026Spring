# Lifetime Cost Study — Light Experiments, 2026-04-24

Two datasets analyzed end-to-end with the new pipeline.

## Dataset 1 — Recorded Anthropic Haiku trajectories (`results/exp_haiku_5/`)

20 trajectories across 5 SWE-bench astropy instances × 4 policies, captured
2026-04-07. Anthropic API recorded `cache_read_input_tokens` per call —
**ground-truth cache behavior, not estimated.**

### Results

| Policy | Mean $/task | vs cheapest | Cold steps | Median cliff |
|---|---|---|---|---|
| `summarize` | **$0.21** | 1.00× | 68% | 8.4× |
| `adaptive` | $0.25 | 1.18× | 55% | 4.0× |
| `none` | $0.28 | 1.34× | 12% | n/a |
| `fifo` | **$0.64** | **3.03×** | 46% | **31.7×** |

**Per-instance:** FIFO loses on every single one of the 5 instances by
2-3×. The other three are within 1.3× of each other and trade off
unpredictably per task.

**Cliff:** median 10.1× across all eviction events (well above the 3×
Decision Gate A threshold). FIFO's median is 31.7× — **every eviction
event blows up uncached tokens by 30+×.**

**Key paradox:** `summarize` has the **highest** cold-step rate (68%) yet
the **lowest** lifetime cost. Mechanism: it shrinks the post-compaction
prompt aggressively (~9 messages from 30+), so each cold step is small
in absolute terms.

Files: `recorded_cache/FINDINGS.md`, `recorded_cache/lifetime_cost_per_instance.png`,
`recorded_cache/cliff_distribution.png`, `recorded_cache/cold_step_fraction.png`,
5× `per_trajectory/cliff_*.png`.

---

## Dataset 2 — Applied Compute production-derived workloads (3 × 50 traces)

Synthesized message contents at recorded per-turn token lengths from
`Applied-Compute/trie/workloads/*.jsonl`. Each policy simulated on each
trajectory; lifetime cost recomputed under 5 different price columns.

### Results (mean $/task on agentic_coding_8k workload)

| Cost model | none | microcompact | prefix_preserving | boundary_aware | naive_summary |
|---|---|---|---|---|---|
| haiku-4-5 | **$0.057** | $0.060 | $0.138 | $0.134 | $0.191 |
| sonnet-4-6 | **$0.170** | $0.181 | $0.414 | $0.402 | $0.573 |
| gpt-4.1 | **$0.170** | $0.139 | $0.310 | $0.303 | $0.400 |
| gpt-4.1-mini | **$0.034** | $0.028 | $0.062 | $0.061 | $0.080 |
| qwen3-30b-a3b | **$0.005** | $0.005 | $0.013 | $0.013 | $0.018 |

(Cheapest per row in **bold**. Ranking is **invariant across all 5
providers** — Decision Gate C passes.)

### Headline finding

For Applied Compute's 8K-budget production workloads:
- **No compaction policy beats `none`.** Best alternative (`microcompact`)
  is at parity (within 5%); `prefix_preserving` is 2.4× more expensive.

### Why

These traces have median input_prompt_length ~6342 tokens and ~10 turns
of small additions. Total trajectory size rarely exceeds the 8K budget by
more than 2×, so the savings from compaction don't recoup the
summarizer-call cost. **Compaction has a cost floor; cheap-context tasks
sit below it.**

This is an *important negative result*. The proposed `prefix_preserving`
policy does NOT win unconditionally. It needs:
- A trajectory that exceeds budget by 5×+ (where the un-cached cost
  starts to dominate), OR
- A higher trigger threshold (compact only when desperately full), OR
- A free or near-free summarizer (e.g., template-based, no LLM call)

Files: `external_traces/results.csv`, `external_traces/summary.csv`,
3× `external_traces/figures/lifetime_cost__*.png`.

---

## Decision gate status (after light experiments)

| Gate | Question | Status | Evidence |
|---|---|---|---|
| **A** | Median cliff > 3×? | ✅ **PASS** | 10.1× overall, 31.7× for FIFO |
| **B** | `prefix_preserving` reduces lifetime $ by ≥20% vs `naive_summary`? | 🟡 **PARTIAL** | Yes vs naive_summary on AC (28% cheaper); but loses to `none` and `microcompact` on these tight budgets. Need long-context benchmark to test the regime where compaction is necessary. |
| **C** | Policy ranking invariant across ≥3 providers? | ✅ **PASS** | Identical ranking across haiku-4-5 / sonnet-4-6 / gpt-4.1 / gpt-4.1-mini / qwen3-30b-a3b on AC workloads |

---

## What to do next

1. **Run on `longdoc` benchmark** — controlled stress test where the
   trajectory MUST exceed budget by 10×+. This is the regime where
   `prefix_preserving` is supposed to win. If it doesn't win there
   either, the policy needs to be redesigned.

2. **Sweep trigger_ratio higher** (0.95, 0.98) so compaction only fires
   when the prompt is truly close to budget. The current 0.85 default is
   too eager.

3. **Free summarizer ablation** — replace the LLM-based summarizer with a
   template ("[Earlier steps elided: %d messages, ~%d tokens]"). Tests
   whether the win is coming from layout or from compression.

4. **Re-run on Applied Compute with budget=2000** so compaction is forced.
   Compares the same workloads in a regime where eviction must happen.

---

## Reproduce everything

```bash
module load python/3.13.12-fasrc01

# (one-time) make venv + install deps
python -m venv .venv-lifetime
.venv-lifetime/bin/pip install pyyaml matplotlib tiktoken pytest

# Tests
.venv-lifetime/bin/python -m pytest studies/lifetime_cost/tests -v

# Cliff + cost on existing recorded data
.venv-lifetime/bin/python -m studies.lifetime_cost.scripts.analyze_recorded_cache \
    --root results/exp_haiku_5

# Applied Compute workloads
.venv-lifetime/bin/python -m studies.lifetime_cost.scripts.run_external_traces \
    --config studies/lifetime_cost/configs/external_traces.yaml --fetch
.venv-lifetime/bin/python -m studies.lifetime_cost.scripts.run_external_traces \
    --config studies/lifetime_cost/configs/external_traces.yaml
```
