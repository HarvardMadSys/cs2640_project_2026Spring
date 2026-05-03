# Paper 2 — v3 Design: KV-Level Recall (vLLM Fork)

Date: 2026-05-02. Status: design + Phase 1 starting.

## TL;DR

v3 makes recall cost ≈ 0 by **storing the obs KV at compaction time and
splicing it back into a future request's block table on recall, bypassing
the hash-based prefix-cache lookup**. Lives in a fork of the Memento
overlay (`external/memento/vllm/`).

This is the *real* "stale historical truth" mechanism: we accept that the
stored KV was computed against the prefix-as-it-was-then; the agent's
later turns were generated against the elided prefix; we mix them at
attention time and let the model live with the staleness. The premise is
that the staleness is bounded — earlier mementoes are summaries of what
the K vectors at position P "saw," so the divergence is small in
distribution.

v2 (append-only at the prompt layer) gets us cheap recall cleanly. v3
gets us cheap recall *at chronological position* — better coreference,
no addendum semantics for the model to learn — at the cost of a real
vLLM fork.

## The fundamental constraint, named

The K, V values at position P depend on every layer's residual stream up
to and including position P, which depends on attention to all positions
< P in earlier layers. So obs's K, V are a function of its own tokens
PLUS the prefix that came before it AT COMPUTE TIME.

If we store obs's K, V at compute time and reuse them later, the new
prompt's prefix may differ (earlier messages mementoed in between). The
stored K, V are then "stale" — they would have different values if
recomputed against the new prefix.

**v3's premise**: this staleness is acceptable. The user's argument is
that the agent's suffix was generated in the elided world and that
suffix is the historical truth. Mixing it with the obs's pre-elided-world
KV at attention time is a *consistent extension* of the agent's actual
trajectory, not a counterfactual. The empirical question is whether
quality holds.

## Why we can't reuse vLLM's prefix cache directly

You might hope: vLLM's prefix cache stores blocks by hash. If we render
the recall prompt with full obs in chronological position, the obs blocks
should hash to the same value as in the original computation, and we get
a free hit.

But the hash chain is `h(parent_block_hash + token_ids)`. The parent
block hash includes everything before the obs. If any earlier message
got mementoed between the original computation and the recall call, the
parent hash differs → obs blocks don't match → cache miss → re-prefill.

In our policy, compaction is oldest-first. So between obs's first
computation (at time T0) and our recall (at time T_now), several earlier
obses may have been mementoed. The byte prefix changed. Hash-based
lookup fails.

**v3 sidesteps the hash**: explicit block_id injection per request, by
position. The fork patches the scheduler so a request can declare
"position [P_start, P_end] uses these specific physical block_ids; do
not hash-lookup that range."

## Architecture

```
                  ┌─────────────────────────────┐
                  │   Compaction time (T0)      │
                  │   - obs at logical [P, P+L] │
                  │   - K/V computed in layers  │
                  └────────────┬────────────────┘
                               │
                       capture obs KV blocks
                       (GPU → CPU pinned memory)
                               │
                               ▼
                  ┌─────────────────────────────┐
                  │  MementoStore (side table)  │
                  │  memento_id ─→ {            │
                  │    cpu_kv_blocks,           │
                  │    logical_positions,       │
                  │    parent_token_hash,       │
                  │    layer_metadata           │
                  │  }                          │
                  └────────────┬────────────────┘
                               │
                  many chat() calls later...
                               │
                               ▼
                  ┌─────────────────────────────┐
                  │  Recall fires (T_now)       │
                  │  policy: "recall memento_id"│
                  └────────────┬────────────────┘
                               │
                       splice request:
                       - render prompt with full obs at [P, P+L]
                       - inject pinned KV blocks at those positions
                       - skip prefill of [P, P+L]
                               │
                               ▼
                  ┌─────────────────────────────┐
                  │  Engine forward             │
                  │  - prefill new tokens only  │
                  │  - obs KV: spliced (stale)  │
                  │  - generate suffix          │
                  └─────────────────────────────┘
```

## Phase plan

### Phase 1 (this session): KV capture + CPU offload

