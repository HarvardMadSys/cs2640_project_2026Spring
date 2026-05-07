# Phase 9 — release + restore + re-rotate, no re-prefill

## Spec (from Vlad)

* Release obs's GPU KV during compacted period.
* On recall: bring obs KV back at original logical positions.
* **No re-prefill of anything** — not obs (already covered by Phase 3c
  CPU→GPU restore), not suffix.
* No padding-to-obs-length hacks.
* Accept the residual approximation that suffix's K/V was computed
  attending to the placeholder context, not the obs context.

## Why simple Phase 3c kvrestore alone isn't enough

Phase 3c restores obs at original positions but leaves the **suffix's K
vectors RoPE-baked at compacted positions**. After splice, those K
vectors are at logical positions shifted by `Δ = m_obs − p_placeholder`,
but RoPE was applied at `p_placeholder + offset`. Q at recall position
rotates differently from K at compacted phase → attention is rotated
incorrectly. For Δ in the thousands of tokens (typical when obs is 5K
tokens compacted to a 50-token summary), high-frequency RoPE channels
wrap around the unit circle many times — effectively random attention.
This is NOT "slight" RoPE drift. The current Phase 3c comment claiming
"slight correctness loss" understates the damage.

The fix is to re-rotate the cached K vectors by Δ after splice. RoPE
rotations compose: `R(p_new) = R(Δ) @ R(p_old)`. So multiplying the
cached K by `R(Δ)` corrects the phase. No model forward pass needed.
Cost is O(n_suffix_tokens × head_dim × num_layers × num_kv_heads) — a
fixed-shape tensor op, ~milliseconds for typical suffix sizes.

## The fix: KV rotation kernel

Re-use vLLM's existing rotary kernel via composition:

```python
from vllm.model_executor.layers.rotary_embedding.base import RotaryEmbedding
# K_old at phase p_old.  Apply R(Δ) → K at phase p_old + Δ.
positions = torch.full((n_suffix_tokens,), delta, device='cuda', dtype=torch.long)
RotaryEmbedding.forward_static(
    positions=positions,
    query=dummy_query,        # we don't care about query rotation here
    key=suffix_K_view,        # in-place rotation
    head_size=head_size,
    rotary_dim=rotary_dim,
    cos_sin_cache=rotary_emb.cos_sin_cache,
    is_neox_style=rotary_emb.is_neox_style,
)
```

The kernel applies `R(positions[i])` to `K[i]` in place. With positions
all = Δ, every suffix K rotates by exactly Δ.

## Plumbing — what needs building

### 1. Block-aligned placeholder pad

* Where: wherever the renderer emits placeholder text. Likely in the
  message rendering path (`memento_policy.py` or a renderer helper).
* What: pad the placeholder's *token count* up to
  `(p_placeholder + k * block_size) ≡ m_obs (mod block_size)` so that
  Δ = m_obs − p_padded is a multiple of `block_size`. Bounded overhead:
  at most `block_size − 1` filler tokens per compaction.
* Filler tokens: harmless, model-skippable. Newline characters or the
  tokenizer's pad token.

### 2. Worker-side KV rotation op

* Where: `gpu_model_runner.py` in the Memento overlay. Add
  `rotate_suffix_kv(block_ids, delta_tokens)`.
* Inputs: list of physical block IDs containing suffix K, the rotation
  delta in tokens.
* Per layer: gather K vectors from those blocks, apply
  `RotaryEmbedding.forward_static(positions=[delta]*n, key=K)`, scatter
  back.
* Layout-aware: handle FlashInfer paged layout
  `(num_blocks, block_size, num_kv_heads, head_size)`. (FlashAttention
  layout differs slightly; we ignore it since we're locked to FlashInfer
  on Blackwell.)

### 3. Scheduler-side rotation request

* When: in `Scheduler.queue_kv_restore`, after computing
  `insert_block_idx`, also collect the suffix's block IDs (everything
  AFTER `insert_block_idx`) and the rotation delta `Δ = m_obs −
  p_placeholder` (delta is positive when obs > placeholder).
