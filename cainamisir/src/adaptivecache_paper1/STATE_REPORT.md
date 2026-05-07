# AdaptiveCache — State Report (2026-04-09)

## What's Built and Working

### Core Library (`src/adaptive_cache/`) — 41 tests passing

| File | Status | Notes |
|---|---|---|
| `scorer.py` | ✅ Complete | 2D importance×stability scoring: structural priors, reference counting, importance variance, cumulative attention (wired to MockAttentionHook) |
| `evictor.py` | ✅ Complete | Budget cascade eviction |
| `layout.py` | ✅ Complete | Zone assignment (PIN/MIDDLE/SUFFIX) and reorg |
| `cache.py` | ✅ Complete | Message-level middleware, wired with MockAttentionHook |
| `kv_controller.py` | ✅ Complete | Two-tier controller: Tier 1 KV block deletion, Tier 2 message reorg |
| `baselines.py` | ✅ Complete | FIFO, LRU, FixedWindow baselines |
| `config.py` | ✅ Complete | CacheConfig with all hyperparameters |
| `segmenter.py` | ✅ Complete | Message → Block parser |
| `types.py` | ✅ Complete | Block, BlockType, Zone dataclasses |

### Unified Inference Server (`modal_app/serve_unified.py`)

Single Modal deployment replacing both `serve.py` and `serve_v2.py`:
- **Model**: Qwen3-30B-A3B on A100-80GB (MoE, 3B active params, fast)
- **vLLM**: 0.8.5 with prefix caching enabled
- **LMCache**: v0.4.3 integrated via LMCacheConnectorV1
  - Patched for vLLM 0.8.5 compatibility: default `engine_id`, graceful `lookup_client=None`
- **HTTP endpoint**: OpenAI-compatible, tool calls work (hermes parser), litellm/mini-swe-agent compatible
- **Block management methods**: `delete_kv_blocks()`, `reset_lmcache()`, `verify_block_hashes()`
- **URL**: `https://vcainamisir--adaptivecache-unified-llmserver-serve.modal.run`

### SWE-bench Harness (`modal_app/run_experiments.py`)

End-to-end pipeline, working:
- 4 policies: `none`, `fifo`, `adaptive`, `kv_adaptive`
- mini-swe-agent 2.2.8 with local repo clone (no Docker needed in Modal)
- Bundled SWE-bench instances (`modal_app/swebench_instances.json`) — no HuggingFace dependency at runtime
- Results write to Modal volume `adaptivecache-results`
- 20 CPU containers (5 per policy) + 1 GPU container

### Visualization

| Script | What it does |
|---|---|
| `scripts/visualize_v2.py` | Paper figures from `run_experiment_v2` JSON: hit-rate-over-steps line chart + before/after eviction bar chart |
| `scripts/viewer.py` | Full conversation viewer: messages, bash commands, think blocks (collapsible), eviction markers, policy filter |
| `scripts/dashboard.py` | HTML dashboard for SWE-bench trajectory files |

---

## Key Experimental Results

### Cache Hit Rate Experiment (`run_experiment_v2.py`, Qwen2.5-7B, 10 steps, budget=500 tokens)

Results in `results/v2_experiment_1775540387.json`:

| Policy | Mean Hit Rate | After Eviction | Notes |
|---|---|---|---|
| `none` | 94.4% | N/A | Full context, no eviction |
| `kv_adaptive` | **98.6%** | 99.2% | Hole-leaving preserves prefix |
| `fifo` | 40.1% | **15.7%** | Eviction destroys prefix cache |

Paper figures: `results/v2_experiment_1775540387_hit_rate.png`, `results/v2_experiment_1775540387_before_after.png`

### SWE-bench Experiments — Qwen3-8B, budget=32K (partial, interrupted)

5 astropy instances, 3-5 completed per policy:

| Policy | Patches (done) | Avg Steps | Avg Max Prompt |
|---|---|---|---|
| `none` | 2/3 | 46.0 | 23,327 |
| `fifo` | 1/2 | 14.0 | 6,003 |
| `adaptive` | **3/3** | **12.7** | 6,699 |
| `kv_adaptive` | **3/3** | 21.0 | 10,153 |