**Goal**: at compaction time, copy the obs's KV blocks from GPU to CPU
pinned memory and stash them in a `MementoStore`. Don't change anything
else. Verify via test: after compaction, the bytes are on CPU; the GPU
blocks are still freed normally.

**Files**:
- New: `external/memento/vllm/vllm/v1/core/block_masking/memento_store.py`
  — the side table + the GPU→CPU copy logic.
- Modified: `external/memento/vllm/vllm/v1/core/single_type_kv_cache_manager.py`
  — `compact_kv_cache` accepts an optional `capture_for_memento_id`
  parameter; when set, copies the obs blocks to CPU before freeing.
- New: `paper2/recall/memento_store_handle.py` — Python-side handle the
  policy uses to track its memento_ids.

**Scope**: capture only. No recall yet. Phase 1 lands a working
"obs KV is now on CPU" mechanism, with bytes verifiable.

### Phase 2: Block_pool pin + LRU exemption (optional)

If we keep the captured KV on CPU only, no pin needed. If we also want
to keep the GPU blocks alive (faster recall), patch block_pool to mark
blocks as pinned by memento_id; LRU skips pinned blocks.

**Decision**: defer until we measure CPU↔GPU recall cost. If H2D copy is
< 10ms on our 96GB Blackwell, CPU-only is fine.

### Phase 3: Recall splice

**Goal**: a new request can declare an injected-blocks list. The
scheduler accepts it, the slot_mapping uses the injected slots for the
declared logical positions, and prefill skips those positions.

**Files**:
- Modified: `external/memento/vllm/vllm/v1/core/sched/scheduler.py` —
  honor an injected-blocks list per request.
- Modified: `external/memento/vllm/vllm/v1/core/kv_cache_manager.py` —
  `inject_blocks(request_id, position_range, block_ids)`.
- Modified: `external/memento/vllm/vllm/v1/worker/gpu_model_runner.py` —
  slot_mapping understands injected ranges.
- Modified: `paper2/adapters/memento_vllm.py` — when policy declares a
  recall, render full obs in chronological position AND pass the recall
  metadata into the engine via `prompt_token_ids` extra.

### Phase 4: Empirical validation

A/B v0 (no recall), v1-inplace (cliff), v2-append (cheap addendum),
v3-spliced (cheap chronological). Measure cached_tokens, wall, resolve
on pytest-7490 multi-seed.

## Phase 1 detailed implementation

### MementoStore

```python
# external/memento/vllm/vllm/v1/core/block_masking/memento_store.py

from dataclasses import dataclass, field
from typing import Optional
import torch

@dataclass
class StoredMemento:
    memento_id: str            # opaque, generated by the policy
    request_id: str            # which request originally captured this
    logical_positions: tuple[int, int]  # [start, end) in original request
    cpu_kv: list[torch.Tensor]  # one per layer; shape [2, num_blocks, ...]
                                # 2 = K, V
    block_size: int
    num_layers: int


class MementoStore:
    """Process-global stash of captured obs KV. Indexed by memento_id."""

    def __init__(self):
        self._store: dict[str, StoredMemento] = {}

    def stash(self, memento: StoredMemento) -> None:
        self._store[memento.memento_id] = memento

    def get(self, memento_id: str) -> Optional[StoredMemento]:
        return self._store.get(memento_id)

    def evict(self, memento_id: str) -> bool:
        return self._store.pop(memento_id, None) is not None

    def total_cpu_bytes(self) -> int:
        return sum(
            sum(t.numel() * t.element_size() for t in m.cpu_kv)
            for m in self._store.values()
        )

    def __len__(self) -> int:
        return len(self._store)


# Singleton accessor — the engine needs one per process.
_GLOBAL_STORE: Optional[MementoStore] = None

def global_memento_store() -> MementoStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = MementoStore()
    return _GLOBAL_STORE
```

### compact_kv_cache modification

In `single_type_kv_cache_manager.py`, change `compact_kv_cache` to
accept an optional `(memento_id, logical_range)` parameter. When set,
before computing copy_operations, we identify which physical slots
correspond to the inactive (obs) range, slice the K,V tensors at those
slots from GPU memory, copy to CPU pinned tensors, and stash a
StoredMemento.

