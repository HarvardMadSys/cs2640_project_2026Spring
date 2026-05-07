# AdaptiveCache — Implementation Plan for Local KV Cache Eviction

## What This Document Is

A complete handoff document for implementing the remaining work on AdaptiveCache. Includes full context on what exists, what was learned from experiments, and exactly what needs to be built.

## Project Location

`/Users/cnmsr/Projects/cacheKarpathy/`

---

## 1. Background: What AdaptiveCache Is

AdaptiveCache is a research project (Harvard CS, Vlad Cainamisir) proposing a context management system for LLM agents. The core idea: when an agent's context exceeds a token budget, decide what to evict and WHERE to evict it from, optimizing for prefix cache reuse.

**The key insight (the "layout gap"):** All existing context management systems (FIFO, LRU, summarization, H2O, SnapKV, etc.) decide WHAT to keep but ignore WHERE surviving items sit. This prevents cross-step prefix cache reuse. AdaptiveCache jointly addresses both.

**The thesis:** On modern LLM serving systems that exploit prefix caching, the POSITION of eviction determines the cost:
- Evicting from the beginning of the conversation destroys the cached prefix (expensive)
- Evicting from the middle preserves the prefix for everything before the eviction point (cheap)
- The optimal strategy: evict low-value content from as late in the conversation as possible

**Full research context:** Read `wiki/system-design.md` (the authoritative spec) and `wiki/overview.md`.

---

## 2. What Already Exists

### 2a. Core Library (`src/adaptive_cache/`)

A Python package implementing the AdaptiveCache scoring and eviction pipeline. **This was designed for the API-level approach (modifying message lists) which we've now concluded is the WRONG abstraction.** The correct approach is KV-level eviction on a local server. However, the scoring logic is still useful.

| File | What it does | Reusable? |
|---|---|---|
| `types.py` | Block, BlockType, Zone dataclasses | Yes — data model is sound |
| `config.py` | CacheConfig with thresholds/weights | Yes |
| `segmenter.py` | Parse messages into typed blocks | Yes — for scoring |
| `scorer.py` | 2D importance×stability scoring with 7 signals | **Yes — this is the core value** |
| `evictor.py` | Eviction engine (budget cascade) | Yes — used for Tier 2 (message-level reorganization) |
| `layout.py` | Zone assignment + reordering | Yes — used for Tier 2 (reorganize message order) |
| `cache.py` | AdaptiveCache middleware (message-level) | Yes — used for Tier 2. Tier 1 (KV-level) is the new `kv_controller.py` |
| `baselines.py` | FIFO, LRU, FixedWindow | Keep for comparison |

### 2b. SWE-bench Harness (`src/harness/`)

Integration with mini-swe-agent for running experiments on SWE-bench Lite.

| File | What it does |
|---|---|
| `swe_model.py` | AdaptiveCacheModel — subclasses mini-swe-agent's LitellmModel. Currently does message-level eviction. **Needs rewrite for KV approach.** |
| `swe_runner.py` | Batch runner for SWE-bench experiments |
| `swe_config.py` | Experiment configuration |
| `llm_client.py` | Backend-agnostic LLM client |
| `react_agent.py` | Standalone ReAct agent (for non-SWE-bench testing) |
| `tools.py` | Tool registry |
| `trace_logger.py` | JSONL trace logging |
| `attention_hook.py` | Stub for attention weight logging |

### 2c. Experiment Scripts (`scripts/`)

| File | What it does |
|---|---|
| `run_swebench.py` | CLI for single SWE-bench runs |
| `run_experiment_matrix.py` | Batch runner for policy×budget matrix |
| `evaluate.py` | Evaluation via sb-cli |
| `dashboard.py` | HTML dashboard with per-step cache stats, sparklines, drill-down |

### 2d. Wiki (`wiki/`)

28 pages of literature review covering 23 papers. Key pages:
- `system-design.md` — full technical spec
- `kv-cache-architecture.md` — how hole-leaving eviction works (RoPE, PagedEviction)
- `prefix-caching.md` — the cost model
- `importance-scoring.md` — the 2D scoring framework
- `claude-code-compaction.md` — how Claude Code does it (cache_edits, microcompact)

### 2e. Test Suite (`tests/`)

41 tests passing. Covers types, segmenter, scorer, and the swe_model integration.

### 2f. Dependencies

```
pyproject.toml — adaptive-cache package
.env — Anthropic API key (don't commit)
configs/swebench_adaptive.yaml — mini-swe-agent config
```

