# Phase 8 plan — manual cache re-keying for "drop GPU + restore in place"

## What we want

Vlad's stated requirement, paraphrased: the agent's working set should **release
GPU memory** when an observation goes stale, and **restore in place at the
original logical position** when needed — **without re-prefilling** anything,
accepting slight RoPE-phase mismatch on the suffix's K vectors.

## Why the current variants don't deliver this

We have two working configurations, each missing one property:

* **Phase 4e (`lru-attmask`, attmask=True + Phase 4a pin)**: in-place restore
  with no re-prefill (recall = mask toggle), but **does NOT release GPU**
  — Phase 4a refcount-pins the obs blocks indefinitely.
* **Non-attmask + `PAPER2_NO_PIN=1` (`lru-inplace`)**: GPU memory IS
  released (via vLLM's natural LRU eviction once the obs's blocks fall
  out of any active request's block_table), but **DOES re-prefill on
  recall** because vLLM's chain-hash prefix cache misses on the suffix.
  Empirically: 40% cache hit rate on lru-inplace recall steps in the
  no-pin bake (run `1778008115`).

## Why vLLM's chain-hash misses

vLLM keys each block by a chained hash: `h(block_n) = f(h(block_{n-1}), tokens_n)`.

* During the **drop period**, the prompt is `[prefix, MEMENTO, suffix_drop]`.
  The suffix's blocks were prefilled at chain-hashes
  `h(prefix → MEMENTO → suffix_drop_chunk)` and live in
  `block_pool.cached_block_hash_to_block` under those keys.
* On **recall**, the prompt becomes `[prefix, OBS_BACK, suffix_drop]`
  (lru-inplace). The same suffix tokens but their parent chain runs
  through `OBS_BACK` instead of `MEMENTO`. **Different chain hash → cache
  miss → re-prefill.**

The K-vector content of those cached blocks is still valid for the suffix
tokens (they are the same tokens, prefilled once). The only thing wrong is
the lookup key. RoPE-baked positions are at compacted positions; on recall
they'd be at original-layout positions (off by `m` = obs's token count) —
that's the slight correctness loss we accept.

## The fix: dual-key the cached blocks

For each suffix block that exists in cache under the compacted-period chain
hash, **also insert it into the cache under the recall-prompt's chain hash**.
Then vLLM's allocate_slots normally finds it on the recall chat. Pure
metadata operation — no data movement, no kernel work, no re-prefill.

### Implementation pieces

1. **Helper: `compute_chain_hashes(token_ids, block_size, hash_fn)`** — walks
   token sequence in `block_size` chunks, threading `parent_hash → child_hash`
   via `vllm.v1.core.kv_cache_utils.hash_block_tokens`. Lives in
   `external/memento/vllm/vllm/v1/core/block_masking/memento_store.py`.
2. **Scheduler API: `scheduler.dual_key_cached_blocks(compacted_tokens, recall_tokens, splice_block_idx)`**:
   - For each block index `i ∈ [splice_block_idx, last_full_block]`:
     - Compute `compacted_hash[i]` from `compacted_tokens[0:(i+1)*block_size]`
     - Compute `recall_hash[i]` from `recall_tokens[0:(i+1)*block_size]`
     - Look up block under `compacted_hash[i]` in `block_pool.cached_block_hash_to_block`
     - If found and not already present under `recall_hash[i]`:
       `cached_block_hash_to_block.insert(recall_hash[i], block)`
3. **Cross-process IPC**: adapter (main proc) computes hashes locally,
   writes a `dual_key_request.jsonl` file with `(compacted_hashes, recall_hashes)`
   tuples per request. Engine subprocess drains on next schedule cycle and
   does the inserts. Same pattern as `queue_recall`.
4. **Adapter method `queue_dual_key_for_recall(compacted_token_ids, recall_token_ids)`**:
   called by the runner when `policy.maybe_recall` fires for `lru-inplace`
   under `PAPER2_NO_PIN=1`.
5. **Memento_store**: store the obs's compacted-period token sequence per
   `memento_id` so the adapter can reconstruct the compacted prompt for
   hash computation.

### Edge cases

- **Initial NONE_HASH**: vLLM initializes `NONE_HASH` at engine startup via
  `init_none_hash()`. Need access to it from the dual-keying helper. Either
  import lazily after engine is up, or stash it in `memento_store.py` at
  capture time (the engine subprocess can read it then).
- **`extra_keys`**: `hash_block_tokens` takes optional `extra_keys` for
  multimodal/LoRA. Our SWE-bench config has neither; pass `None`.
- **Token-id boundaries**: vLLM hashes only **full** blocks (`block_size`
  tokens). The last partial block of suffix never hashes anyway; we don't
  need to dual-key it.
- **vLLM v1 multi-proc**: `block_pool` lives in the engine subprocess.
  Dual-keying must run there; adapter (main proc) only writes the IPC file.

### Expected outcome

After dual-keying, vLLM's `allocate_slots` on the recall chat should hit
cache for **every block before the suffix's last partial block**, in both
the prefix region (already worked) AND the suffix region (newly working).
The only re-prefill is for the obs itself (we fix that via
`queue_kv_restore` + the existing capture path), and decode of newly
generated tokens.

## Estimated effort

- Helper + hash function plumbing: 2-3 hours
- Scheduler API + IPC: 3-4 hours
- Adapter method + runner integration: 2 hours
- Tests (unit + GPU smoke): 3-4 hours
- **Total: 1.5–2 working days**

## Validation

1. **Unit test** `compute_chain_hashes` matches vLLM's own chain hashes
   on a known token sequence (compute with both methods, compare).
2. **GPU smoke** with `PAPER2_NO_PIN=1` + `lru-inplace`: verify the recall
   step's `cached_tokens` jumps from ~40% to >90% on suffix tokens.
3. **Modal bake** matching the no-pin baseline: verify wall-time reduction
   on cells that exercise recall heavily.

## Why this is worth it

Phase 4e's win (-11% to -29%) costs ~32GB GPU memory in the bake (51K
pin events × 96KB/block ≈ 5GB cumulative, peaking around 30GB at long
trajectories). For a deployment where conversation length isn't bounded,
that's unsustainable. Phase 8 gives the same recall savings WITHOUT the
GPU memory growth — the obs's KV lives in CPU pinned memory ($/GB/hour
is ~10× cheaper than GPU), and the suffix re-uses cached blocks via
dual-keying.

The accuracy cost (suffix RoPE wrong by `m` positions) is empirically
measurable via resolve rate; we'd compare against Phase 4e's resolve
rate on identical workloads.
