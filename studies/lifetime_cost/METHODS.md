# Methods — Lifetime Cost of Context-Managed Agents

This document is the reproducibility spec. It describes exactly what each
component does, how to invoke it, and how to interpret the outputs.
Companion to `README.md` (which states the research question and bets).

---

## 1. Pipeline anatomy

```
studies/lifetime_cost/
  pricing.yaml                  # per-model price sheet (USD per 1M tokens)
  README.md                     # what we're measuring and why
  METHODS.md                    # this file

  pipeline/
    types.py                    # Message, Usage, Step, Trajectory, CompactionEvent, LifetimeCost
    pricing.py                  # PriceSheet, cost_of, cost_per_resolved
    tokenization.py             # tiktoken / HF / Anthropic tokenizers, model-agnostic
    prefix_match.py             # canonical render, byte/block common-prefix
    replay.py                   # cliff detection from existing trajectory logs
    analysis.py                 # cliff plot, lifetime cost bars, Pareto, summary CSV
    ablations.py                # parameter sweeps

    runner.py                   # one-task agent loop with compaction hooks
    harness.py                  # model × policy × benchmark matrix runner

    policies/
      base.py                   # CompactionPolicy abstract; NoCompaction
      naive_summary.py          # full middle → one summary message
      microcompact.py           # per-msg in-place summarization for oversized obs
      prefix_preserving.py      # frozen [sys + first K turns] head, summary middle, recent M tail
      boundary_aware.py         # only compact at detected sub-task boundaries

    models/
      base.py                   # ChatModel abstract
      openai_compat.py          # OpenAI / vLLM / Together / OpenRouter / etc
      anthropic_native.py       # Anthropic with explicit cache_control breakpoints

    benchmarks/
      base.py                   # Task, Tool, ToolEnv, Benchmark abstract
      swebench_replay.py        # offline cliff analysis on results/*.json
      taubench.py               # τ-bench airline / retail (multi-turn dialogue)
      gaia.py                   # GAIA L1/2/3 (web research) — stub or real tools
      longdoc.py                # synthetic long-doc agent — fully controlled

  configs/
    cliff_replay.yaml           # the cheap experiment
    main.yaml                   # the main matrix
    ablations.yaml              # parameter sweeps

  scripts/
    run_replay.py               # python -m studies.lifetime_cost.scripts.run_replay --config ...
    run_main.py                 # python -m studies.lifetime_cost.scripts.run_main --config ...
    run_ablations.py            # python -m studies.lifetime_cost.scripts.run_ablations --config ...

  tests/
    test_prefix_match.py
    test_pricing.py
    test_policies.py
```

## 2. Lifetime cost — the metric

For a single trajectory of T LLM calls plus C compaction calls:

```
lifetime_cost(traj) =
    Σ_t  uncached_t · price_in_uncached
  + Σ_t  cached_t   · price_in_cached
  + Σ_t  output_t   · price_out
  + Σ_t  cache_write_t · price_cache_write   (Anthropic only)
  + Σ_c  compaction_call_tokens_c · price_in_uncached
```

`uncached_t = prompt_tokens_t − cached_tokens_t`, both pulled from the
provider's `usage` block. The cliff ratio at step k is

```
cliff_k = uncached_block_estimate(k) / max(uncached_block_estimate(k−1), 1)
```

where `uncached_block_estimate(t) = curr_blocks(t) − shared_blocks(t)` and
shared_blocks comes from `prefix_match.cache_hit_ceiling`.

## 3. Compaction policies

| Policy | Trigger | What it does | Predicted cliff impact |
|---|---|---|---|
| `none` | never | identity | n/a (no compaction) |
| `naive_summary` | tokens > trigger_ratio · budget | replace all non-system / non-first-user / non-recent messages with one summary message | very large — every byte after [sys+task] changes |
| `microcompact` | same | rewrite each oversized observation in place to a 1-2 line summary | medium — first rewritten observation invalidates everything after |
| `prefix_preserving` | same | freeze `[sys + first K turns]`, summarize the middle, keep last M turns intact | small after first compaction (frozen prefix = byte-identical for all subsequent steps) |
| `boundary_aware` | soft only at detected sub-task boundary; hard at hard budget | wraps prefix_preserving | small AND fewer compactions |

**Why the frozen prefix matters.** The first time `prefix_preserving`
fires, it commits to a head ending at `_frozen_idx`. On all subsequent
compactions (no matter how many), it re-uses the same head — never
expanding it, never shrinking it. This is the entire mechanism by which
the prefix cache stays warm across compaction events.

## 4. Model adapters

| Adapter | Used for | `cached_tokens` source | Cache breakpoint mechanism |
|---|---|---|---|
| `openai_compat` | OpenAI, vLLM, Together, OpenRouter, Anthropic via OAI shim | `usage.prompt_tokens_details.cached_tokens` | none (automatic prefix matching) |
| `anthropic_native` | Anthropic API | `usage.cache_read_input_tokens` | explicit `cache_control: ephemeral` markers; runner can place one |

For Anthropic specifically, `prefix_preserving` will tell the runner to
mark a `cache_control` breakpoint at the end of the frozen prefix —
without this, Anthropic won't cache anything beyond the system prompt.

## 5. Benchmarks — why these

