# Phase 4e — Recall via Attention Unmask (Design)

The headline experiment for Paper 2's v4 KV-mask thesis: recall an
evicted obs by toggling attention back on, **without re-prefilling its KV**.
This is what makes v4 strictly better than v3 physical compaction and v1
inplace recall — recall becomes free (mask flip) instead of expensive
(re-prefill).

## What we have (after Phase 4a–4d)

* **Scheduler.unmask_blocks_for_recall(request_id, memento_id)** — already
  exists at `external/memento/vllm/vllm/v1/core/sched/scheduler.py:1827`.
  Removes the memento's pinned block IDs from `request.masked_block_ids`.
  Next attention forward for that request will INCLUDE those blocks.
* **GPU bytes are pinned.** Phase 4a refcount-pins obs blocks at
  capture time so they survive `free_blocks()` and are reusable.
* **Memento metadata in scheduler-side store.** `gpu_pinned_block_ids`
  tracks which physical blocks belong to which memento_id.

## What we need

A way for the **policy** (`studies/`) to ask the **scheduler** (engine
overlay) to unmask blocks for a specific obs at a specific turn.

Two challenges:

### 1. memento_id is per-request, but requests are per-chat()

The auto-capture path uses `_auto_memento_id(request_id, start, end)` →
`f"auto:{request_id}:{start}:{end}"`. Each `LLM.generate()` call creates a
fresh `request_id`, so the same logical obs gets a NEW memento_id every
turn.

**Implication:** the policy can't remember "memento m_xyz lives at
message index 5" — that ID dies with the request.

### 2. The scheduler computes masked_block_ids per-request, fresh each call

In `mask_token_span`, when `attention_mask_mode=True` and capture_ops is
non-empty, the scheduler does:

```python
for op in capture_ops:
    request.masked_block_ids.update(op.src_block_ids)
```

This happens DURING the chat()'s prefill, not before. So `unmask_…` would
have to fire AFTER the request is created but BEFORE attention runs —
awkward async timing.

## Proposal

### Stable obs IDs (policy-assigned)

The policy assigns each obs a stable `obs_id` (e.g., a hash of obs
content + originating tool call). Threads it through:
1. As a field on the message dict (e.g., `_obs_id`)
2. Into the rendered prompt as a comment? No — invisible to model.
3. Or via the BlockMaskingConfig's existing memento_id channel.

Actually the simplest route: when the policy attaches a memento, it
also includes a stable `_obs_id`. The MementoVLLMModel adapter passes
this hint into the engine via a new BlockMaskingConfig channel that
maps `(request_id, obs_logical_range) → obs_id`. The scheduler's
auto_capture path uses `obs_id` as memento_id instead of generating a
new one each call.

### Recall-on-this-turn flag

The policy expresses "recall obs X this turn" by a new field on the
message dict, e.g., `_recall_attmask=True`. The renderer leaves the
markers intact (so engine compacts as usual, KV pinned). The adapter
collects all `_recall_attmask=True` obs IDs and:

1. Calls `LLM.generate()` (engine prefills, fires compaction, pins,
   adds to masked_block_ids)
2. RIGHT AFTER prefill but BEFORE decode, calls
   `scheduler.unmask_blocks_for_recall(request_id, obs_id)` for each
   recalled obs

Engine API needs: a hook to fire mid-step. vLLM has step-level
callbacks. Or we can intercept at the `generate()` boundary and do it
after prefill completes (during decode of subsequent tokens).

**Simpler alternative:** make the unmask happen in `mask_token_span`
itself. If `obs_id` is flagged for recall (by a new
`Scheduler.queue_recall(obs_id)` API), `mask_token_span` simply doesn't
add to masked_block_ids for that op. The block stays in the
block_table normally, attention sees it.

### Stable prompt across recall turns (the prefix-cache requirement)

For the v4 win to be real, the prompt must be **byte-identical** to a
previous turn so prefix cache reuses the same physical blocks. That
means:
* Same markers
* Same obs text
* Same memento text

So the renderer should NEVER strip markers on recall (unlike v1).
Recall = "engine, on this turn, attention should attend to the
already-pinned blocks for this obs_id, instead of masking them".

## Implementation order

1. Add `Scheduler.queue_recall(obs_id)` — sets a flag on the StoredMemento
2. Modify `mask_token_span` Phase 4b path: if the auto-generated
   memento_id matches a queued recall, skip `request.masked_block_ids.update(...)`
3. Add `MementoPolicy.recall_mode="attmask"` — leaves markers intact,
   queues recall via the adapter
4. Bridge: `MementoVLLMModel.queue_recall(obs_id)` calls into engine
5. Stable obs_id derivation: hash(obs_content + tool_call_id)
6. Bake on a multi-recall task to amortize the savings

## Risks and unknowns

* **Re-pin every turn.** Each chat() with markers re-fires capture_ops →
  refcount++. After N turns of the same obs, ref_cnt = N. We need to
  decrement at request-finish or limit re-firing.
* **Prefix cache hit assumption.** If the prompt drifts (e.g.,
  assistant text changes between turns), prefix cache misses, KV is
  re-prefilled, v4 advantage evaporates. This is a real risk in
  multi-turn agents and may dominate any gains.
* **Numerical drift.** Pinned KV is "frozen" at capture time. If the
  same logical position gets re-rotated under a new RoPE base or
  similar config, the cached values become stale. Qwen3 RoPE is
  fixed, so this should be OK.

## Where this lives in the v4 stack

Phase 4a–4c built the masking infrastructure. Phase 4d proves it's
wired correctly. Phase 4e is where v4 demonstrates a result that v1/v3
can't match: recall without re-prefill. If 4e doesn't show a wall_s
win on a multi-recall task, the v4 thesis is just engineering — not a
research contribution.