### SWE-bench Experiments — Qwen3-30B-A3B, budget=10K (partial, interrupted)

| Policy | Notable findings |
|---|---|
| `none` | Stays under 10K for easy instances (6-15 steps) |
| `fifo` | Evicts 31–51 messages but still submits patches |
| `adaptive` | Solves without needing to evict at all on most instances |
| `kv_adaptive` | Similar to adaptive; Tier 1 block deletion path not yet verified |

**Server-side LMCache stats** (2,000+ requests): 76.3% hit rate, avg 10,977 tokens saved per request.

---

## Known Issues / Bugs

### 1. Cache Hit Rate Logging Broken in Trajectories
**Symptom**: `cache_read_tokens` = 0 in all trajectory files.  
**Root cause**: `_log()` in `swe_model.py` reads `cache_read_input_tokens` (Anthropic-only). For vLLM, tried `prompt_tokens_details.cached_tokens` but vLLM 0.8.5 returns `prompt_tokens_details: null`.  
**Fix needed**: Poll vLLM Prometheus `/metrics` endpoint delta per request (like `serve_v2.py` did). Real hits are visible in server logs but not per-trajectory.

### 2. kv_adaptive Tier 1 Not Verified
**Symptom**: `delete_kv_blocks()` may return 0 (wrong hashes or engine not accessible).  
**Diagnosis tool**: `verify_block_hashes()` Modal method — lists LMCache keys and checks overlap with our SHA256 chain.  
**Root cause uncertainty**: Hash scheme (`compute_block_hashes`) may not match LMCache v0.4.3's internal scheme.

### 3. Warm Cache Bias in Policy Comparison
**Symptom**: All 4 policies run in parallel sharing the same LMCache CPU store. `none` computes long contexts that get cached and reused by `fifo`/`adaptive`.  
**Fix**: Run policies sequentially with `reset_lmcache()` between each. `LMCacheClient.reset_lmcache()` exists but isn't called in the parallel run.

### 4. SWE-bench Resolve Rate Unknown
Patches are submitted but `sb-cli` evaluation has never been run. We don't know which patches actually fix the bugs.

### 5. `messages_evicted` Not Logging Correctly
**Symptom**: Always 0 in the viewer even when FIFO clearly evicted 31–51 messages.  
**Root cause**: `messages_evicted = len(original_msgs) - len(sent_msgs)` is correct in `_log()`, but the field is logged per-step and eviction fires in the middle of a step, not at the step boundary visible to `_log()`.

---

## Architecture: What Lives Where

```
serve_unified.py          ← GPU server (Qwen3-30B + LMCache), deployed on Modal
run_experiments.py        ← CPU harness dispatcher (4×5 containers), calls serve_unified
swe_model.py              ← AdaptiveCacheModel (all 4 policies), runs inside CPU containers
lmcache_client.py         ← Client to serve_unified's Modal methods (delete/pin/reset blocks)
kv_controller.py          ← Scoring + eviction logic, runs inside CPU containers
```

The **Tier 1 KV eviction loop**:
1. Agent sends full messages → serve_unified (litellm → vLLM → LMCache)
2. `kv_controller.on_step_complete()` scores blocks, decides which to evict
3. `lmcache_client.delete_kv_blocks()` → Modal method → `serve_unified.delete_kv_blocks()` → LMCache internal API (port 6999)
4. Next request: evicted positions are cache misses, all others are hits

**Tier 1 is wired but unverified** (hash scheme uncertain, internal API untested end-to-end).

---

## Files to Look at First

- `wiki/system-design.md` — full technical spec and two-tier architecture
- `results/v2_experiment_1775540387.json` — main cache hit rate results
- `modal_app/serve_unified.py` — the unified server
- `src/adaptive_cache/kv_controller.py` — the core eviction logic
- `src/harness/swe_model.py` — mini-swe-agent integration
 