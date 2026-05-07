---
phase: 01-enabling-refactors-courtlistener-pilot
plan: 03
subsystem: simulator-throughput
tags: [refactor, csv-schema, instrumentation, steady_clock]
dependency-graph:
  requires:
    - 01-01  # hash_util extraction + self-test gate (preserved)
    - 01-02  # replay_zipf split + prepare_objects hoist (preserved)
  provides:
    - "accesses_per_sec column in mrc.csv, alpha_sensitivity.csv, shards_mrc.csv"
    - "_has_throughput(df) helper in plot_results.py for downstream Pareto plots"
  affects:
    - 01-04  # plot_results.py --workload flag (next wave) — schema is now stable
    - Phase-05  # ANAL-01 miss-ratio-vs-throughput Pareto plots consume this column
tech-stack:
  added: []
  patterns:
    - "std::chrono::steady_clock timing wrap around run_simulation (mirrors SHARDS block src/main.cpp:297-300)"
    - "CSV-as-contract: C++ writer header + snake_case column + pd.read_csv tolerant reader"
key-files:
  created: []
  modified:
    - src/main.cpp         # 3 timing sites + 3 CSV header updates
    - scripts/plot_results.py  # CSV schema doc block + _has_throughput helper
decisions:
  - "Timing lives in main.cpp locals; CacheStats in include/cache.h is NOT touched (D-02)"
  - "SHARDS accesses_per_sec uses trace.size() / elapsed (not sampled count) for cross-CSV comparability (D-03)"
  - "Same accesses_per_sec value is duplicated on every MRC row within a sampling rate (D-03)"
  - "plot_results.py doc block + helper added preemptively; existing plotters unchanged because pd.read_csv already tolerates extra columns"
  - "--workload flag deferred to Plan 01-04 per D-05 (out of 01-03 scope)"
metrics:
  duration: "~3 minutes"
  tasks_completed: 3
  files_modified: 2
  completed: 2026-04-19
requirements:
  - REFACTOR-03
---

# Phase 1 Plan 3: Add accesses_per_sec to All Simulator CSVs Summary

**One-liner:** Wall-clock throughput measurement via `std::chrono::steady_clock` around `run_simulation` / `shards.process`, emitted as a new `accesses_per_sec` column in `mrc.csv`, `alpha_sensitivity.csv`, and `shards_mrc.csv`, with `scripts/plot_results.py` documenting the new schema and tolerating legacy CSVs.

## What Was Built

### src/main.cpp — three timing sites, three CSV header updates

1. **MRC loop** (lines 175, 197-206): `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`. Timing wraps `run_simulation(trace, *p)`.
2. **Alpha-sweep loop** (lines 219, 249-258): `alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`. Timing wraps `run_simulation(sweep_trace, *p)`. Post-01-02 `prepared_objects` hoist preserved untouched.
3. **SHARDS loop** (lines 293, 297-309): `sampling_rate,cache_size_objects,miss_ratio,accesses_per_sec`. Reuses the **existing** `steady_clock` timing at the same site (src/main.cpp:297-300) — no new timing, just forwards `elapsed` through to the CSV row as a per-rate throughput value. Same `accesses_per_sec` is duplicated on every MRC row for a given sampling rate (D-03 "same value per rate" invariant).

All three sites use the **same idiom** (matches the pattern locked in `01-PATTERNS.md` under "std::chrono::steady_clock timing block"):
```cpp
auto t_start = std::chrono::steady_clock::now();
run_simulation(trace, *p);
auto t_end = std::chrono::steady_clock::now();
double elapsed = std::chrono::duration<double>(t_end - t_start).count();
double accesses_per_sec = elapsed > 0 ? (double)trace.size() / elapsed : 0.0;
```

**Exact new CSV headers:**

| File                      | Header                                                                        |
| ------------------------- | ----------------------------------------------------------------------------- |
| `mrc.csv`                 | `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec` |
| `alpha_sensitivity.csv`   | `alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`                    |
| `shards_mrc.csv`          | `sampling_rate,cache_size_objects,miss_ratio,accesses_per_sec`                |

**Legacy CSV schemas unchanged** (out of REFACTOR-03 scope):
- `exact_mrc.csv`: `cache_size_objects,miss_ratio` (verified via `--shards-exact` run)
- `shards_error.csv`: `sampling_rate,mae,max_abs_error,num_points` (verified via `--shards-exact` run)
- `one_hit_wonder.csv`: `window_frac,ohw_ratio` (not touched)

### scripts/plot_results.py — schema doc + tolerance helper

- **CSV schema doc block** added between module docstring and first import, enumerating all simulator CSV layouts including the new `accesses_per_sec` column, and noting "Readers below tolerate older CSVs that lack accesses_per_sec (REFACTOR-03)".
- **`_has_throughput(df)` helper** added at module scope (before `plot_mrc`) for future Pareto plots that need to guard on column presence.
- **No existing plotting function bodies changed** — pandas' `pd.read_csv` already tolerates extra columns gracefully. Verified by running the full suite against both new-schema and hand-crafted legacy-schema CSVs.

## Sample accesses_per_sec Values Observed (20K-request smoke run)

