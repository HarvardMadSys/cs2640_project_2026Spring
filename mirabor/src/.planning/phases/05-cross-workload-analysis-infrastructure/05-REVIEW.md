---
status: issues_found
phase: 05-cross-workload-analysis-infrastructure
reviewed: 2026-04-20
depth: standard
files_reviewed: 8
counts:
  critical: 0
  warning: 2
  info: 10
  total: 12
---

# Phase 5: Code Review Report

**Reviewed:** 2026-04-20
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Files reviewed

- `scripts/check_anal_acceptance.py`
- `scripts/compare_workloads.py`
- `scripts/plot_results.py`
- `scripts/run_multiseed_sweep.py`
- `src/main.cpp`
- `tests/test_check_anal_acceptance.py`
- `tests/test_compare_workloads.py`
- `tests/test_compare_workloads_tables.py`

## Summary

The Phase 5 cross-workload analysis infrastructure is generally clean, well-documented, and follows project conventions (grep-discoverable module constants, list-form subprocess, atomic `os.rename`, float-tolerance comparisons). The C++ `--seed` wiring in `src/main.cpp` is a correct surgical edit and deliberately preserves the literal `42` at the `--emit-trace` path per D-15.

Two latent-coupling issues are worth fixing before Phase 5 ships: the `plot_winner_per_regime_bar` figure can diverge from `winner_per_regime.md/json` because the figure code does not filter to `BASE_POLICIES` while the table code does. One minor regime-dispatch code-safety issue, plus a small handful of Info-level suggestions.

No Critical issues found. No security issues found (scripts use list-form subprocess, no `eval`, no `shell=True`, no hardcoded secrets, no unsafe deserialization).

## Warnings

### WR-01: `plot_winner_per_regime_bar` does not filter to BASE_POLICIES — can silently diverge from the winner_per_regime table

**File:** `scripts/plot_results.py:796-845`

**Issue:** `compare_workloads.py::_winner_in_group` (line 224: `sub = df[df["policy"].isin(BASE_POLICIES)]`) correctly restricts the regime winner calculation to the 6 base policies per D-01, explicitly excluding ablation variants. The plotting counterpart `plot_winner_per_regime_bar::winner_on` (plot_results.py:796-804) performs the same argmin logic without the BASE_POLICIES filter. The Mixed Sizes branch at plot_results.py:828-842 also does not filter.

Today this does not produce wrong figures because the multi-seed sweep in `run_multiseed_sweep.py` invokes `./cache_sim --alpha-sweep` with default policies (6 base policies, no ablations), so the aggregated CSVs only contain base policies. But this is a latent coupling bug: the figure and the table would silently diverge if the sweep is ever extended to include ablation variants. For Mixed Sizes specifically, `results/court/mrc.csv` is already produced by a full-policy sweep that can include ablations today, so this divergence is reachable right now via Mixed Sizes if the Court run was built with ablation CSVs merged.

**Fix:** Mirror the `BASE_POLICIES` constant in `plot_results.py`, then filter at the top of `winner_on` (and in the Mixed Sizes single-seed branch).

### WR-02: Regime dispatch falls through on unknown key — `cw`/`cv`/`kw`/`kv` become undefined

**File:** `scripts/plot_results.py:819-849`

**Issue:** The regime dispatch (plot_results.py:820-845) is an `if/elif/elif/elif` chain with no `else` branch. The adjacent `regimes` tuple (line 807-812) defines the four keys, so today the chain is total. But if a fifth regime is added to the `regimes` tuple without a corresponding `elif` branch, the loop on line 819 will append uninitialized `cw`/`cv`/`kw`/`kv` from the previous iteration (or raise `NameError` on the first iteration). Silent wrong-data propagation on stale values is the worse failure mode.

**Fix:** Add `else: raise ValueError(f"Unknown regime key: {key!r}")` after the last `elif`.

## Info

### IN-01: `--seeds` CLI accepts duplicates silently

**File:** `scripts/run_multiseed_sweep.py:133-140`

`--seeds 42,42,7` would run seed 42 twice (overwriting `mrc_seed42.csv`) and silently skew the aggregation (observable via `n=4` downstream but root cause is upstream). Fix: dedupe while preserving order, warn on duplicates.

### IN-02: `significant=True` semantics for LRU rows is counter-intuitive

**File:** `scripts/compare_workloads.py:101-105`

LRU rows get `p_value=NaN, significant=True`. This is a sentinel (LRU is the reference for the t-test, not self-compared), but downstream filters on `significant == True` will include LRU rows. Document or use `significant=NaN` for the reference row.

### IN-03: `pipe_count < 40` heuristic is brittle

**File:** `scripts/check_anal_acceptance.py:86-90`

SC-3 uses `content.count("|") < 40` to verify a 10-row table rendered. Schema extensions or contractions silently false-pass/false-fail. Fix: count data rows by parsing table lines.

### IN-04: `open(path)` without explicit encoding

**Files:** `scripts/check_anal_acceptance.py:80,101`, `scripts/compare_workloads.py:167,169,372,374,387,389`, test files

Python's `open(path)` uses the platform's locale encoding — OK on macOS/Linux but cp1252 on Windows, which would mangle the `α` glyph in the regime table. Fix: add `encoding="utf-8"` to all markdown/JSON `open()` calls.

### IN-05: `.values` vs `.to_numpy()` on Series

**File:** `scripts/compare_workloads.py:89,95`

Legacy `.values` accessor still works but `.to_numpy()` is forward-compatible.

### IN-06: Inconsistent float-comparison idiom between modules

**Files:** `scripts/compare_workloads.py` (tolerance), `scripts/plot_results.py` (mixed tolerance + `round(4) == 0.01`)

Four different float-comparison idioms across the two modules. All work for the current value set, but inconsistent reads make future diff review harder. Low priority.

### IN-07: `test_red_path_missing_regime_md` risks file loss if restore fails

**File:** `tests/test_check_anal_acceptance.py:129-142`

Moves a file to `/tmp` and restores via `finally`. Since the file is regenerable per D-15, severity is low; document or use a context-manager-style backup.

### IN-08: `test_wtlfu_significant_high_alpha_congress` is a data-dependent assertion

**File:** `tests/test_compare_workloads.py:147-161`

This is a locked Phase 2 finding per the plan, coupling the test to simulation data. Worth a comment noting it's a regression check, not a unit test of the aggregation code.

### IN-09: Multi-alpha `round(2)` coercion in High Skew filter

**File:** `scripts/compare_workloads.py:273-278`, `scripts/plot_results.py:824-827`

Works correctly for the current 1-decimal alpha grid; silently misses intended rows if the grid ever moves to 2-decimal alphas. Low risk per D-01 grid stability.

### IN-10: `run_multiseed_sweep.py` may leak scratch dirs on partial runs

**File:** `scripts/run_multiseed_sweep.py:109-113`

`shutil.rmtree(scratch)` is try/except-non-fatal. Mid-rename interruption (2 file-op window) could leave stale data that future runs inherit. Fix: clear scratch at the *start* of each cell rather than the end.

## Files With No Issues

- `src/main.cpp` — the `--seed` flag wiring (lines 116, 140-141, 204, 215, 334, 339) is correct and surgical; the `--emit-trace` path's literal `42` at line 182 is deliberately preserved per D-15. Upstream/downstream consumers (`prepare_objects`, `replay_zipf`, `generate_replay_trace`, `generate_zipf_trace`) all receive the threaded `seed` value.

---

_Reviewed: 2026-04-20_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
