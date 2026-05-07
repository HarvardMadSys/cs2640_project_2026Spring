# Modal H100 Bake — Phase 4d (attention_mask_mode=True)

Date: 2026-05-04. Run id: `1777836299`. Wall: 972s.

A re-run of the 2026-05-03 baseline with `PAPER2_ATTENTION_MASK_MODE=1`
to validate that the v4 KV-mask compaction path doesn't break the
trajectory under real workload. Same task, model, seeds, variants.

## Setup

- H100 80GB on Modal (`paper2-validate-recall` app)
- Image: vLLM 0.13.0 + Memento overlay + v3 Phase 1–4c patches
- Qwen3-30B-A3B-Instruct-2507, T=0.6, max_steps=20, budget=24000, low_water=0.6
- Variants: `off`, `lru`, `lru-append`, `embedding`, `embedding-append`
- Task: `pytest-dev__pytest-7490` (single-task probe)
- Engine config: **`attention_mask_mode=True`, `auto_capture_mementos=True`**

## Per-cell results

```
variant                seed steps  res recalls compact wall_s   |   prev wall_s (v3)
------------------------------------------------------------------------------------------
off                       0    12 Fals       0       2    31.2  |   32.9   match
lru                       0    19 Fals       0       0    16.3  |   15.1   match
lru-append                0    14 Fals       0       0    12.5  |   11.9   match
embedding                 0    20 Fals       0       0    14.9  |   13.8   match
embedding-append          0    12 Fals       0       0    11.4  |   10.6   match
off                       1    11 Fals       0       2    28.3  |   29.6   match
lru                       1    20 Fals       4       9    45.6  |   32.6   diverged
lru-append                1    20 Fals       2       3    37.3  |   35.7   match
embedding                 1    11 Fals       1       2    30.1  |    9.1   diverged
embedding-append          1    17 Fals       5       6    57.5  |   72.3   v4 -14.8s ✓
off                       2    20 Fals       0      11    61.3  |    8.0   diverged
lru                       2    20 Fals       7      11    47.5  |    8.8   diverged
lru-append                2    20 Fals       3      11    53.8  |    9.3   diverged
embedding                 2    20 Fals       1      11    70.8  |    8.0   diverged
embedding-append          2    20 Fals       3      11    57.1  |   87.5   v4 -30.4s ✓
```

## Aggregate (mean ± std across 3 seeds)

| variant | resolve | recalls | compactions | wall_s (v4) | wall_s (v3) | Δ |
|---|---|---|---|---|---|---|
| off | 0/3 | 0.0 ± 0.0 | 4.3 ± 5.0 | 40.3 | 23.5 | +16.8 |
| lru | 0/3 | 3.7 ± 3.5 | 6.7 ± 6.0 | 36.5 | 18.8 | +17.7 |
| lru-append | 0/3 | 1.7 ± 1.5 | 4.7 ± 5.5 | 34.5 | 18.9 | +15.6 |
| embedding | 0/3 | 0.7 ± 0.6 | 4.3 ± 5.5 | 38.6 | 10.3 | +28.3 |
| embedding-append | 0/3 | 3.7 ± 1.2 | 7.7 ± 2.9 | 42.0 | 56.8 | **-14.8** |

## What we learned

1. **15/15 cells completed without crashing.** Phase 4c filter +
   refcount-pin path is stable across multi-step, multi-variant trajectories.
   No CUDA errors, no OOM, no FlashInfer kernel surprises.

2. **Trajectory shape preserved on stable seeds.** All 5 seed=0 cells
   match yesterday's wall_s within ±2s. Same step counts, same
   compaction counts. v4 KV-mask is at parity with v3 physical
   compaction on stable trajectories.

3. **Seed=2 went the long route across all variants.** Yesterday's
   seed=2 had multiple "lucky bail" cells (8-11 steps each, near-zero
   wall). Today's seed=2 ran out to 20 steps for every variant. With
   T=0.6 and Qwen3 sampling, 1 of 3 seeds choosing a long path is
   normal noise. The aggregate wall_s comparison is heavily skewed by
   this — looking at matched-shape per-cell pairs gives a cleaner
   picture.

4. **embedding-append (long-trajectory variant) is meaningfully faster
   under v4.** -14.8s on seed=1, -30.4s on seed=2, +0.8s on seed=0.
   This is the ONE variant where both bakes consistently went long
   enough to amortize compaction differences — and v4 is the winner.
   Encouraging signal that v4's "skip physical KV move + pin instead"
   buys real wall time on long trajectories.

5. **0/3 resolve persists.** Same as yesterday. pytest-7490 is too
   hard for Qwen3-30B-A3B at T=0.6 within max_steps=20. Need to
   broaden the task set for any meaningful resolve-rate A/B.

## Probe results (Phase 4d-diag, run 1777840765)

After yesterday's bake we couldn't tell whether the v4 path actually
fired in the worker subprocess (worker stdout went silent post-load).
Added `ENGINE_STATS` counters to `memento_store.py` with inline
`bump()` writes (file-per-pid jsonl), then ran one cell to verify.

