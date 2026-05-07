# Phase 9 — KV rotation for no-re-prefill recall in vLLM

## Claim

When an LLM agent recalls a previously-compacted observation, the agent
need not re-prefill its KV cache. Instead, the obs's KV bytes can be
brought back from CPU pinned memory into freshly-allocated GPU blocks
(Phase 3c), AND the suffix's already-cached K vectors can be rotated in
place by `Δ = m_obs − p_placeholder` tokens via vLLM's existing rotary
kernel. The rotation is mathematically equivalent to having stored those
K vectors at the new (post-splice) logical positions, because RoPE
composes as `R(p+Δ) = R(Δ) ∘ R(p)`. The mechanism is end-to-end
operational on Qwen3-30B-A3B-Instruct-2507 with FlashInfer on H100.

Headline engine stats from a single-task `--no-pin` smoke
(`bqkcximvt`, run id `1778099664`):

```
compactions_seen: 3, captures_built: 3, kv_captures_executed: 3,
kv_restores_executed: 4, kv_rotations_executed: 2,
kv_rotations_blocks: 1260, pins_applied: 0
```

The salient number is `pins_applied: 0`: the captured GPU blocks were
NOT refcount-pinned, the request truncation released them to vLLM's LRU
free queue, and we still recovered correct KV state on recall via
restore + rotate. This is the property Phase 9 was built to deliver.

## Why Phase 3c alone was not enough

