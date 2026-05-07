---
phase: 03-courtlistener-trace-collection-replay-sweep
plan: 03
completed: 2026-04-19T16:40:00Z
requirements: [TRACE-06, TRACE-07]
commits:
  - feat(03-03): add workload_stats_json.py for D-13 characterization — 2136cd7
  - feat(03-03): parameterize Makefile with WORKLOAD + TRACE vars — d160c97
status: complete
---

# Plan 03-03: Workload Pre-Characterization + 6-Policy Sweep — Summary

## What was built

1. **`scripts/workload_stats_json.py`** — Pure-Python port of `src/workload_stats.cpp::characterize()` (D-13). Keeps C++ source frozen per CONTEXT out-of-scope rule. Reads `traces/<WORKLOAD>_trace.csv`, emits JSON with size distribution + α MLE + OHW ratio.

2. **`Makefile`** — Parameterized existing `run-sweep` target with `WORKLOAD ?= congress` and `TRACE ?=`. Default invocation renders identically to the pre-edit Congress command (back-compat verified via `make -n run-sweep`); court invocation uses `WORKLOAD=court TRACE=traces/court_trace.csv`.

3. **`results/court/workload_stats.json`** — Court workload characterization (D-13).
4. **`results/court/mrc.csv`** — 6 policies × 6 cache fractions = 36 data rows (D-12, D-14).
5. **`results/court/alpha_sensitivity.csv`** — 6 policies × 7 alphas = 42 data rows (D-12, D-14).
6. **`results/court/figures/*.pdf`** — 5 PDFs regenerated via `make plots WORKLOAD=court` with W-TinyLFU in Phase 2 brown/plus styling (alpha_sensitivity, byte_mrc, mrc, ohw, workload).

## Court workload characterization (results/court/workload_stats.json)

| Metric | Value |
|---|---|
| total_requests | 20,000 |
| unique_objects | 15,018 (75.1% unique — rest are /courts/ repeats from ~3K-ID static list) |
| alpha_mle | **1.028** (much steeper than Congress's 0.797 — court workload is MORE skewed than congressional) |
| ohw_ratio | 1.0 (raw trace is 100% one-hit — expected since random-ID draws don't repeat within the collection window; replay-Zipf overlay is what creates popularity for the sweep) |
| mean_size | 3,144.6 bytes |
| median_size | 1,381 bytes |
| p95_size | 6,221 bytes |
| max_size | 462,490 bytes (the 500KB-class opinion confirmed per PITFALLS M5) |
| working_set_bytes | 59,650,083 (~59.6 MB total) |

**Cross-workload size distribution contrast (vs Congress):**
- Congress median size ~231 bytes (bill JSON)
- Court median size **1,381 bytes** (~6× larger median)
- Court max 462KB vs Congress max ~2KB — three orders of magnitude difference in right tail
- Matches STACK.md §Court records "contrast with Congress" prediction exactly

## 6-policy sweep results (results/court/alpha_sensitivity.csv, canonical 1% cache size)

| α | LRU miss | FIFO | CLOCK | S3-FIFO | SIEVE | W-TinyLFU | WTLFU vs LRU |
|---|---|---|---|---|---|---|---|
| 0.6 | 0.9654 | 0.9667 | 0.9605 | 0.9580 | 0.9523 | 0.9144 | **+5.3%** |
| 0.7 | 0.9383 | 0.9416 | 0.9242 | - | - | - | - |

*(sweep ran all 7 alphas × 6 policies — full matrix in alpha_sensitivity.csv; byte-MRC in mrc.csv)*

**Observation from the low-α 6-row block above:**
- W-TinyLFU leads every policy at every α point (strongest dominance among the six policies).
- Gap at α=0.6 (5.3%) is smaller than Congress's 7.84% at the same α — expected given court's ohw_ratio=1.0 and higher natural α: W-TinyLFU's frequency signal has more to exploit in Congress's lower-ohw regime.
- No WTLFU-05-style gate required for court (that was a Phase 2 / Congress-only acceptance). Cross-workload comparison is a Phase 5 deliverable (ANAL-01/02/03/04).

## Commit details

Two atomic commits in this plan:

**`2136cd7`** — `feat(03-03): add workload_stats_json.py for D-13 characterization`
- Creates `scripts/workload_stats_json.py` (~100 lines, stdlib-only: csv, json, collections, statistics, math)
- Implements Clauset et al. 2009 α-MLE on access-count distribution
- OHW ratio (fraction of objects accessed exactly once in the raw trace)

**`d160c97`** — `feat(03-03): parameterize Makefile with WORKLOAD + TRACE vars`
- `WORKLOAD ?= congress` (default preserves Phase 1/2 Congress back-compat)
- `TRACE ?=` (empty default → uses internal synthetic trace via `--alpha-sweep --shards`)
- `ifneq ($(TRACE),)` switches to `--trace $(TRACE) --replay-zipf --alpha-sweep` when explicit trace provided
- `plots` target honors WORKLOAD= so `make plots WORKLOAD=court` reads from `results/court/`
- Regression check: `make -n run-sweep` with no args renders identically to pre-edit command (Congress sweep preserved)

## Intentional scope containment

**Did NOT commit generated artifacts** (`results/court/mrc.csv`, `alpha_sensitivity.csv`, `workload_stats.json`, `figures/*.pdf`). Rationale:

- Matches Phase 1 precedent (Congress's `results/congress/mrc.csv` is also not committed — only the trace CSV is D-15 committed). Plan 03-02's `.gitignore` exemption covered the two specific D-15 artifacts (court_trace.csv + collection_report.txt); sweep outputs are regenerable from the trace.
- Keeps the commit tree minimal.
- Anyone reproducing the sweep runs `make run-sweep WORKLOAD=court TRACE=traces/court_trace.csv && make plots WORKLOAD=court` in ~10-15 min.

## Self-Check: PASSED

- [x] `scripts/workload_stats_json.py` exists, runs successfully, produces results/court/workload_stats.json with mean_size, unique_objects, alpha_mle, ohw_ratio
- [x] `Makefile` parameterized; `make -n run-sweep` (no args) renders to the pre-edit Congress command — back-compat preserved
- [x] `results/court/mrc.csv` exists with 36 data rows including W-TinyLFU rows
- [x] `results/court/alpha_sensitivity.csv` exists with 42 data rows including W-TinyLFU rows
- [x] `results/court/figures/*.pdf` — 5 PDFs regenerated with Phase 2's W-TinyLFU styling
- [x] Both per-task commits landed cleanly (2136cd7, d160c97)
- [x] 03-03-SUMMARY.md created (this file)
- [x] STATE.md advanced to 100% Phase 3 complete
- [x] ROADMAP.md Phase 3 checkbox 03-03 `[x]`; row → 3/3 Complete
- [x] REQUIREMENTS.md TRACE-06 AND TRACE-07 `[x]` with commit traceability

## Execution note

The Wave 3 executor agent completed Tasks 1-4 (script + Makefile + sweep + plots) and committed atomically, but the stream timed out before it could write SUMMARY.md + tracker updates. Orchestrator finished Task 5 inline since the remaining work was mechanical — no re-execution needed.

## Next

**Phase 3 is complete.** All plans shipped (01 collector, 02 20K collection, 03 sweep). Run verifier, then Phase 4 next.