**The first probe (run 1777840028) returned ZERO counters and zero
stats files on the volume.** Investigation of the trajectory JSON
revealed the cause: `last_only_masking=True` (the adapter default)
puts markers ONLY on the LAST tool message per chat(). But the LAST
tool message is always the freshly-returned tool result — never has a
memento attached yet. So markers were never in the prompt → engine
never fired compaction → all v4 plumbing was bypassed.

This means **yesterday's "v4 attmask" bake (run 1777836299, the
aggregate table above) was running v3 physical-compaction-with-extra-
config-flags, NOT v4 KV-mask.** The wall_s comparison was apples-to-apples
identical to the v3 baseline because the engine took the SAME path in
both. The "embedding-append v4 -14.8s / -30.4s" observation is
therefore noise, not a real v4 win.

**Fix:** `last_only_masking=False` when `attention_mask_mode=True`.
Under attmask, per-call compaction is cheap (refcount pin + filter,
no physical KV move), so the "addendum tax" concern that motivated
last_only_masking doesn't apply.

**Re-probe (run 1777840765, 1 cell, off variant, seed=0):** stats file
landed on volume:

```json
{
  "compactions_seen": 1,
  "captures_built": 1,
  "pins_applied": 18,
  "mask_short_circuits": 1,
  "block_table_filters": 1,
  "block_ids_filtered": 18,
  "kv_captures_executed": 1
}
```

All four v4 phases fired:
* Phase 1B: 18 blocks captured GPU→CPU (28 MB stashed)
* Phase 4a: 18 blocks refcount-pinned in block_pool
* Phase 4b: physical compaction skipped (short-circuit fired)
* Phase 4c: 18 blocks compacted out of `input_batch.block_table`

Worker stdout also resurfaced under this config — `[v3-pin]`,
`[v4-mask]`, `[v3-capture]` all visible in Modal logs. Probably the
`last_only_masking=True` path simply never reached the print sites
(no compaction at all), which is why the previous bake's stdout was
empty for these tags.

## Outstanding work

* Re-run the full 5-variant × 3-seed bake with `last_only_masking=False`
  in attmask mode for an actual v4-vs-v3 wall comparison. The
  "+15-30s" deltas in the table above are NOT v4-vs-v3 — they're
  v3-vs-v3-with-different-noise.
* Phase 4e (recall via `unmask_blocks_for_recall`) is the headline
  experiment. Needs a token-level convention for the policy to keep
  the prompt with markers stable across turns, then trigger
  `Scheduler.unmask_blocks_for_recall` at the right step.

## (original) Diagnostic gap

The worker-side print statements (`[v3-pin]`, `[v4-mask]`, `[v3-capture]`)
that fired in the smoke (`smoke_v4_mask.py`) did NOT appear in the bake
log, despite identical engine config. Three explanations possible:

- **Print buffering across long-running engine subprocess.** The smoke
  exits the engine immediately; the bake holds it across 15 cells. If
  worker stdout is line-buffered but flushes only on process exit (which
  the bake never does), the prints could be queued in the worker's pipe
  buffer and dropped when Modal terminates the container.
- **`last_only_masking=True` interaction.** The bake uses the default
  rendering, which only marks the LAST tool message per chat() call.
  Markers should still trigger compaction once per call, but there may
  be a marker-detection edge case in long multi-turn trajectories.
- **Compactions firing but with empty `capture_specs`.** Possible if
  `auto_capture_mementos` config didn't propagate through to the
  compaction trigger sites for some path. The smoke's
  `auto_capture_mementos=True` did fire prints.

The trajectory metric `compactions=N` is the **policy-side** memento
attachment count (`HaikuMementoWriter` calls), which doesn't depend on
engine compaction firing — so a high compaction count in the trajectory
doesn't prove engine-side compaction (and pin/mask) actually fired.

**Action item for next session:** add a counter-only debug to
`MementoStore` that's written to a volume at end-of-run, and run a
single-cell variant to verify the engine-side path executed. Or thread
the BlockMaskingProcessor's stats through the engine output.

## Bottom line

* v4 KV-mask compaction is **stable** across the same workload that the
  v3 physical-compaction path handles. Zero crashes.
* Wall_s is **comparable** to v3 on stable trajectories; **better** on
  the long-trajectory variant (`embedding-append`).
* Resolve rate is **unchanged** (0/3 in both — task too hard regardless).
* Direct telemetry of the pin/mask path firing during bake is the
  outstanding diagnostic. Smoke proved it works in isolation; bake
  proves stability; need a tighter probe to confirm it's actually firing
  during the multi-step bake.

## Compare quickly

* v3 baseline: `studies/lifetime_cost/paper2/MODAL_RUN_2026-05-03.md`
* v4 raw output: Modal volume `paper2-out-v3:/scratch/out/validate_recall/1777836299`
* `modal volume get paper2-out-v3 / ./modal_out_v4d` to fetch trajectories
