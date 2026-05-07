# AdaptiveCache

Live-context management for LLM agents — selection, eviction, and reordering optimized for prefix-cache reuse. Two papers in this repo:

- **Paper 1 (lifetime-cost study).** Eight training-free heuristic compaction policies × SWE-bench Lite live + τ-bench retail × Qwen3-30B-A3B and Anthropic Haiku 4.5. Headline: **no policy Pareto-dominates `none`** under prefix-cache-aware pricing. The cliff tax structurally beats the byte-saved benefit.
- **Paper 2 (KV-pointer recall).** A vLLM 0.13.0 overlay that captures evicted observations to a CPU side cache, splices them back at recall via in-place RoPE-composed K rotation, and registers chain-hash entries append-only. The mechanism is the storage-systems insight that "re-position cached blocks without re-prefilling" actually requires.

Final report: `paper/final_report.tex` (and `final_report.pdf`).

---

## Repository layout

```
.
├── external/memento/              # microsoft/memento at d8c10e6 + our v3 patches (vLLM 0.13.0 overlay)
├── paper/                         # final_report.tex / .pdf, paper2 architecture JSONs
├── raw/                           # immutable source PDFs (research wiki inputs)
├── results/                       # CSV / JSON of completed bakes (gitignored most)
├── scripts/                       # eval / dashboard / experiment-matrix CLIs
├── src/                           # Paper 1 framework code
├── studies/lifetime_cost/         # Paper 1 study
│   ├── pipeline/                  # benchmarks + runner + tokenizer utilities
│   ├── reports/                   # Paper 1 figures + .pptx decks
│   ├── scripts/                   # figure generators
│   └── paper2/                    # Paper 2 study (Phase 1–9)
│       ├── adapters/              # MementoVLLMModel (vLLM adapter)
│       ├── modal_app/             # Modal containers (image.py + entrypoints)
│       ├── policy/                # MementoPolicy (compaction + recall)
│       ├── tests/                 # microbenches + smokes
│       ├── v3_overlay_patches/    # vLLM patch + memento_store.py.new + apply.sh
│       ├── PHASE_*.md             # phase plans / handoffs
│       └── validate_recall.py     # the bake driver
├── tests/                         # Paper 1 unit tests
└── wiki/                          # research wiki (LLM-maintained)
```

---

## Setup

Two pip envs:

```bash
# Paper 1 env (model-agnostic, no vLLM)
python3 -m venv ~/adaptivecache/.venv
~/adaptivecache/.venv/bin/pip install -e ".[dev]"

# Paper 2 env (Modal client only — vLLM lives inside the container)
python3 -m venv ~/adaptivecache-paper2/.venv-paper2
~/adaptivecache-paper2/.venv-paper2/bin/pip install modal anthropic openai datasets
```

Modal auth + secret (one-time):

```bash
~/adaptivecache/.venv/bin/modal token new
# Paper 1 keys (Anthropic / OpenAI for cliff measurement on real traces)
echo "ANTHROPIC_API_KEY=sk-ant-…" >> ~/adaptivecache/.env
echo "OPENAI_API_KEY=sk-…"        >> ~/adaptivecache/.env
# Paper 2 reuses the .env via from_dotenv in modal_app/run_validate_recall.py
```

---

## Paper 1 — Lifetime-cost study

### Reproduce the headline cost-per-resolved table

Real SWE-bench Lite live runs (Anthropic Haiku 4.5):

```bash
~/adaptivecache/.venv/bin/python scripts/run_experiment_matrix.py \
  --benchmark swebench_live \
  --policies none plain facts outline \
  --n-tasks 10 --seeds 0 \
  --out results/phase_e_haiku
```

Cost decomposition + analyses (figures land in `studies/lifetime_cost/reports/figures/`):

```bash
~/adaptivecache/.venv/bin/python studies/lifetime_cost/scripts/analyze_evictable.py
~/adaptivecache/.venv/bin/python studies/lifetime_cost/scripts/measure_tool_obs_share.py
~/adaptivecache/.venv/bin/python studies/lifetime_cost/scripts/run_ablations.py
```

Build the talk deck + appendix from the latest results:

```bash
~/adaptivecache/.venv/bin/python studies/lifetime_cost/scripts/make_pptx.py
# writes AdaptiveCache_talk.pptx + AdaptiveCache_appendix.pptx
```

### τ-bench retail (multi-customer chain)

```bash
~/adaptivecache/.venv/bin/python scripts/run_swebench.py \
  --benchmark tau_retail --chain-size 10 \
  --policies none lru_evict embedding ...
```

Outputs land in `results/<run_id>/`. See `studies/lifetime_cost/METHODS.md` for the full method.

---

## Paper 2 — KV-pointer recall

All Phase-2 work runs on **Modal H100** because vLLM 0.13.0 + Memento overlay needs CUDA 12.8 + FlashInfer + the patched scheduler.

### One-time: build the Modal image

The `image.py` file pins vLLM 0.13.0 + the Memento overlay at commit `d8c10e6` and applies our v3/v4/v8/v9 patches via `studies/lifetime_cost/paper2/v3_overlay_patches/apply.sh`. Modal rebuilds automatically when any of these change:

