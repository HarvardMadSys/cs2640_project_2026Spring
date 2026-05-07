# No-pin natural-eviction bake — confirms manual cache insertion is needed

Date: 2026-05-05
Modal run: `1778008115` (1481s wall, 24 cells)

## Setup

* Modal H100 80GB
* Qwen3-30B-A3B-Instruct-2507
* `attention_mask_mode=False`, `PAPER2_NO_PIN=1`
* 4 tasks × 2 seeds × 3 variants = 24 cells, 25-step cap
* `budget_tokens=12_000`, `recall_low_water=3.0`

## Headline (8 cells per variant, 2 seeds)

```
variant         wall_s     prompt  cache%  comp  rec  res
off              114.8    1382388   46.7%    60    0  2/8
lru-inplace      210.9    3099923   40.3%    82   41  2/8
lru-append       224.8    3930881   67.4%    80   52  2/8
```

* **vs the attmask=True bake (`1777964225`)**: off was 295.5s in attmask, 114.8s here
  — **~60% wall reduction** just from disabling attmask. The carry cost of
  "obs always in prompt with markers" dominates everything else.
* **lru-inplace cache hit is 40%** — vLLM's natural prefix cache misses on the
  suffix because the recall prompt's chain hash differs from the drop-period
  chain hash (obs reintroduced in the middle changes everything downstream).
* **lru-append cache hit is 67%** — better because the recall pattern appends
  obs to the tail, leaving the chain through earlier tokens intact.

## Why this confirms the design

If vLLM's native prefix cache could naturally absorb the chain-hash mismatch
on `lru-inplace` recall, we'd see 80%+ cache hit. Instead it's 40% — meaning
60% of recall steps eat re-prefill cost.

The fix is **manual cache re-keying**: when recall fires, walk the suffix's
existing cached blocks (under the compacted-period chain hash) and insert
them into `BlockHashToBlockMap` under the recall-prompt's chain hash too.
Pure metadata operation — no data movement, no kernel work.

This pushes the 40% cache hit rate toward ~95+% on lru-inplace recall steps,
directly eliminating the re-prefill cost.

## Resolves stay flat

Same resolve rate (2/8 = 25%) across all variants. SWE-bench Lite resolves
are dominated by which tasks the model can solve at all, not by recall
behavior. Pylint resolves under all variants; the others mostly don't.

## Next

* Build `scheduler.dual_key_cached_blocks_for_recall(request_id, recall_prompt_token_ids)`
  in the vLLM overlay. Walks the suffix block-by-block, computes the recall
  chain hash via `vllm.v1.core.kv_cache_utils.hash_block_tokens`, calls
  `block_pool.cached_block_hash_to_block.insert()` to dual-key.
* Wire into the adapter at recall time (alongside `queue_recall` already
  there for attmask).
* Smoke: validate that lru-inplace recall steps under no-pin show >90%
  cache hit after dual-keying.