* Append a `KVRotateOp(suffix_block_ids, delta)` to a new
  `pending_kv_rotate_operations[request_id]` queue.
* Plumbed through `SchedulerOutput` to the worker the same way
  `kv_restore_operations` already is.

### 4. Dual-key chain hashes

* After splice + rotate, vLLM's `allocate_slots` will look up suffix
  blocks under the recall-prompt's chain hashes. The cached blocks live
  under compacted-period chain hashes. Insert under both.
* The Phase 8 dual-key engine path is already in `_dual_key_blocks_for_recall`
  — needs the trigger moved to the recall event (not per-chat) and the
  prompts passed correctly:
  * `compacted_tokens` = the token sequence as it was at the END of the
    compacted period (just before recall).
  * `recall_tokens` = the token sequence after splice (obs back, suffix
    shifted).
  * With block-aligned padding, the chain-hash lookup walks both
    sequences block-by-block; suffix blocks at shifted indices correspond
    to the same content with R(Δ)-rotated K.

### 5. Adapter integration

* On recall:
  * Snapshot the compacted-period prompt token IDs at compaction (per
    memento_id). Stash on the policy.
  * After `policy.maybe_recall` flips the message + `pending_kvrestore_recalls`
    is populated, the runner calls `adapter.queue_kv_restore(obs_text)`
    which already exists. Extend it to also queue the rotation.
  * Add `adapter.queue_dual_key_for_recall(compacted_snapshot, recall_prompt)`
    — fires at recall, NOT per chat.

## Validation milestones

1. **Unit test**: rotate a known K tensor, verify
   `R(Δ) @ R(p) = R(p+Δ)` numerically.
2. **GPU smoke**: lru-kvrestore variant with rotation on a 30-step
   trajectory. Verify:
   * `kv_restores_executed > 0` (capture-restore works)
   * `kv_rotations_executed > 0` (rotation fires)
   * `dual_key_inserts > 0` (cache lookup hits suffix on recall)
   * Resolve rate doesn't crater vs lru-attmask.
3. **Yellow Pages bake**: long compacted periods amortize the rotation
   cost. Compare `none` (overflows) vs `lru-kvrestore+rotate` vs
   `lru-attmask`. Hypothesis:
   * `none`: crashes on overflow at turn ~K.
   * `lru-attmask`: works, costs GPU memory.
   * `lru-kvrestore+rotate`: works, releases GPU memory, no re-prefill,
     comparable wall.

## Why this is a real Paper 2 contribution

In-place re-positioning of cached KV via RoPE rotation is, to our
knowledge, novel for prefix-cache-aware inference engines. vLLM's prefix
cache is content-addressable but position-immutable; we make it
position-mobile via the rotation trick. This unlocks compaction policies
that release GPU memory aggressively without paying re-prefill on
recall.

## Estimated effort

* (1) Block-aligned pad: 1 hour
* (2) Worker rotation op + kernel call: 4-6 hours (includes layout
  handling per backend)
* (3) Scheduler plumbing: 2-3 hours
* (4) Dual-key trigger refactor: 2 hours (fix the broken adapter path,
  move trigger to recall event)
* (5) Adapter snapshot + integration: 2 hours
* Unit + GPU smoke: 4 hours
* **Total: ~3 working days**

## Open questions

* RoPE backends: do all RoPE variants (NTK, YaRN, Llama3, Qwen3) compose
  cleanly? Most do because they're variants of position scaling that
  preserve `R(a) @ R(b) = R(a+b)` for the SCALED positions. Need to
  verify for Qwen3-30B-A3B specifically.
* CUDA graph compatibility: the rotation op runs OUTSIDE the captured
  forward graph, between recall and the next forward. Should be fine
  since CUDA graphs only capture forwards.
* Multiple rotations: if a memento is recalled, then re-compacted, then
  recalled again, the K accumulates rotation phase. We need to track
  the current phase per memento and apply the corrective Δ each time.