Key deps: `mini-swe-agent>=2.0`, `tiktoken`, `openai`, `numpy`, `datasets`, `litellm`

---

## 3. What We Learned From Experiments

We ran 5 SWE-bench Lite instances × 4 policies (none, adaptive, fifo, summarize) at 8K token budget with Claude Haiku 4.5. Full results in `results/exp_haiku_5/`. Dashboard at `results/exp_haiku_5/dashboard.html`.

### Key Findings

1. **Baseline (`none`) gets 95% real cache hits for free.** Linear conversation = stable prefix = Anthropic caches automatically. `cache_read_input_tokens` from the API confirms this.

2. **Message-level eviction on the API is fundamentally limited.** Whether you remove messages, replace content with placeholders, or summarize — any byte change at position N invalidates cache from N onward. The only lever is evicting LATER to preserve more prefix. Our position-aware adaptive policy achieved 61% cache hits vs FIFO's 54%.

3. **The real opportunity is KV-level eviction on a local server.** With control over the KV cache, you can evict specific blocks without changing the prompt bytes at all. The prefix cache is never invalidated. This is what Claude Code's `cache_edits` does internally, and what PagedEviction demonstrates in a research setting.

4. **Scoring works.** The structural prior + reference counting + importance variance signals correctly identify high-value vs low-value steps. The issue was never the scorer — it was the eviction mechanism.

5. **Summarization is the worst approach.** Destroys prefix cache, costs extra API calls, and performed worst (44.5% cache hits, $0.146/instance but 0 patches).

---

## 4. What Needs To Be Built

### Phase A: LMCache Integration (the infrastructure layer)

**Goal:** Enable selective KV cache block deletion on a local vLLM server via LMCache.

