# Pareto smoke: `none` vs `lru-kvrestore` on pytest-7490

**Modal run id:** `1778125050`
**App id:** `ap-h57MSRmbNgV4S0uXxNEQ9b`
**Date:** 2026-05-06
**Wall:** 560.6 s end-to-end
**Driver code:** `studies/lifetime_cost/paper2/modal_app/run_validate_recall.py`

## Config

```bash
modal run -d -m studies.lifetime_cost.paper2.modal_app.run_validate_recall \
  --instances "pytest-dev__pytest-7490" \
  --n-seeds 2 \
  --temperature 0.6 \
  --variants "none,lru-kvrestore" \
  --max-steps 20 \
  --recall-low-water 2.0 \
  --budget-tokens 12000 \
  --hard-budget-tokens 16000 \
  --no-pin \
  --profile-mem
```

- Hardware: Modal H100 80GB
- Model: Qwen3-30B-A3B-Instruct-2507, BF16, FlashInfer
- vLLM 0.13.0 + Memento overlay (commit `d8c10e6` + v3/v4/v8/v9 patches)
- `recall_low_water=2.0` is artificially high to force recall to fire whenever a memento exists; not a realistic operating config but the right setting to **stress-test the recall path**
- `--no-pin`: captured obs blocks fall to LRU naturally rather than refcount-pinning

## Per-cell rows

| Cell | Variant | Seed | Steps | Resolved | Compactions | **Recalls** | chat_wall (s) | haiku_wall (s) | total_wall (s) | final_prompt_tok |
|------|---------|------|-------|----------|-------------|----|---------------|----------------|----------------|------------------|
| 1 | `none` | 0 | 12 | F | 0 | 0 | 18.4 | 0.0 | 19.5 | 0 |
| 2 | `lru-kvrestore` | 0 | 16 | F | 5 | **4** | 32.5 | 7.4 | 40.7 | 0 |
| 3 | `none` | 1 | 20 | F | 0 | 0 | 34.3 | 0.0 | 34.6 | 0 |
| 4 | `lru-kvrestore` | 1 | 14 | F | 10 | **8** | 54.8 | 14.2 | 69.7 | 0 |