Compaction only matters when context overflows. We need benchmarks where
that *actually happens*. SWE-bench is the wrong choice for the headline:
most instances finish under 32K. So:

- **`longdoc` (synthetic)** — guaranteed overflow. The document is sized
  so that even one full read busts the budget. Fully controlled, fully
  reproducible, no external deps. **This is the benchmark to use for
  ablations.** It also has a clean ground-truth resolve metric (sum of
  needle values).
- **`taubench`** (Sierra, 2024) — real long multi-turn dialogues. Two
  domains: airline (115 tasks) and retail (115 tasks). Average dialogue
  length 20-50 turns; airline domain routinely exceeds 32K. Has a real
  resolve metric (`env.calculate_reward()`).
- **`gaia`** (Mialon et al., 2023) — multi-step web research. Levels 2-3
  produce 50K+ contexts. Stub tool backend lets us measure cost shape
  with zero external API calls; real backend (DuckDuckGo + trafilatura
  + python sandbox) for actual eval.
- **`swebench_replay`** — offline cliff analysis on existing logs. No
  agent loop. Use for sanity check + the cheap-first cliff plot.

We deliberately do **not** include LongBench / NoCha / InfiniteBench:
those are single-shot QA, not agent loops, so compaction doesn't make
sense as an intervention.

## 6. Ablations

| Sweep | Values | Interpretation |
|---|---|---|
| `prefix_preserving.keep_first_turns` (K) | {2, 4, 6, 8, 12} | How big a head to freeze. Larger K = more cached, less compression headroom. |
| `prefix_preserving.keep_recent_turns` (M) | {2, 4, 6, 8} | How much of the tail to keep verbatim. Smaller M = more aggressive but risks losing recent state. |
| `prefix_preserving.trigger_ratio` | {0.50, 0.70, 0.85, 0.95} | When to start compacting. Eager (0.5) = many small compactions; lazy (0.95) = few large compactions. |
| `microcompact.per_msg_threshold_tokens` | {200, 500, 800, 2000} | What counts as "oversized" for in-place compaction. |
| `boundary_aware.boundary_grace_steps` | {0, 1, 2, 4} | Window around a detected boundary in which compaction is allowed. |
| `budget_tokens` | {8K, 16K, 32K, 64K} | The actual budget. Tests whether ranking is budget-invariant. |

## 7. Decision gates

Defined in `README.md`. Re-stated here so they're not forgotten:

- **Gate A (after replay):** `median cliff_k > 3×` across existing
  trajectories. Measured by `scripts/run_replay.py`. If fail → kill the
  study.
- **Gate B (after main run):** `prefix_preserving` reduces lifetime $ by
  ≥ 20% vs `naive_summary` at equal resolve rate, on at least one
  benchmark × model. If fail → downgrade to a measurement paper.
- **Gate C (after multi-model run):** policy ranking holds across ≥ 3
  providers. If fail → result is provider-specific.

## 8. How to actually run things

```bash
# 0. Install deps
pip install openai anthropic tiktoken transformers pyyaml matplotlib datasets

# 1. Cheap first experiment: cliff plot from existing logs (zero $)
python -m studies.lifetime_cost.scripts.run_replay \
    --config studies/lifetime_cost/configs/cliff_replay.yaml

# Reads results/*.json, writes:
#   studies/lifetime_cost/out/cliff/per_trajectory.json
#   studies/lifetime_cost/out/cliff/aggregate.json
#   studies/lifetime_cost/out/cliff/cliff_top.png

# 2. Sanity-check tests
pytest studies/lifetime_cost/tests -v

# 3. Main matrix (pick policies × models × benchmarks; budget your $$)
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
python -m studies.lifetime_cost.scripts.run_main \
    --config studies/lifetime_cost/configs/main.yaml

# Writes:
#   studies/lifetime_cost/out/main/trajectories/<benchmark>/<model>/<policy>.jsonl
#   studies/lifetime_cost/out/main/figures/lifetime_cost.png
#   studies/lifetime_cost/out/main/figures/pareto.png
#   studies/lifetime_cost/out/main/figures/summary.csv

# 4. Ablations
python -m studies.lifetime_cost.scripts.run_ablations \
    --config studies/lifetime_cost/configs/ablations.yaml

# To run only one sweep:
python -m studies.lifetime_cost.scripts.run_ablations \
    --config studies/lifetime_cost/configs/ablations.yaml \
    --only prefix_preserving:keep_first_turns
```

## 9. Integration with the wider repo

This study is **independent** of the rest of `adaptivecache/` — it does
not depend on `src/adaptive_cache/`, `modal_app/`, or `LMCache`. It can
be run from a fresh checkout with only the deps in §8.

It does optionally consume `results/*.json` for the cheap-first
cliff plot — that's the only coupling.

## 10. Open issues (tracked)

- The `taubench` adapter assumes `tau-bench` v1 API; verify after install.
- `gaia` requires HF auth for the gated dataset; document the click-through.
- Public agent-trace dataset adapters (Hermes, TRAIL, SWE-Gym, Applied
  Compute workload-stats) are stubbed as task #10 — not yet implemented.
- We don't yet model TTL-driven cache evictions on Anthropic (5m TTL
  default; 1h TTL premium). The cliff math currently assumes the cache
  is still warm at the next step, which is true within typical agent
  cadence but not always.