- `external/memento/` (we don't ship; container clones it fresh)
- `studies/lifetime_cost/paper2/v3_overlay_patches/v3_phase1_modifications.patch`
- `studies/lifetime_cost/paper2/v3_overlay_patches/memento_store.py.new`
- Any local Python under `studies/lifetime_cost/paper2/`

### Validate the recall mechanism (1-task, ~10 min)

Forces recalls aggressively to exercise the Phase 9 capture→splice→rotate path:

```bash
cd ~/adaptivecache-paper2
~/adaptivecache/.venv/bin/modal run -d -m studies.lifetime_cost.paper2.modal_app.run_validate_recall \
  --instances "pytest-dev__pytest-7490" \
  --n-seeds 1 \
  --temperature 0.0 \
  --variants "lru-kvrestore" \
  --max-steps 20 \
  --recall-low-water 99 \
  --no-pin
```

Expected: 5–10 captures + 5–10 recalls + 0 crashes. See `[v3-restore-splice]`, `[v9-rotate-queued]`, `[v9-sched-shrink]` lines in the worker logs.

### Pareto comparison (none vs lru-kvrestore)

```bash
cd ~/adaptivecache-paper2
~/adaptivecache/.venv/bin/modal run -d -m studies.lifetime_cost.paper2.modal_app.run_validate_recall \
  --instances "pytest-dev__pytest-7490,django__django-11099,sympy__sympy-13177" \
  --n-seeds 3 \
  --temperature 0.6 \
  --variants "none,lru-kvrestore" \
  --max-steps 40 \
  --recall-low-water 0.85 \
  --budget-tokens 12000 \
  --hard-budget-tokens 16000 \
  --no-pin \
  --profile-mem
```

Results stream to the persistent `paper2-out-v3` Modal volume. Pull locally:

```bash
~/adaptivecache/.venv/bin/modal volume get paper2-out-v3 / ./modal_out_v3
```

Each run lands at `modal_out_v3/validate_recall/<run_id>/` with:

- `validate_recall_summary.json` — per-cell rows: variant, seed, steps, resolved, num_recalls, total_wall_ms, etc.
- `gpu_mem_trace.jsonl` — per-event GPU memory snapshot (only when `--profile-mem`).
- `v9_engine_stats.jsonl.<pid>` — engine-side counters: captures_executed, restores_executed, rotations, dual_key_inserts, etc.

### Watch a running bake

```bash
~/adaptivecache/.venv/bin/modal app list
~/adaptivecache/.venv/bin/modal app logs <app_id> -f \
  | grep -E "policy-recall.*FIRING|v3-restore-splice|v9-bump-diag|v9-rotate-queued|steps=[0-9]+ resolved|IndexError|Traceback"
```

### Other Paper 2 entrypoints

| Module | Purpose |
|---|---|
| `studies.lifetime_cost.paper2.modal_app.run_smoke_v3_capture` | Phase 3a: capture obs KV → CPU pinned, no recall |
| `studies.lifetime_cost.paper2.modal_app.run_smoke_v3_restore` | Phase 3b/c: capture + splice the blocks back via block_table mutation |
| `studies.lifetime_cost.paper2.modal_app.run_smoke_v4_mask` | Phase 4: attention-mask eviction (refcount pin + paged_kv_indices filter) |
| `studies.lifetime_cost.paper2.modal_app.run_smoke_v4e_recall` | Phase 4e: end-to-end attmask + unmask-on-recall |

All take similar flags. See each file's docstring.

---

## Microbenches

These run **locally** (no Modal) on whatever GPU you have (Blackwell sm_120 box for the original work). They're the proofs the Paper 2 mechanism rests on.

```bash
cd ~/adaptivecache-paper2

# RoPE composition: R(p+Δ) = R(Δ) ∘ R(p) — fp32 cosine sim 1.0
~/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_rope_compose

# vLLM's own RotaryEmbedding kernel composes the same way
~/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_rope_vllm

# In-place K rotation in the paged KV cache layout (bf16, FlashInfer indexing)
~/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_rope_kvcache

# Block-masking processor microbench (no compaction, just marker handling)
~/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_masking

# Memory-growth probe under repeated prompts
~/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_growing
```

---

## Tests

```bash
# Paper 1 unit tests
~/adaptivecache/.venv/bin/pytest tests/

# Paper 2 store + drop tests (need vLLM but no GPU)
~/adaptivecache/.venv/bin/pytest studies/lifetime_cost/paper2/tests/test_memento_store.py
~/adaptivecache/.venv/bin/pytest studies/lifetime_cost/paper2/tests/test_phase7_drop.py
```

---

## Wiki + research

`wiki/` is an LLM-maintained knowledge base — concepts, paper summaries, evolving thesis. Start at `wiki/index.md`. Append-only activity log at `wiki/log.md`. Operations (ingest, query, lint) and naming conventions are in the project-root `CLAUDE.md`.

`raw/` holds the immutable source PDFs the wiki was built from — never edit those.

---

## Common pitfalls

- **Modal image rebuilds when overlay patch changes.** First run after a `.patch` regen takes ~5–10 min. Subsequent runs reuse the cache.
- **`PYTHONHASHSEED=1`** is set unconditionally in `run_validate_recall.py`. The adapter (main proc) and engine (subproc) compute identical block-chain hashes only with a fixed seed; without this, dual-key inserts never match.
- **`PAPER2_NO_PIN=1`** (set via `--no-pin`) disables refcount-pinning of captured blocks. Required for the realistic eviction-pressure path; mechanism still recovers correct KV via the CPU side cache.
- **Stale `memento_store.py.new`** — `apply.sh` copies the `.new` file over the patched memento_store. Regenerate both together: regenerate the patch, then `cp external/memento/.../memento_store.py studies/lifetime_cost/paper2/v3_overlay_patches/memento_store.py.new`.
- **Recall trigger** is `total_tok < recall_low_water_ratio * budget_tokens`, where `total_tok` is rendered prompt size with mementos applied. Set `--recall-low-water` high (≥1.5) to force recalls during validation; ≈0.85 is the realistic config.