**LMCache** (https://github.com/LMCache/LMCache, arXiv:2510.09665) is an open-source KV cache management layer that integrates with vLLM and SGLang. It stores KV cache blocks externally (CPU/disk/Redis) and returns a bitmask to vLLM indicating which blocks are cached.

**Key docs:**
- Architecture: https://docs.lmcache.ai/developer_guide/architecture.html
- Integration: https://docs.lmcache.ai/developer_guide/integration.html
- vLLM examples: https://docs.vllm.ai/en/latest/examples/others/lmcache/
- Paper: https://arxiv.org/abs/2510.09665
- CacheBlend (sparse reuse): https://blog.lmcache.ai/en/2024/10/09/beyond-prefix-caching-how-lmcache-speeds-up-rag-by-4-5x-by-one-line-of-change/

**What LMCache provides today:**
- Block-level KV storage (256-token chunks by default, configurable via `chunk_size`)
- `pin(tokens)` API — mark blocks as must-keep
- `clear(location)` API — wipe entire storage tier
- Bitmask protocol — tells vLLM which blocks exist (sparse cache support)
- Multi-tier storage: GPU → CPU → disk → Redis

**What LMCache does NOT provide (we need to add):**
- Per-block deletion by hash — `clear()` is location-level only
- Position-to-block mapping — the scorer needs to say "evict tokens 2048-2304" and translate to the right block hash

**Implementation steps:**

1. **Install and run LMCache + vLLM locally:**
   ```bash
   pip install lmcache
   # vLLM with LMCache connector:
   vllm serve Qwen/Qwen2.5-7B-Instruct \
     --enable-prefix-caching \
     --kv-connector LMCacheConnector \
     --kv-connector-config '{"chunk_size": 256}'
   ```

2. **Extend LMCache's `RemoteConnector` with `delete_by_hash()`:**
   - File: `lmcache/storage_backend/remote_connectors/base.py`
   - Add: `def delete(self, key: str) -> bool` to the abstract base class
   - Implement in `RedisConnector` (trivial: `self.client.delete(key)`)
   - Implement in `LocalCPUBackend` (remove from the memory allocator)

3. **Extend `CacheController` with per-block deletion:**
   - File: `lmcache/controller/cache_controller.py`
   - Add: `def delete_blocks(self, instance_id: str, block_hashes: List[str]) -> int`
   - This removes specific blocks from storage. Next time vLLM queries for them, they return as cache misses, and vLLM recomputes only those blocks.

4. **Build position-to-block mapping:**
   - Track: `{(instance_id, token_position_range) → block_hash}`
   - When the scorer says "evict step at tokens 2048-2560", look up which block hashes cover that range and call `delete_blocks()`.

5. **Verify with a simple test:**
   - Send a 10-step conversation to vLLM
   - Check that all blocks are cached (LMCache hit)
   - Delete blocks for steps 3-5
   - Re-send the same conversation
   - Verify: steps 1-2 are cache hits, steps 3-5 are recomputed, steps 6-10 have their KV recomputed (because they follow the eviction point and the KV may depend on the evicted positions)
   
   **IMPORTANT:** Even with KV-level eviction, recomputation cascades forward. If you evict the KV for position 500, positions 501+ were computed while attending to position 500. With post-rotated RoPE keys, the stored keys at 501+ are still valid, but the VALUE vectors at 501+ were computed attending to 500's KV, so they may be stale. This is the "hole-leaving" assumption — the literature says it works (PagedEviction, H2O, SnapKV all do it), but measure the quality impact.

### Phase B: AdaptiveCache Two-Tier Controller

**Goal:** Implement the two-tier eviction strategy. Tier 1 (KV-level, every step) and Tier 2 (message-level reorganization, rare) work together.

**The two tiers are complementary, not alternatives:**

- **Tier 1 — KV-level eviction (every step, free):** Keep the prompt bytes identical. Delete low-value KV blocks from LMCache. vLLM recomputes only those blocks. Prefix cache fully preserved. This is the "hole-leaving" from system-design.md.

- **Tier 2 — Message-level reorganization (every ~10-20 steps, one cache miss):** When the importance structure has changed significantly (>30% of top-K set changed, or too many holes accumulated), restructure the actual message list: reorder so high-value content is at the prefix, drop stale steps, compact. This invalidates the cache for ONE step. The new layout becomes the stable prefix for all subsequent steps. Break-even analysis: pays for itself after ~3 steps of stability.

**New file: `src/adaptive_cache/kv_controller.py`**

```python
class KVController:
    """Two-tier KV cache management via LMCache + message reorganization.
    
    Tier 1 (every step): Keep prompt identical, delete KV blocks from LMCache.
    Tier 2 (rare): Restructure the message list for a new stable prefix.
    """
    
    def __init__(self, lmcache_client, scorer, config):
        self.lmcache = lmcache_client
        self.scorer = scorer
        self.config = config
        self.block_map = {}  # position_range → block_hash
        self._prev_top_k = set()
        self._steps_since_reorg = 0
    
    def on_step_complete(self, messages, step):
        """Called after each agent step. Returns (messages, reorganized).
        
        Returns the original messages if only Tier 1 fired.
        Returns reorganized messages if Tier 2 fired.
        """
        # 1. Parse messages into steps, score each step
        # 2. Check KV cache size vs budget
        
        # Tier 1: KV-level eviction (free)
        # - Select low-value blocks to evict (position-aware: prefer later)
        # - Call self.lmcache.delete_blocks(block_hashes)
        # - Pin high-value blocks: self.lmcache.pin(block_hashes)
        
        # Tier 2: Check if message-level reorganization needed
        if self._should_reorganize(steps):
            # - Reorder messages: pin zone first, then middle, then suffix
            # - Drop fully evicted steps from the message list
            # - Return new message list (one-time cache miss, new stable prefix)
            return self._reorganize(messages, steps), True
        
        return messages, False
    
    def _should_reorganize(self, steps):
        """Trigger Tier 2 when importance structure has shifted."""
        self._steps_since_reorg += 1
        current_top_k = {s.step_index for s in sorted(steps, key=lambda s: -s.score)[:10]}
        change = len(current_top_k - self._prev_top_k) / max(len(self._prev_top_k), 1)
        return change > 0.3 or self._steps_since_reorg > 20
    
    def _reorganize(self, messages, steps):
        """Tier 2: restructure message list for new stable prefix."""
        # Protected (system + task) first
        # Then high-value steps sorted by score
        # Then recent steps chronologically
        # Drop fully evicted steps
        self._prev_top_k = {s.step_index for s in sorted(steps, key=lambda s: -s.score)[:10]}
        self._steps_since_reorg = 0
        return reorganized_messages
```

**How the two tiers interact with vLLM/LMCache:**

```
Steps 1-15: Tier 1 only
  → Agent sends full conversation (same bytes every time)
  → LMCache: blocks for steps 3, 7, 11 deleted (low value)
  → vLLM: recomputes only those blocks, everything else cached
  → Prefix fully preserved

Step 16: Tier 2 fires (importance structure shifted)
  → Controller reorganizes messages: [sys, task, step2, step5, step14, step15, step16]
  → New prompt = different bytes → one-time full cache miss
  → LMCache stores the new KV blocks
  → This becomes the new stable prefix

Steps 17-35: Tier 1 only
  → Back to KV-level eviction, new prefix stays byte-identical
  → High cache hit rate again
```

**The existing message-level code in `src/adaptive_cache/` (cache.py, layout.py, evictor.py) is reused for Tier 2.** The zone assignment, scoring, and reordering logic already works — it just fires less frequently now.

### Phase C: Attention-Based Scoring (100% milestone)

**Goal:** Replace structural priors with actual attention weights for importance scoring.

The scorer in `src/adaptive_cache/scorer.py` currently uses:
- Signal 5: Structural type prior (zero-cost)
- Signal 3: Reference count (string matching)
- Signal 6: Importance variance (running variance)

The 100% milestone adds:
- Signal 2: Cumulative attention (H2O-style running mean)
- Signal 7: Dependency graph centrality

**Implementation:**

1. **Attention hook for vLLM:**
   - File: `src/harness/attention_hook.py` (currently a stub)
   - vLLM exposes attention weights via `output.outputs[0].attention` when `--return-attention` is enabled (check vLLM docs — this may require a specific version or flag)
   - Alternative: register a forward hook on the model's attention layers. With vLLM's `LLM` class, you can access the model via `llm.llm_engine.model_executor.driver_worker.model_runner.model`
   - Log: per-block attention received (sum attention from all positions beyond the block to positions within the block)

2. **Wire attention into scorer:**
   - After each step, the attention hook produces: `{block_id: mean_attention_received}`
   - Feed into `block.attention_history`
   - The scorer's `_cumulative_attention_signal()` already has the logic, just returns 0.0 because `attention_history` is empty

3. **Dependency graph:**
   - Build a DAG from tool call chains: `grep → file_read → edit → test`
   - Blocks upstream in the DAG get higher centrality scores
   - Implementation: track which tool calls reference content from which earlier tool results

### Phase D: Experiment Infrastructure

**Goal:** Run the real experiments from `wiki/detailed-research-plan.md`.

**Requirements:**
- A100 GPU (or equivalent) with vLLM + LMCache
- Qwen2.5-7B-Instruct for development/ablations
- SWE-bench Lite (300 instances)

**Experiments (from detailed-research-plan.md):**

| Exp | Description | What it measures |
|---|---|---|
| 1.1 | Vanilla ReAct baseline (300 instances, full context) | Baseline performance, attention traces |
| 1.2 | Structural prior validation (analysis only) | Do structural priors match empirical attention? |
| 1.3 | FIFO/LRU/FixedWindow baselines | Pareto frontier for naive eviction |
| 2.1 | Layout isolation (score vs score+layout) | Does KV-level eviction actually improve cache hits? |
| 2.2 | Scoring signal ablation (6 configs) | Which signals contribute? |
| 2.3 | Budget sweep (128K, 64K, 32K, 16K) | Cost-performance Pareto curve |
| 2.5 | Full comparison vs all baselines | Headline result |

**The existing experiment infrastructure works:**
- `scripts/run_experiment_matrix.py` — batch runner with parallel execution
- `scripts/dashboard.py` — HTML dashboard with real cache stats
- `src/harness/swe_runner.py` — SWE-bench instance management
- `src/harness/swe_model.py` — model wrapper (needs rewrite for KV approach)

**What needs to change for local experiments:**
1. Replace Anthropic API calls with local vLLM server
2. Replace message-level eviction with KV-level eviction via LMCache
3. Add attention logging hook
4. The scorer, dashboard, and experiment runner stay the same

### Phase E: Integration with mini-swe-agent (updated)

**The `swe_model.py` rewrite:**

Currently, `AdaptiveCacheModel` subclasses `LitellmModel` and modifies the message list before sending to the API. For the KV approach:

1. **Don't modify messages at all.** Pass the full conversation to vLLM every time.
2. **After each response**, call `kv_controller.on_step_complete()` which:
   - Scores all blocks
   - Evicts low-value blocks from LMCache
   - Pins high-value blocks
3. **The next vLLM call** automatically gets cache hits for pinned blocks and recomputes evicted blocks.

The `query()` override becomes:

```python
def query(self, messages, **kwargs):
    # Call KV controller — may return reorganized messages (Tier 2) or original (Tier 1)
    managed_messages, reorganized = self.kv_controller.on_step_complete(messages, self._call_count)
    
    # Send messages to LLM (original if Tier 1, reorganized if Tier 2)
    result = super().query(managed_messages, **kwargs)
    
    # Log real cache stats
    self._log(messages, managed_messages, result)
    return result
```

When Tier 1 fires: `managed_messages == messages` (same bytes, KV eviction happened in LMCache).
When Tier 2 fires: `managed_messages` is the reorganized list (one-time cache miss, new stable prefix).

---

## 5. User Preferences

- **Use SGLang or vLLM** — user initially preferred SGLang ("vllm I hate") but is now OK with vLLM for this specific use case since LMCache integrates with both. Check which has better LMCache support at the time of implementation.
- **No step limits or cost limits** on experiments — let the agent run until it submits or gives up.
- **Honest comparisons** — always include `none` (baseline), `summarize` (OpenHands-style), and `fifo` alongside adaptive. Don't strawman.
- **Real cache stats** — always log `cache_read_input_tokens` / `prompt_tokens` from the API response, not fake estimates.

---

## 6. Priority Order

1. **Phase A** (LMCache integration) — this is the critical infrastructure. Without it, nothing else works locally.
2. **Phase B** (KV controller) — the core AdaptiveCache logic for KV-level eviction.
3. **Phase D** (experiments) — run Experiment 1.1 (vanilla baseline with attention logging) first, then 2.1 (layout isolation).
4. **Phase C** (attention scoring) — depends on having attention traces from Phase D's Experiment 1.1.
5. **Phase E** (mini-swe-agent rewrite) — depends on Phases A and B.

---

## 7. File Inventory

```
/Users/cnmsr/Projects/cacheKarpathy/
├── CLAUDE.md                      # Project rules (read first)
├── IMPLEMENTATION_PLAN.md         # This file
├── pyproject.toml                 # Package config
├── .env                           # API keys (don't commit)
├── wiki/                          # 28-page research wiki
│   ├── system-design.md           # ★ Full technical spec
│   ├── overview.md                # Project overview
│   ├── kv-cache-architecture.md   # ★ Hole-leaving eviction mechanics
│   ├── prefix-caching.md          # ★ Cost model
│   ├── importance-scoring.md      # ★ Scoring framework
│   ├── detailed-research-plan.md  # ★ Experiment plan with compute budget
│   ├── claude-code-compaction.md  # How Claude Code does it
│   └── ... (20+ more pages)
├── raw/
│   ├── claude_code_compaction.md  # ★ Full Claude Code compaction reference
│   └── papers/                    # 15 PDFs
├── src/
│   ├── adaptive_cache/            # Core library (scoring is reusable)
│   │   ├── types.py               # Block, BlockType, Zone
│   │   ├── config.py              # CacheConfig
│   │   ├── scorer.py              # ★ 2D importance×stability scoring
│   │   ├── segmenter.py           # Message → Block parser
│   │   ├── evictor.py             # Message-level eviction (replace for KV)
│   │   ├── layout.py              # Zone assignment
│   │   └── cache.py               # Message-level middleware (replace for KV)
│   └── harness/                   # Experiment infrastructure
│       ├── swe_model.py           # ★ mini-swe-agent model wrapper
│       ├── swe_runner.py          # Batch SWE-bench runner
│       ├── swe_config.py          # Experiment config
│       ├── attention_hook.py      # Stub — needs implementation
│       └── ...
├── scripts/
│   ├── run_swebench.py            # CLI entry point
│   ├── run_experiment_matrix.py   # Batch experiment runner
│   ├── dashboard.py               # ★ HTML dashboard
│   └── evaluate.py                # sb-cli evaluation
├── configs/
│   └── swebench_adaptive.yaml     # mini-swe-agent config
├── tests/                         # 41 tests passing
└── results/
    └── exp_haiku_5/               # Experiment results + dashboard.html
```

---

## 8. Key External Resources

| Resource | URL | Why it matters |
|---|---|---|
| LMCache repo | https://github.com/LMCache/LMCache | The KV cache layer we'll extend |
| LMCache docs | https://docs.lmcache.ai/ | Architecture, integration guide |
| LMCache paper | https://arxiv.org/abs/2510.09665 | Technical details |
| vLLM docs | https://docs.vllm.ai/ | Inference server |
| vLLM + LMCache | https://docs.vllm.ai/en/latest/examples/others/lmcache/ | Integration examples |
| mini-swe-agent | https://github.com/SWE-agent/mini-swe-agent | Agent framework |
| SWE-bench | https://www.swebench.com/ | Benchmark |
| PagedEviction paper | https://arxiv.org/abs/2509.04377 | Block-level KV eviction reference |
| KVzap paper | https://arxiv.org/abs/2601.07891 | Compatible with PagedAttention |
| Awesome KV Cache | https://github.com/TreeAI-Lab/Awesome-KV-Cache-Management | Survey of 100+ papers |