The actual K,V tensors live in the worker's `self.kv_caches` (a list,
one per layer). The single_type_kv_cache_manager doesn't directly
access them — it tells the worker via copy_ops what to do. So the
capture has to happen IN the worker, not the manager.

**Revised plan**: introduce a "capture_op" alongside copy_ops. The
manager builds the list `[("capture", src_block_ids, memento_id),
("copy", src→dst), ...]`. The worker executes captures first (GPU→CPU
async copy), then copies, then frees.

### Worker change in gpu_model_runner.py

`_execute_kv_copy_operations` already iterates copy_ops and runs the
torch ops. We add a sibling `_execute_kv_capture_operations` that
iterates the captures, slices the layer KV caches at the source blocks,
copies to pinned host buffers, and stashes via `global_memento_store()`.

### What ships in Phase 1

- New file: `memento_store.py` with the MementoStore singleton.
- Modified: `single_type_kv_cache_manager.compact_kv_cache` signature
  + capture_op generation.
- Modified: `gpu_model_runner._execute_kv_capture_operations` worker
  hook + wiring in `_execute_kv_copy_operations` call site.
- Modified: `scheduler.mask_token_span` accepts a `memento_id`.
- Test: a microbench that runs a 2-block compaction with a memento_id,
  asserts MementoStore has the bytes after, verifies the CPU tensor
  values match what was on GPU before.

Phase 1 is ~300 LOC across 4 files plus the test.

## Validity invariant the policy must enforce

For Phase 3 to work correctly, the policy MUST guarantee: once an obs
at position P is captured, no earlier message in the trajectory may be
re-rendered. This means:

- ✓ Compaction may add NEW mementoes only on positions ≥ P. (Memory:
  current policy is oldest-first, so older positions get mementoed
  BEFORE P. P is captured AFTER its own compaction — P's prefix at
  capture time has already gone through all the mementoes that will
  ever land before it.)

Wait — this is wrong. With oldest-first: at time T0, position P_0 is
mementoed first. Then P_1, P_2, etc. Each mementoing changes the byte
stream up through that position. By the time we memento P_3, the prefix
up through P_3 has changed (P_0, P_1, P_2 are now mementoes, not full
obs).

The byte prefix UP TO POSITION P_3 stabilizes once all P_i for i < 3
have been mementoed. After that, no more changes will happen before
P_3.

So we should capture P_3's KV AFTER it gets mementoed (when its prefix
is stable). At that moment, P_3's KV was computed against [P_0_full,
P_1_full, P_2_full, ...] (the prefix as it was when chat() last ran).

But the prefix at recall time will be [P_0_memento, P_1_memento,
P_2_memento, ..., P_3_memento, ..., P_now_full]. Different from compute
time.

The staleness is real. We accept it. The mementoes are summaries of
what the deeper prefix contained, so the staleness is not arbitrary
noise — it's a "drop-in summary" replacement.

## Risk register

1. **Quality cliff under stale KV**: if the model's attention to stale
   obs K/V produces gibberish, v3 is dead. Mitigation: a microbench
   that replays a known prompt with stale KV vs fresh KV; measure
   logit divergence at the next token. If mean KL > some threshold,
   we know v3 is risky.

2. **Block_pool LRU evicting before we capture**: if compaction frees
   the blocks before our worker capture-op runs, we capture noise.
   Mitigation: capture happens inline in `_execute_kv_copy_operations`
   BEFORE the free (current order is copy-then-free; capture goes
   first).

3. **Hash-bypass injecting wrong blocks**: Phase 3 risk, not Phase 1.
   Need a strong invariant that injected logical positions match the
   prompt's rendered obs positions.

4. **CPU memory growth**: each obs is a few hundred tokens × 30 layers
   × 2048 head_dim × 2 (K,V) × 2 bytes (bfloat16) = ~25 MB per obs.
   At 20 mementoes per task: 500 MB per task. Across many tasks: GBs.
   Mitigation: bound MementoStore size; LRU evict on overflow.