| CSV                         | Sample row (first data row)                                            | Throughput (order of magnitude) |
| --------------------------- | ---------------------------------------------------------------------- | ------------------------------- |
| `mrc.csv`                   | `0.001,26559,LRU,0.9775,0.991145,1.12716e+07`                           | ~11M accesses/sec (LRU, tiny cache) |
| `alpha_sensitivity.csv`     | `0.6,LRU,0.96055,0.971022,1.32454e+07`                                   | ~13M accesses/sec              |
| `shards_mrc.csv`            | `0.001,19,0.571429,1.45587e+08`                                         | ~145M accesses/sec (0.1% SHARDS, sampled-path fast) |

**Per-sampling-rate throughput values** (from the SHARDS 30K smoke run — D-03 invariant check):
- `rate=0.001 → 1.36e+08 acc/sec`
- `rate=0.01  → 1.12e+08 acc/sec`
- `rate=0.1   → 2.09e+07 acc/sec`

Each rate has a single distinct value across all its MRC rows (verified with `sort -u`) — D-03 "same value on every row for that rate" invariant holds.

All observed values are **positive finite doubles** per the plan's truth contract.

## Confirmations

- **`include/cache.h` was NOT modified** — `git diff --quiet include/cache.h` exits 0. `CacheStats` is still the unchanged POD aggregate; timing lives entirely in `main.cpp` locals per D-02.
- **`plot_results.py` runs successfully on both new and legacy CSVs**:
  - New CSVs (`/tmp/tput_check/`) → exit 0, no traceback, all 5 PDFs generated (`mrc.pdf`, `byte_mrc.pdf`, `alpha_sensitivity.pdf`, `shards_mrc.pdf`, `ohw.pdf`).
  - Synthetic legacy CSVs (`/tmp/legacy_check/`, no `accesses_per_sec` column) → exit 0, no traceback, all 5 PDFs generated. (Note: `workload.pdf` is correctly skipped with "congress_trace.csv not found" — workload plot depends on trace files not present in this worktree, unrelated to the schema change.)
- **01-01 artifact preserved**: `hash_util_self_test()` gate still present in `main.cpp` (grep confirmed).
- **01-02 artifact preserved**: `prepared_objects = prepare_objects(raw_trace)` hoist still present in alpha-sweep block (grep confirmed).
- **Build clean**: `make clean && make` exits 0 with no `-Wall -Wextra` warnings.
- **`grep -c 'double accesses_per_sec = elapsed > 0' src/main.cpp` = 3** (MRC + alpha-sweep + SHARDS sites).

## Deviations from Plan

None — plan executed exactly as written. No Rule 1/2/3 auto-fixes required, no Rule 4 architectural decisions surfaced.

## Environment Notes (non-deviation)

- Python plotter testing required the project's `.venv` (`/Users/mirayu/civicache/.venv`) with `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` prepended — this is the documented macOS Homebrew libexpat workaround from `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-CONTEXT.md` specifics block. System `python3` lacks `matplotlib`; this is expected and does not require a fix (CONTEXT explicitly marks the libexpat issue as out-of-scope for Phase 1).
- `traces/congress_trace.csv` is not present in this worktree, so `plot_workload` is skipped with its expected "not found" message in both new-CSV and legacy-CSV runs. Unrelated to REFACTOR-03 scope.

## Commits

| # | Task                                                              | Commit   | Files                                                          |
| - | ----------------------------------------------------------------- | -------- | -------------------------------------------------------------- |
| 1 | Add accesses_per_sec to mrc.csv and alpha_sensitivity.csv         | `b7e0ff1` | src/main.cpp (MRC header + alpha header + 2 steady_clock wraps) |
| 2 | Forward SHARDS timing into shards_mrc.csv as accesses_per_sec     | `58289c1` | src/main.cpp (SHARDS header + compute accesses_per_sec + row write) |
| 3 | Document CSV schemas + add _has_throughput helper                 | `7eb7b5e` | scripts/plot_results.py (doc block + helper)                    |

## Threat Flags

None. REFACTOR-03 introduces no new trust boundaries — purely internal instrumentation + CSV schema extension. The D-02 divide-by-zero mitigation (`elapsed > 0 ? ... : 0.0`) is applied at all three sites (grep-anchored = 3). Schema drift threat (T-01-07) is mitigated by the explicit schema doc block in `plot_results.py` plus acceptance-criteria greps that anchor the exact header strings.

## Self-Check: PASSED

- `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-03-SUMMARY.md`: verified below.
- `src/main.cpp`: FOUND (16+4 insertions across 2 commits).
- `scripts/plot_results.py`: FOUND (19 insertions).
- Commit `b7e0ff1`: FOUND (`feat(01-03): add accesses_per_sec to mrc.csv and alpha_sensitivity.csv`).
- Commit `58289c1`: FOUND (`feat(01-03): add accesses_per_sec to shards_mrc.csv (D-03)`).
- Commit `7eb7b5e`: FOUND (`docs(01-03): document CSV schemas + add _has_throughput helper`).
- All 5 expected PDFs produced in `/tmp/tput_check/figures/`: mrc.pdf, byte_mrc.pdf, alpha_sensitivity.pdf, shards_mrc.pdf, ohw.pdf.
