# Headline findings — recorded Anthropic Haiku trajectories

**Source:** 20 trajectories under `results/exp_haiku_5/` (5 SWE-bench astropy
instances × 4 policies: `none`, `fifo`, `adaptive`, `summarize`). Run on
2026-04-07 against `claude-haiku-4-5` with `cache_budget = 8000` for
non-`none` policies and `999999` for `none`. The Anthropic API reports
`cache_read_input_tokens` per request, which is recorded in
`cache_trace[*].cache_read_tokens` — i.e., **these numbers are
ground-truth, not estimates.**

## Lifetime cost per task (Haiku 4.5 prices)

| Policy | Mean $/task | vs cheapest | Mean steps | Mean uncached tokens |
|---|---|---|---|---|
| `summarize` | **$0.2100** | 1.00× | 30.0 | 95K |
| `adaptive` | $0.2488 | 1.18× | 30.0 | 102K |
| `none` | $0.2811 | 1.34× | 49.2 | **55K** |
| `fifo` | **$0.6372** | **3.03×** | 50.8 | **253K** |

FIFO is **3× more expensive** than the cheapest alternative on Haiku.
On every single one of the 5 instances, FIFO is the most expensive policy.

## Cliff measurement

Cliff = `uncached_tokens(step k) / uncached_tokens(last warm step before k)`,
where "warm" = `hit_rate > 0.5`. One cliff point per *compaction episode*
(contiguous run of evictions), not per evicted message.

| Policy | n events | Median cliff | p90 cliff |
|---|---|---|---|
| `adaptive` | 52 | 4.01× | 7.84× |
| `summarize` | 39 | 8.39× | 32.70× |
| `fifo` | 54 | **31.72×** | **46.27×** |

**Decision Gate A: PASS** — overall median cliff = 10.11×, well above the 3×
threshold the study committed to.

## Cold-step fraction

Fraction of LLM calls where `cache_read_tokens = 0` (cold cache):

| Policy | Cold-step fraction |
|---|---|
| `none` | 12% |
| `fifo` | 46% |
| `adaptive` | 55% |
| `summarize` | 68% |

Note `summarize` has the *highest* cold-step fraction yet the *lowest*
mean cost. That's because it shrinks the post-compaction prompt
aggressively (~9 messages from 30+), so each cold step is cheap. The
absolute uncached tokens are still smaller despite never hitting cache.

## What this means for the proposed `prefix_preserving` policy

Existing data already shows three failure modes that `prefix_preserving`
should avoid:

1. **FIFO's 31× cliff.** Each eviction event invalidates ~all of the
   cached prefix. The frozen-prefix invariant in `prefix_preserving`
   prevents this by never moving the head bytes.
2. **`summarize`'s 68% cold-step rate.** Even though summary keeps the
   prompt small, the new prompt structure changes the byte prefix every
   time, so the cache is permanently cold. `prefix_preserving` keeps
   `[system + first K turns]` byte-identical, so the cache stays warm.
3. **`adaptive`'s lingering 4× cliff.** Even smarter scoring still moves
   bytes around. The mechanism, not the smarts, is what creates cliffs.

Target: lifetime cost ≤ summarize ($0.21) AND cold-step fraction ≤ none
(12%). If we hit both, the policy strictly dominates the existing four.

## Reproduce

```bash
.venv-lifetime/bin/python -m studies.lifetime_cost.scripts.analyze_recorded_cache \
    --root results/exp_haiku_5 --out studies/lifetime_cost/out/recorded_cache
```

## Files generated

```
studies/lifetime_cost/out/recorded_cache/
  trajectories.csv               — per-trajectory row: instance, policy, n_steps, cost, cliff stats
  summary.json                   — aggregate cliff stats by policy
  cliff_distribution.png         — boxplot, log y, with median annotations
  lifetime_cost.png              — bars + stacked decomposition
  lifetime_cost_per_instance.png — instance × policy bars (the headline figure)
  cold_step_fraction.png         — cold-step % by policy
  per_trajectory/                — 5 per-instance two-panel cliff plots
```

## Caveats

- Only 5 instances (one project, astropy). Headline holds qualitatively;
  quantitative magnitude (e.g., FIFO = 3.03×) is instance-dependent.
- The `adaptive` policy in this dataset is the *old* AdaptiveCache message-
  level reordering, not the proposed `prefix_preserving`. Real test of the
  new policy requires running the new policy through the runner — not yet
  done.
- Cliff metric uses "warm step before" as the baseline, which is the
  honest comparison. The earlier per-step ratio gave 1.0 for
  `summarize` because consecutive cold steps both have ~0 cache read.
- We do not have resolve-rate-vs-cost Pareto here because `resolved`
  isn't in the recorded trajectories. Need to run via `sb-cli` for that.