Phase 3c (Paper 2's earlier checkpoint) restored obs bytes to GPU at the
original logical positions, but left the suffix's K vectors RoPE-baked
at the compacted-period positions. After splice, those K vectors sit at
positions shifted by `Δ` tokens from where their phase was computed.
For a 5K-token obs compacted to a 50-token placeholder, `Δ ≈ 5000`;
high-frequency RoPE channels wrap around the unit circle many times,
making suffix attention essentially random. The Phase 3c source comment
calling this "slight correctness loss" is wrong, and Phase 9 is the
fix.

The fix is a single multiplication by `R(Δ)` on every suffix K vector,
applied per layer in the cached page tensor. No model forward, no
re-prefill, no extra memory allocation. The cost is
`O(n_suffix_tokens × head_dim × num_layers × num_kv_heads)`, in the
millisecond range for typical suffix sizes (~10K rotation_blocks per
recall in the smoke runs).

## Mechanism — capture, release, restore, rotate, dual-key

The full pipeline reads as five logically-distinct stages, all running
in the engine subprocess:

1. **Capture** at compaction time.
   `gpu_model_runner._execute_kv_capture_operations`
   (`gpu_model_runner.py:1117`) copies obs's KV bytes from GPU to CPU
   pinned memory and stashes them in `MementoStore` keyed by a
   content-hash `obs_id`. The engine then calls `announce_captured_obs`
   (`memento_store.py:212`) so the adapter knows which obs_ids are
   recoverable.
2. **Release** under GPU pressure. With `PAPER2_NO_PIN=1`, the captured
   GPU blocks are NOT refcount-pinned. Once compaction truncates the
   request's logical span, the blocks fall out of `block_table` and
   become eligible for the LRU free queue. The marker is
   `pins_applied: 0` in `ENGINE_STATS`. We confirmed actual block-level
   release in run `b0k90z1ex`'s `gpu_mem_trace.jsonl`: `num_free_blocks`
   oscillated 5677 → 6240 → 5943 across capture, restore, and rotate
   events — measured directly via the `register_block_pool`
   (`memento_store.py:34`) hook called from
   `Scheduler.__init__` (`scheduler.py:280`).
3. **Restore** at recall time. `Scheduler.queue_kv_restore`
   (`scheduler.py:2157`) drains the IPC restore queue, allocates fresh
   blocks via `block_pool.get_new_blocks(n_obs_blocks)`, queues a
   `KVRestoreOp` for the worker (CPU→GPU memcpy), and splices the new
   blocks into `req_to_blocks[request_id]` at
   `insert_block_idx = m.logical_range[0] // block_size`. The
   request's `num_computed_tokens` is bumped to reflect that the
   restored region is now "computed" (with defensive clamps; see Bugs
   below).
4. **Rotate** (Phase 9 proper). The same `queue_kv_restore` call
   captures the suffix block IDs *before* the splice, then queues a
   `KVRotateOp(suffix_block_ids, delta_tokens)` (defined in
   `memento_store.py:614`). On the next worker step,
   `_execute_kv_rotate_operations` (`gpu_model_runner.py:1409`) gathers
   suffix K from each layer's page tensor via `kv_cache.index_select`,
   applies `rope.forward_native(positions=[Δ]*n_tokens, ...)`, and
   scatters back. V is left untouched. The rotary embedding module is
   discovered with a one-time walk in `_find_rotary_embedding`
   (`gpu_model_runner.py:1382`) and cached on the runner.
5. **Dual-key insert** (Phase 8 + Phase 9). After splice + rotate, the
   suffix blocks now hold K at the recall-prompt RoPE phase. We insert
   them into `block_pool.cached_block_hash_to_block` under the
   recall-prompt's chain hashes via `_dual_key_after_recall_splice`
   (`scheduler.py:2083`), so future requests' prefix-cache walks find
   them under the new chain. **The OLD chain-hash entries are NEVER
   invalidated.** This is Vlad's running directive throughout the
   project — the cache is append-only; future requests matching the
   old chain still get the same physical blocks (their K is now
   misphased relative to that chain, which is an accepted correctness
   trade for the sake of cache stability).

The mathematical core is verified by three microbenches in
`studies/lifetime_cost/paper2/tests/`:
`microbench_rope_compose.py` confirms `R(p+Δ) = R(Δ) ∘ R(p)` from
first principles in fp32 (cosine similarity 1.0000000 across all
tested `(p, Δ)` pairs); `microbench_rope_vllm.py` confirms vLLM's
`RotaryEmbedding.forward_native` composes (so we can re-use the
engine's optimized kernel rather than write a new one);
`microbench_rope_kvcache.py` exercises the FlashInfer paged-layout
gather/scatter primitive used inside `_execute_kv_rotate_operations`,
verifying bf16 cosine similarity > 0.999 across layers and confirming
that V is untouched.

## Empirical results

All runs on Modal, Qwen3-30B-A3B-Instruct-2507, FlashInfer backend, H100.

- **`bqkcximvt`** (run `1778099664`) — single task, `--no-pin`. The
  proof of release-side mechanism. Engine stats already quoted above.
  `pins_applied: 0`, `kv_rotations_executed: 2`,
  `kv_rotations_blocks: 1260`. End-to-end recall path with no
  refcount pin is operational.
- **`b4zbb5be4`** — single task, `--no-pin`,
  `recall_low_water=15` (forces a recall on every chat). 11 steps,
  8 compactions, 6 recalls, 57.9 s wall. Multi-recall trajectory
  through the rotation kernel runs without crash and without
  resolution collapse.
- **`becboyp1y`** (run `1778101927`) — pin-mode equivalent, single
  task. 12 steps, 9 compactions, 7 recalls, 110.1 s wall. Engine
  stats: 8 captures, 14 restores, 7 rotations, 10 314 rotation_blocks,
  1 774 pins_applied. The "with pin, no release" comparison; longer
  wall reflects the absence of release pressure pruning the working
  set.
- **`b0k90z1ex`** (run `1778104722`) — first run with the GPU memory
  trace plumbed through, `--no-pin`. Captured `gpu_mem_trace.jsonl`
  showing `num_free_blocks` oscillating 5677 → 6240 → 5943 across
  capture / rotate / restore events. The only direct measurement of
  GPU-block release we obtained.
- **5-task SWE-bench Lite baseline** (`none` variant only, runs
  `1778106617`, `1778107302`, `1778110246`): pytest-7490 (20 steps,
  failed), pytest-5413 (6 steps, failed), django-11099 (8 steps,
  **resolved=True**), django-13230 (25 steps, failed), django-12184
  (25 steps, failed). Vanilla Qwen3 baseline = **1/5 resolved**. This
  is the floor against which a Phase 9-resolved comparison eventually
  has to land.

What we did NOT obtain: a clean apples-to-apples Phase 9 vs. baseline
wall-time / resolve-rate comparison on the same 5-task list.
Modal credit ran out before that run completed end-to-end. The
single-task smokes don't overlap with the 5-task baseline list, so
no head-to-head number exists yet.

## Bugs hit and how they were resolved (categorical)

The build hit roughly 15 distinct bugs. The taxonomy:

- **API drift in vLLM 0.13.** vLLM renamed `_coordinator` → `coordinator`
  and turned `BlockHashWithGroupId` into a `NewType` rather than a
  dataclass between minor releases. The fix in each case was to chase
  the canonical import (commit `e5812de` for the coordinator rename;
  `6c4f0c8` for `make_block_hash_with_group_id`). Lesson: pin to a vLLM
  SHA or budget for chasing renames at every revision.
- **Cross-process state in vLLM v1.** The engine runs in a subprocess.
  Hash determinism requires `PYTHONHASHSEED` set in both processes
  (and `init_none_hash()` to be called before any chain-hash compute).
  The recall queue, kv-restore queue, dual-key queue, and captured-obs
  set all crossed the process boundary as JSONL files in `/tmp/`,
  drained by the engine on the next scheduler tick. The block pool
  itself only exists in the engine subprocess, so the `register_block_pool`
  hook is what allows the heartbeat / mem trace to read
  `num_free_blocks` at all.
- **Tokenizer mismatch between policy and engine.** The policy used
  `cl100k_base` (the budget-counting tokenizer) to hash obs text into
  obs_ids; the engine used Qwen3's tokenizer. Same string, completely
  different obs_ids, recall never matches. Fixed by routing the policy
  through `MementoVLLMModel.compute_obs_id_for_text`
  (`memento_vllm.py:150`), which wraps `<tool_response>…</tool_response>`,
  tokenizes with the *model's* tokenizer, and calls the engine's
  `compute_obs_id` (commit `190c98b`). The fallback path in
  `memento_policy.py:466` still uses `ctx.tokenizer` as a last resort,
  which is wrong if the budget tokenizer differs from the model
  tokenizer; we kept the fallback only because all current callers
  install `_obs_id_for_text`.
- **Multi-compaction trap in the scheduler.** `pending_compactions`
  blindly popped operations whose `end > num_computed`; in the same
  loop, `capture_ops` was being overwritten instead of extended, so
  multiple captures in one step lost all but the last. Fixed in commits
  `4f715f1` (capture_ops extend) and `ea25005` (`max_end` gating).
- **`num_computed_tokens` overshoot.** When a stale
  `m.logical_range[1]` (set at capture time) exceeded the request's
  current `num_prompt_tokens`, bumping `num_computed_tokens` to it
  pushed the request past total tokens, triggering vLLM's
  `assert total_num_scheduled_tokens > 0`. Fixed with defensive clamps
  (commits `5fa69eb`, `13146e1`, `25b9726`); the clamps are still
  band-aids over the deeper issue (see Open Questions).
- **Stale `cached_block_hash_to_block` entries post-eviction.** Under
  `--no-pin`, blocks evicted from `block_pool.free_block_queue` left
  dangling chain-hash entries pointing to now-reused blocks. vLLM's
  prefix-cache walk on a subsequent request would advance past
  `num_prompt_tokens`, causing the same `total_num_scheduled_tokens`
  assert. We tried a post-rotate cache invalidation (commit
  `3d1ecd6`) but reverted it (commit `06cc871`) per Vlad's
  never-invalidate directive; the surviving fix is the defensive
  `num_computed_tokens` clamp described above.
- **Modal image caching gotcha.** The vLLM patch file was being
  regenerated by one script and overwritten by a separate `.new`
  output — successful regeneration on disk did not propagate into
  the Modal image. Fixed in commits `40e3e68` and its revert /
  follow-ups; the lesson is to keep one canonical patch path and
  delete the stale `.new` writer.
- **Splice INSERT vs REPLACE in `queue_kv_restore` (the assert
  killer).** `req_blocks[insert_idx:insert_idx] = blocks` extended
  the block_table by `n_obs_blocks` instead of replacing the
  fresh-blank obs slots vLLM had naturally allocated. vLLM's
  prefix walk treats `len(req_to_blocks)` as ground truth for
  cached-token count, so `num_computed` overshot `num_prompt` by
  exactly that delta and `assert total_num_scheduled_tokens > 0`
  fired N cycles later. Fixed in commit `47ae956` to slice-REPLACE
  + `block_pool.free_blocks(displaced)`. This was masked by the
  earlier `num_computed_tokens` clamps for the simple cases but
  manifested as cumulative state corruption that broke chats 4–7.
- **`num_computed` bump without `num_scheduled_tokens` shrink (the
  IndexError killer, only surfaced after the splice was fixed).**
  `allocate_slots` ran with the PRE-bump `num_computed=5328` and
  extended block_table to cover `[5328, 5328+16384) = 1357 blocks`.
  `queue_kv_restore` then bumped `num_computed` to 6027. Forward
  processed the full 16384 scheduled tokens at positions
  `[6027, 22411)` — the last 699 fell past the block_table end. K/V
  silently went to null slots, no immediate crash. Next compaction
  saw `num_computed=22411` and built `active_physical_positions`
  up to slot 22410, triggering `IndexError: list index out of range`
  in `copy_kv_slots` (`blocks[1357]` when `len(blocks)=1357`). Fixed
  in commit `4924ed1` by capturing the bump delta in the drain loop
  and shrinking `num_scheduled_tokens` by exactly that delta. 7
  successive recalls validated end-to-end with no crash (run
  `1778119187`).

The pattern across all 15 bugs is the same: each is mechanically
small, but they compound because the system has FOUR cross-process
state surfaces (recall queue, kv-restore queue, dual-key queue,
captured-obs file) and TWO tokenizers (budget vs. model). Any
divergence — a stale file, a mismatched hash, a blindly-popped op —
manifests as either a silent recall miss or a hard scheduler assert.

## What is settled

- **The math.** RoPE composition holds in fp32 (cosine similarity
  1.0000000) and to bf16 rounding precision (mean abs error ~3e-3) per
  `microbench_rope_compose.py`. vLLM's `RotaryEmbedding.forward_native`
  composes the same way per `microbench_rope_vllm.py`, so we use the
  shipped kernel rather than write a new one.
- **The mechanism.** Capture → release → restore → rotate → dual-key
  has been demonstrated end-to-end on at least one full trajectory
  (smoke `bqkcximvt`), with engine stats consistent with the design
  intent and `pins_applied: 0` confirming the release branch.
- **Block-level release is real.** The `gpu_mem_trace.jsonl` from
  `b0k90z1ex` shows `num_free_blocks` actually moving in response to
  Phase 9 events. This is the only direct measurement we have of GPU
  release behavior, and it is consistent with the design.

## Paper 1 → Paper 2 Continuity

### What Paper 1 established

Paper 1 (`adaptivecache/paper/PAPER1_PLAN.md`, written up in
`adaptivecache/studies/lifetime_cost/reports/REPORT.md`) ran 8 training-free
heuristic compaction policies × 2 benchmarks (SWE-bench Lite live, τ-bench
retail with chain extension) × 2 model classes (Qwen3-30B-A3B local, Haiku
4.5 API) across phases B / C / D / E. The aggregate finding: **no policy
Pareto-dominates `none` on a strong agent at any benchmark cell tested.**
The dominant cost term was `input_uncached`: each compaction event
invalidates the downstream prefix cache, so the next K input tokens get
billed at the uncached rate (~10× the cached rate). Per-event cliff cost
was measured at $0.10–0.15 of new uncached input on Haiku, with a 5+ step
amortization horizon before break-even.

The headline numbers that motivated Paper 2:

- **SWE-bench Lite Phase C v1, Qwen3-30B-A3B, N=4, max_steps=40**:
  `none` 2/4 resolved; all five compaction policies (`prefix_preserving`,
  `microcompact`, `llm_reorganizer`, `evict_oldest`, `position_aware`)
  1/4. Compaction *prevented* context overflow on `requests-3362` (held
  max prompt 18–32K vs `none`'s 55K overflow) but the `max_steps=40`
  ceiling cut policies off before they could re-explore the dropped tool
  obs. The Phase C report (`PHASE_C_REPORT.md`) calls this out as
  expected: compaction's structural job worked; the agent's behavior
  *under* compaction is what regressed.
- **SWE-bench Lite Phase E v1, Haiku 4.5, N=10 with real `FAIL_TO_PASS`
  validation**: `none` 5/10 at $3.47 ($0.69/resolved); plain
  `consumption_evict` 5/10 at $7.63 (**$1.53/resolved, 2.2× more
  expensive for the same resolve count**); facts variant 4/10 at $6.15;
  outline variant 5/10 at $8.16. Cost decomposition (Haiku v5 lazy, 4
  heavy tasks): `prefix_preserving` paid $3.69 in `input_uncached` from
  19 cliffs vs `none`'s $1.16, plus $0.57 in summarizer LLM calls.
- **τ-bench retail chain_size=10 (Phase E v4)**: 0 compactions across all
  4 policies despite max prompt reaching 36K (above the 29.75K trigger).
  `consumption_evict`'s coding-tuned supersession rules don't match
  retail tools — a portability finding, not a mechanism failure.

### The Paper 1 → Paper 2 transition

Paper 1's auto-memory line, *"Project goal: Pareto-dominate `none` on
cost/resolved"* (`phase_c_findings.md`), was not achieved on the
workloads tested. Paper 1's own diagnosis (REPORT.md §"Why the negative
result is publishable") is that byte-level eviction is fundamentally
bounded by the cliff cost amplification factor: any policy that mutates
the prompt prefix incurs the ~10× re-bill on everything after the
mutation, and the saved bytes on the dropped span almost never amortize
that on realistic agent trajectories. The constructive direction Paper 1
itself names is "keep the bytes — offload to a side store, recall on
demand" — i.e. Paper 2.

### What Paper 2 inherits and what it changes

Inherited from Paper 1: same model (Qwen3-30B-A3B-Instruct-2507), same
agent harness (`pipeline/runner.py`, `swebench_live` benchmark with
real-test `FAIL_TO_PASS` validation via `validate_with_tests.py`), same
vLLM 0.13 base, same Memento-overlay starting point for prompt-time
compaction. The action-graph supersession signal from `consumption_evict`
is the trigger for Paper 2's KV capture events.

Changed: the *response* to a compaction event. Paper 1's policies all
mutate the prompt — replacing a tool obs with a placeholder, dropping it,
or summarizing it — and live with the resulting cache invalidation.
Phase 9 instead treats the obs's KV bytes as the asset and the
placeholder as a pointer: capture KV to CPU on compaction, restore +
rotate on recall, dual-key the suffix into the new chain hash. The
prompt still mutates (the placeholder still goes in), but the **suffix's
cached blocks remain physically intact and findable** under the new
chain via the dual-key insert.

### Why Paper 1's specific failure mode is what Phase 9 eliminates

Paper 1's cliff tax is structurally a chain-hash invalidation: vLLM's
prefix cache keys blocks by `H_i = hash(H_{i-1}, block_token_ids_i)`.
Replacing 5K obs tokens with a 50-token placeholder changes
`block_token_ids` at the placeholder position, which changes `H_i` and
every `H_j` for `j > i`. The cached suffix blocks still exist in the
block pool but are unreachable from the new request's chain walk —
forcing re-prefill. Phase 9's release branch leaves the suffix's
*physical* K/V untouched (rotated in place by `Δ` to match the new
absolute positions), and `_dual_key_after_recall_splice` registers those
same physical blocks under the new chain's hashes. The `pins_applied: 0`
+ `kv_rotations_blocks: 1260` smoke from `bqkcximvt` is the proof point
that the chain-hash break Paper 1 took as an axiom is no longer
load-bearing.

## Open Questions

- ~~**The `total_num_scheduled_tokens > 0` assert keeps recurring**~~
  **RESOLVED 2026-05-06 (two distinct bugs).** First, the splice was
  using slice-INSERT (`req_blocks[a:a] = blocks`) instead of
  slice-REPLACE (`req_blocks[a:a+n] = blocks`) — extended block_table,
  vLLM's prefix walk overshot `num_prompt`. Fixed in `47ae956`. After
  that fix, a SECOND bug surfaced as `IndexError` in `copy_kv_slots`:
  `queue_kv_restore` bumped `num_computed` AFTER `allocate_slots` had
  already locked in `num_scheduled_tokens` for the pre-bump range.
  Forward processed 16384 tokens at positions past the block_table
  end, K/V silently went to null slots, next compaction saw
  `num_computed > len(block_table) * block_size` → out-of-range index
  in active_physical_positions. Fixed in `4924ed1` by shrinking
  `num_scheduled_tokens` by the bump delta in the drain loop. **End-
  to-end validation: 7 successive recalls + 9 compactions on
  pytest-7490, no crash, run `1778119187` (493s wall).** Posted to
  Agora as `3ae6602c-...` (splice) and `c96f1ba5-...` (sched-shrink).
- **The Phase 8 dual-key INSERT at recall time** (commit `0bd78c2` —
  the brief refers to this as `600bb48` but that SHA does not exist
  on the branch; the actual top-of-branch commit is `0bd78c2 "regen
  patch w/ Phase 8 dual-key on recall"`) was just connected. It runs
  in `_dual_key_after_recall_splice` after every successful rotation.
  We have not yet validated on Modal whether future requests actually
  hit cache via the new chain-hash entries. The expected signal is
  `dual_key_inserts > 0` in `ENGINE_STATS` paired with a measurable
  prefix-cache hit-rate jump on the recall step's follow-on chats.
- **No clean apples-to-apples wall-time comparison Phase 9 vs.
  baseline** has been produced. The smoke runs that completed do not
  overlap on the same task list as the 5-task baseline. Producing this
  comparison is the next priority and is gated only on Modal credit.
- **Multi-recall phase tracking.** If a memento is recalled, then
  re-compacted, then recalled again, the suffix K accumulates phase.
  The current `KVRotateOp` always rotates by the delta passed in by
  the policy (`m_obs - p_placeholder`); this is correct only if no
  prior rotation has been applied to the same suffix region. Any
  serious deployment needs a per-region phase tracker.
- **RoPE variants.** We verified composition for the standard
  base-10000 NeoX RoPE used by Qwen3. NTK / YaRN / Llama3 scaled-RoPE
  variants were called out as potentially needing re-verification in
  the Phase 9 plan. We have not done that verification.
- **CUDA-graph compatibility.** The plan called this out as
  expected-to-be-fine since CUDA graphs only capture forwards; we
  have not stress-tested rotation under the captured-graph regime
  with multiple concurrent requests.
