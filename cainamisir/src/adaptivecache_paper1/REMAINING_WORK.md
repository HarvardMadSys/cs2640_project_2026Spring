# AdaptiveCache — Remaining Work

Written after session on 2026-04-07. All phases A–E were implemented; what follows is what needs polish, fixes, or extension.

---

## 1. LMCache Block-Level Demo (the hole-leaving proof)

**What we want:** Call `delete_kv_blocks([chunk_i])`, replay the same prompt, show that only chunk i is recomputed while chunks 0..i-1 and i+1..N are still LMCache CPU hits.

**Why it's blocked:** vLLM 0.8.5 runs the model in a worker subprocess. LMCache's engine lives there — `LMCacheEngineBuilder._instances` is empty in the main process, so `delete_kv_blocks()` silently no-ops.

**Clean fix:** Enable LMCache's internal HTTP API server in `serve.py`:
```python
os.environ["LMCACHE_INTERNAL_API_SERVER_ENABLED"] = "True"
# Then delete blocks via: POST http://localhost:6999/delete_blocks
```
This exposes LMCache's management API from inside the worker process over a local port.

**Alternative:** Downgrade vLLM to 0.6.x where the V0 (single-process) engine is the default, making LMCache directly accessible. But this loses the `num_cached_tokens` Prometheus metric.

---

## 2. Fix `num_cached_tokens` Capture in serve.py (LMCache server)

`lmcache_hit_tokens` and `lmcache_computed_tokens` return 0 in the `generate()` response because LMCache's Python logging goes to the worker subprocess's stdout, not the main process's `sys.stderr`. The `_LogCapture` handler only sees main-process logs.

**Fix options:**
- Add `LMCACHE_INTERNAL_API_SERVER_ENABLED=True` and poll stats via HTTP after each request
- Or parse the per-request stats from the Prometheus `/metrics` endpoint delta (like we do in `serve_v2.py`)

---

## 3. Rerun the Policy Experiment on Fresh Containers (fairness)

The current `run_experiment_v2.py` results (none=94.4%, kv_adaptive=98.6%, fifo=40.1%) have a mild warm-cache bias: all three policies share the same LLM server container, so `fifo` and `kv_adaptive` benefit from `none`'s warm-up.

For the paper: run each policy in its own fresh container (separate Modal function invocations with explicit container separation), or add a cache-clearing call between policies.

The qualitative result holds regardless — FIFO crashing to 16% is too dramatic to explain away — but the exact numbers will be slightly different.

---

## 4. Larger Scale Experiments (SWE-bench)

`modal_app/run_experiments.py` is written but not tested end-to-end. The SWE-bench harness requires:
- Docker or local repo setup per instance
- mini-swe-agent properly configured for the Modal-hosted LLM
- Evaluation via sb-cli

The `kv_adaptive` policy in `swe_model.py` currently falls back to litellm when `kv_client` isn't configured. Wire it to `LMCacheClient(mode="modal")` pointing at `serve_v2.py`.

---

## 5. Attention-Based Scoring (Phase C — 100% milestone)

`src/adaptive_cache/scorer.py` has stubs for:
- `_cumulative_attention_signal()` — H2O-style running mean attention weights
- `_dependency_centrality_signal()` — DAG centrality from tool call chains

These require real attention weights from the model. With vLLM + the EAGER attention backend (`--attention-backend EAGER`), attention matrices are accessible via forward hooks. `src/harness/attention_hook.py` has the `VLLMAttentionHook` stub ready.

Enable with `w_cumulative_attention=0.3, w_dependency_centrality=0.2` in `CacheConfig`.

---

## 6. Hash Verification for `delete_kv_blocks()`

`compute_block_hashes_v1()` in `serve.py` uses a SHA256 chain that we *believe* matches LMCache's internal hash scheme. This has not been verified to match exactly — we always get `deleted=0` which could mean: (a) wrong hashes, (b) engine not accessible, or (c) `save_decode_cache` not populating CPU store.

Once the internal API server fix is in, verify by:
1. List all keys in LMCache's CPU store
2. Compute our hashes for the same prompt
3. Check they match

---

## 7. Results Visualization

`results/v2_experiment_1775540387.json` has the key data. Need:
- A figure showing hit rate over steps for all 3 policies, with a vertical line where eviction fires
- The "before/after eviction" bar chart (92%→16% for FIFO vs 98%→99% for kv_adaptive)
- Update `scripts/dashboard.py` to show the new LMCache metrics

---

## What's Done and Works

| Component | Status |
|---|---|
| vLLM 0.8.5 + LMCache 0.4.3 on A100-80GB | ✅ Deployed |
| ABI shim (torch 2.6.0 unsigned int → int) | ✅ Working |
| lmcache built from source with correct arch | ✅ Working |
| vLLM 0.8.5 compatibility patches | ✅ Applied |
| vLLM OpenAI server with real `cached_tokens` | ✅ Working |
| 3-policy experiment with Prometheus metrics | ✅ Results in hand |
| KVController (two-tier, Tier 2 working) | ✅ 41 tests pass |
| Policy comparison results | ✅ none=94%, kv_adaptive=99%, fifo=16% after eviction |
| All code committed to git | ✅ master branch |
