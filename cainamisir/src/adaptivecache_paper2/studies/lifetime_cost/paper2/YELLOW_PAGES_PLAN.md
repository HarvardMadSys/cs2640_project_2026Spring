# Yellow Pages benchmark plan — long-doc multi-turn lookup

A synthetic benchmark designed to let compaction + recall actually win.
SWE-bench Lite at 25 steps doesn't overflow context, so `none` always
wins on wall time. Yellow Pages forces the agent to drop and re-fetch
content the way the v4 thesis predicts.

## Workload

* 10,000 synthetic people, each ~50 tokens:
  `id | name | phone | email | employer | role | city | bio_one_liner`
* Total directory: ~500K tokens — safely above any sane `max_model_len`.
* 200 lookup queries per session, mixed:
  * 80% single-name lookup: "What is <name>'s phone number?"
  * 15% employer-of-X: "Who works at Acme?"
  * 5% ambiguous (multiple matches): "Find people in Boston named Smith"
* Locality bias: 70% of queries drawn from a 50-person working set; 30%
  uniform over the 10K. Reproduces realistic re-query patterns and gives
  recall something to do.
* Ground truth: exact-match on the looked-up field. Resolve rate is
  unambiguous.

## Tool surface

A single tool: `lookup(query: str) -> str`. The runner partitions the
directory into ~64 chunks of ~150 records each (~7.5K tokens per chunk).
On a query, the tool returns the chunk(s) containing matches as the
observation. This is what gets compacted between turns.

This shape mirrors SWE-bench tool-call observations (file reads) but with
controllable size and recall density.

## Why this exposes Memento-style compaction

* Each `lookup` returns ~7.5K tokens. After 10 turns the working context
  is ~75K — overflows our 65K `max_model_len`.
* Variant `none`: hits context overflow → must drop the directory entirely
  → re-loads on every query → **decode of a fresh 7.5K obs every turn**.
* Variant `lru / lru-append / lru-inplace + Phase 8`: compacts old obs to
  placeholders, recalls on demand. Compacted prompts stay short → no
  overflow. Re-query of an already-seen chunk should hit dual-keyed cache.

Predicted ranking (Phase 8 hypothesis):
1. `lru-inplace+attmask` (Phase 4e) — best wall, ~5GB GPU memory growth.
2. `lru-inplace + dual-key` (Phase 8) — close to (1), no GPU growth, slight
   suffix-RoPE drift.
3. `none` (full context) — slowest because every recall is a re-prefill of
   a fresh chunk (chain hash differs).
4. `off` (no compaction, no overflow handling) — crashes on overflow at turn ~10.

If (2) and (3) end up tied or (3) wins, we've learned that vLLM's prefix
cache already handles repeat-chunk re-fetch via input dedup before our
intervention triggers. Useful negative result.

## Files to add

* `studies/lifetime_cost/paper2/benchmarks/yellow_pages_gen.py`
  Deterministic generator. CLI: `--n-people 10000 --n-queries 200 --seed 0
  --out yellow_pages_v1.jsonl`. Writes `(directory, queries, ground_truth)`.
* `studies/lifetime_cost/paper2/yellow_pages_runner.py`
  Drop-in twin of `validate_recall.py`. Agent loop:
  1. Seed system prompt with task instructions.
  2. For each query: model calls `lookup(query)`, tool returns matching
     chunk(s), model emits answer. Score answer.
  3. Trajectory length = number of queries × 2 turns.
* `studies/lifetime_cost/paper2/modal_app/run_yellow_pages.py`
  Modal entrypoint mirroring `run_validate_recall.py`.

## Knobs to expose for ablation

* `--n-queries` 50 / 200 / 500 — sweep trajectory length.
* `--locality 0.7` — fraction of queries from working set; controls recall
  density. 0.0 = uniform (no recall payoff); 1.0 = same 50 people (high
  payoff).
* `--chunk-size 150` — records per chunk; trades obs size vs # chunks.
* `--working-set-size 50` — knob to vary recall locality independently.

## Validation milestones

1. **Smoke** (1 task, 30 queries, single seed): verify `lookup` works,
   `none` overflows on schedule, `lru-append` survives, resolve rate >0.
2. **Locality sweep** (3 seeds × 5 locality levels × 4 variants): plot
   wall vs locality. Expect compaction wins to widen as locality rises.
3. **Trajectory length sweep** (50 / 200 / 500 queries × 4 variants):
   demonstrate compaction's cost-per-step stays flat while `none` blows up.

## Estimated effort

* Generator + runner: 4-6 hours.
* Modal wiring: 1-2 hours.
* First smoke + debug: 2-3 hours.
* Ablation sweep: ~3h compute, ~1h analysis.
* **Total: ~1.5 days end to end.**

## Cross-references

* See `MODAL_RUN_2026-05-05_phase4e_recall_works.md` for the Phase 4e
  win on SWE-bench Lite that we want to reproduce on a non-rigged
  workload.
* See `PHASE_8_PLAN.md` for the dual-key cache mechanism this benchmark
  is designed to stress.
* See `compaction_taubench_dead.md` (memory) for why τ-bench airline is
  dead — same diagnosis as SWE-bench Lite, different workload.
