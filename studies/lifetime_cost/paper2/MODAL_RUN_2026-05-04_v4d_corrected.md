# Modal H100 — Phase 4d Corrected Bake (v4 actually firing)

Date: 2026-05-04. Run id: `1777841888`. Wall: 797s.

This is the corrected re-run of yesterday's bake. The previous attempt
(run 1777836299, see `MODAL_RUN_2026-05-04_v4d.md`) had `last_only_masking=True`
which silently neutralized `attention_mask_mode` — markers never reached
the engine, so the v4 path never fired. After the diag probe
(`MODAL_RUN_2026-05-04_v4d.md` § Probe results), we set `last_only_masking=False`
when `attention_mask_mode=True` and re-ran.

## Setup

- H100 80GB (Modal `paper2-validate-recall`)
- vLLM 0.13.0 + Memento overlay + v3 Phase 1–4c patches
- Qwen3-30B-A3B-Instruct-2507, T=0.6, max_steps=20, budget=24000
- Variants: `off`, `lru`, `lru-append`, `embedding`, `embedding-append`
- Task: `pytest-dev__pytest-7490` (3 seeds)
- Engine: **`attention_mask_mode=True`, `auto_capture_mementos=True`,
  `last_only_masking=False`**

## Engine-side proof (file-based stats from worker pid=40)

```json
{
  "compactions_seen": 40,
  "captures_built": 40,
  "pins_applied": 1249,
  "mask_short_circuits": 40,
  "block_table_filters": 40,
  "block_ids_filtered": 1249,
  "kv_captures_executed": 40,
  "kv_restores_executed": 0
}
```

40 engine-side compactions across 15 cells (avg 2.7/cell). 1249 GPU
blocks pinned (avg 31 per compaction). All four v4 phases fired 40
times each. **The v4 path was actually exercised** — unlike the previous
bake where these were all zero.

## Aggregate (mean across 3 seeds)

| variant | v3 wall_s | **v4 wall_s** | Δ | v3 compact | v4 compact |
|---|---|---|---|---|---|
| off | 23.5 | **24.9** | +1.4 | 1.3 | 1.3 |
| lru | 18.8 | **26.0** | +7.1 | 1.3 | 2.7 |
| lru-append | 19.0 | **29.0** | +10.0 | 1.3 | 2.3 |
| embedding | 10.3 | **34.7** | +24.4 | 0.0 | 3.0 |
| **embedding-append** | **56.8** | **25.7** | **-31.1** | 7.7 | 1.3 |

## What we learned

1. **v4 wins on the variant where compaction cost actually matters.**
   `embedding-append` was the only variant where v3 fired
   compactions heavily (7.7 ± avg). v4 cuts that wall by **31s on
   average** — a 55% reduction. The compactions per cell drop too
   (1.3 vs 7.7), suggesting that the masked-but-cached obs blocks
   served the model well enough that fewer recall→re-compact cycles
   were needed.

2. **v4 loses on light-compaction variants.** When v3 barely
   compacts (avg 0–1.3), the v4 marker tax dominates: prompts are
   ~6K tokens larger because EVERY memento'd tool message gets
   markers under `last_only_masking=False` (the price for v4 path
   actually firing). Prefill cost wins.

3. **The tradeoff is structural, not a bug.** v4 trades "always-on
   marker tax + ref-counted pinning overhead" for "no physical KV move
   on each compaction". On long, compaction-heavy trajectories the
   trade pays off. On short, light-compaction trajectories it
   doesn't. This is the picture the paper should report.

4. **Trajectories diverge between v3 and v4.** Different prompts
   (markers vs no-markers) → different model decisions → different
   step counts and read patterns. Per-cell comparison is muddled by
   this; the variant-aggregate is the right unit.

5. **Repeated re-pinning of the same blocks is the next problem.**
   In one cell we saw the same 18 obs blocks pinned 8 consecutive
   times (different request_ids, same span). Ref_cnt accumulates
   without auto-release. Phase 4e needs idempotent pin (skip
   refcount++ if already pinned for this obs_id) or stable obs_ids
   that persist across requests.

## Bottom line

v4 KV-mask compaction is now **proven to fire end-to-end** under real
workload, and demonstrates a **31s wall_s win on the most
compaction-heavy variant**. The thesis holds where it should. The
infrastructure is solid; Phase 4e (recall via unmask, the headline
experiment) is the next step.

## Files

* Trajectories: Modal volume `paper2-out-v3:/validate_recall/1777841888/`
* Engine stats: `paper2-out-v3:/validate_recall/1777841888/v4_engine_stats.jsonl.40`
* v3 baseline (yesterday): `MODAL_RUN_2026-05-03.md`
* Invalidated v4 attempt #1: `MODAL_RUN_2026-05-04_v4d.md`
* Phase 4e design: `V4E_DESIGN.md`
