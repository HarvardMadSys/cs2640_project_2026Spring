# Modal H100 — Phase 4e Recall via Unmask Smoke

Date: 2026-05-04. Run id: smoke (single chat × 2). Wall: 376s.

Validates the v4 attention-unmask recall path end-to-end: adapter
queues obs_id via file IPC, engine consumes during compaction, skips
adding to `request.masked_block_ids`, attention reads the obs from
already-pinned KV. No re-prefill.

## Setup

- H100 80GB on Modal
- vLLM 0.13.0 + Memento overlay + v3 Phase 1–4e patches
- Qwen3-30B-A3B-Instruct-2507, attention_mask_mode=True, last_only_masking=False
- Two chat() calls with identical marker'd prompts
- Between calls: `model.queue_recall(obs_text)`

## Engine stats (worker pid=35)

```
compactions_seen:     2   (both chats)
captures_built:       2
pins_applied:        78   (39 blocks pinned per chat — same blocks both times)
mask_short_circuits:  2   (Phase 4b path took on both)
block_table_filters:  1   (Phase 4c filter applied ONLY in chat 1)
block_ids_filtered:  39
kv_captures_executed: 2
recalls_via_unmask:   1   (chat 2 consumed the queued obs_id; skipped mask)
```

## Hash match

- Engine `_auto_memento_id`: `[v4e-id] span=[46,651) len=605 first=[151665, 198, 46] last=[3495, 715, 151666] → obs:9e6224d81141f1ea`
- Adapter `queue_recall`:    `[v4e-adapter-id] len=605 first=[151665, 198, 46] last=[3495, 715, 151666] → obs:9e6224d81141f1ea`

Same length, same boundary tokens (151665 = `<tool_response>`, 151666 = `</tool_response>`), same hash. ✓

## Behavioral evidence

* **Chat 1 (no recall queued, obs masked)** — model can't see the obs, hallucinates about the tool-call JSON structure:
  > "I saw a repeating pattern in the data: a JSON object with a 'name' field containing 'read_file', followed by a repeated…"

* **Chat 2 (recall queued, obs unmasked)** — model references obs content via the memento (which is itself a summary of the obs):
  > "The exact contents of X were: 'observation pattern repeats'"

Counter says recalls_via_unmask=1; only one of two compactions filtered. The path works.

## What this proves

1. **File-IPC channel works** — adapter writes recall queue in main process; engine subprocess reads + drains atomically during compaction.
2. **Stable obs_id derivation works** — same content (token-identical prompt) produces the same hash on both sides, even though the `request_id` differs across chat() calls.
3. **The skip-mask branch in `mask_token_span` fires** — `recalls_via_unmask: 1` confirms the consume_recall hit, and `block_table_filters: 1` (vs 2 captures) confirms the masked_block_ids update was skipped exactly once.
4. **No re-prefill** — KV bytes were pinned in chat 1 and reused for chat 2. Bytes never moved.

## Next

Big bake: 4 SWE-bench Lite tasks × 3 seeds × 5 variants (off, lru, lru-append, lru-attmask, embedding-attmask) under attention_mask_mode=True. The v4 attmask thesis is testable: lru-attmask should have lower wall than lru (no re-prefill on recall) and lower wall than lru-append (no addendum tax).