(`final_prompt_tok=0` on these cells is from a `_run` summary path that doesn't always populate it; the trajectory JSONs on the volume have the per-step prompt sizes.)

## Aggregate

- **`none`:** 0/2 resolved, 32 steps total, 52.7 s vLLM-only wall, 0 compactions, 0 recalls
- **`lru-kvrestore`:** 0/2 resolved, 30 steps total, 87.3 s vLLM-only wall, 15 compactions, **12 recalls — all firing successfully, 0 crashes**

Wall overhead per step (vLLM-only, excluding Anthropic Haiku for memento generation):

| Variant | s/step (vLLM) |
|---|---|
| `none` | 1.65 |
| `lru-kvrestore` | 2.91 |
| **Overhead** | **+77% per step** |

Resolution parity: this task is hard for vanilla too at temp=0.6 (0/2 in this draw). The headline "kvrestore solves what none solves" claim is consistent with this run (parity at 0/2) but not strongly evidenced — would need a task slate where `none` resolves reliably.

## Per-event Phase 9 timing

From `gpu_mem_trace.jsonl` (pre/post snapshots around each capture/restore/rotate event):

| Event | Count | Mean | Max | Total time |
|-------|-------|------|-----|-----------|
| capture (GPU→CPU memcpy) | 12 | 116 ms | 423 ms | 1.39 s |
| restore (CPU→GPU memcpy) | 12 | 15.5 ms | 22.6 ms | 0.19 s |
| rotate (in-place RoPE on K, FlashInfer kernel) | 12 | 17.0 ms | 18.4 ms | 0.20 s |
| **Mechanism total** | 36 | — | — | **1.78 s** |

→ **Phase 9 mechanism is ~2% of the 87.3 s vLLM time.** The +77% overhead vs `none` comes overwhelmingly from cliff-tax forward-pass cost (re-prefilling the suffix's K/V at new logical positions after each mid-prompt memento insertion), NOT from the splice/rotate/capture work itself.

## Engine-side capacity returned (the "memory back to vLLM" number)

Per-block KV size on Qwen3-30B-A3B:
- 48 layers × 2 (K+V) × 4 KV heads × 128 head_dim × 2 bytes (BF16) × 16 tokens/block = **1,572,864 bytes = 1.50 MB / block**

| Measure | Blocks | GB |
|---------|--------|----|
| Peak instantaneous free-block delta (max−min over the trace) | 988 | **1.55 GB** |
| Cumulative blocks released across all events | 1973 | **3.10 GB** |
| K vectors rotated in-place across all rotate events | 6939 | 5.46 GB of K mutated, no model forward |

Allocator state across the entire run:
- `alloc_bytes`: 73,173,470,208 (constant — 73.2 GB)
- `reserved_bytes`: 79,465,283,584 → 79,469,477,888 (≈constant — 79.5 GB)

→ **The mechanism returns capacity to vLLM's internal block pool, not to the OS.** PyTorch's caching allocator never shrinks. In a multi-agent serving topology (one vLLM serving N concurrent agents), every ≈1.5 GB returned per agent-trajectory is one extra concurrent agent's working set fitting in the same hardware budget. Scales linearly with compaction events per trajectory.

## Engine stats final state

```json
{
  "compactions_seen": 12,
  "captures_built": 12,
  "kv_captures_executed": 12,
  "kv_restores_executed": 24,    // counted at scheduler-queue + worker-execute (12 unique recalls)
  "kv_rotations_executed": 12,
  "kv_rotations_blocks": 6939,
  "pins_applied": 0,             // --no-pin honored
  "mask_short_circuits": 0,      // attmask not used in kvrestore
  "block_table_filters": 0,
  "block_ids_filtered": 0,
  "recalls_via_unmask": 0,       // attmask path not exercised
  "dual_key_inserts": 0          // dual-key insertion path didn't fire — known gap
}
```

## What this run validates (vs Phase-9 plan)

- ✓ Capture (GPU→CPU): 12 events, 116 ms mean
- ✓ Release path: --no-pin honored, blocks fell to LRU
- ✓ Splice REPLACE (not insert) into block_table: no `total_num_scheduled_tokens > 0` assert
- ✓ num_scheduled_tokens shrink after num_computed bump: no `IndexError` in copy_kv_slots
- ✓ In-place RoPE-composed K rotation across 6939 blocks
- ✓ 12 successive recalls × 2 seeds, no crashes
- ✗ dual_key_inserts = 0 — the auxiliary chain-hash registration path didn't fire in production. Code is wired and unit-tested; the in-production trigger condition (insertion under recall-prompt's chain hash) needs investigation
- ✗ The cliff is still present at compaction events because the renderer inserts the memento appendix mid-prompt (between obs and suffix). Architecturally fixable by appending the memento as a trailing user message instead — out of scope for this session

## Files

- `validate_recall_summary.json` — per-cell rows (canonical source for the table above)
- `gpu_mem_trace.jsonl` — 72 events: pre/post snapshot at every capture/restore/rotate, includes `num_free_blocks` and full `engine_stats` at each point
- `v4_engine_stats.jsonl.40` — engine-side counter snapshots (pid 40 was the EngineCore subprocess)

## Reproducibility

The exact command above will reproduce this run. Note that:
- `temp=0.6` introduces sampling variance — different seeds will give different trajectories
- `recall_low_water=2.0` is artificially high; for realistic comparison use 0.85
- `--no-pin` is the realistic eviction-pressure config
- The Modal image rebuilds when the v3 patch / `memento_store.py.new` change; first run after a regen takes ~5–10 min for the rebuild
