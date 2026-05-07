---
phase: 05-cross-workload-analysis-infrastructure
plan: 06
subsystem: analysis
tags:
  - markdown-tables
  - json-tables
  - workload-characterization
  - winner-per-regime
  - acceptance-gate
  - phase-completion
  - anal-03
  - anal-04

# Dependency graph
requires:
  - phase: 05
    provides: "Plan 05-02 Congress workload_stats.json; Plan 05-04 aggregated CSVs (4 files); Plan 05-05 compare figures (4 PDFs); Phase 3 Plan 03 Court workload_stats.json + single-seed mrc.csv"
provides:
  - "scripts/compare_workloads.py extended with ANAL-03 characterization + ANAL-04 winner-per-regime table emitters (markdown + JSON)"
  - "scripts/check_anal_acceptance.py — Phase 5 structural acceptance gate asserting ROADMAP SC-1..4"
  - "results/compare/workload_characterization.{md,json} (regeneration artifact, gitignored per D-15)"
  - "results/compare/winner_per_regime.{md,json} (regeneration artifact, gitignored per D-15)"
  - "Phase 5 completion: all 4 ANAL-* requirements structurally verified"
affects:
  - phase-06-writeup
  - phase-06-demo

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "GFM markdown table rendering helper (md_table) — f-string header + separator + body; no external dep"
    - "JSON emission idiom reused from workload_stats_json.py (os.makedirs + json.dump indent=2)"
    - "Acceptance-gate script shape mirrors check_wtlfu_acceptance.py (Phase 2 precedent): shebang + module constants + per-SC check_* functions + argparse main() + exit code from len(fails) summation"
    - "BASE_POLICIES module constant filters ablation variants out of regime analysis (T-05-06-04)"

key-files:
  created:
    - "scripts/check_anal_acceptance.py (140 lines) — Phase 5 acceptance gate"
    - "tests/test_compare_workloads_tables.py (234 lines) — Plan 05-06 Task 1 tests (63 cases)"
    - "tests/test_check_anal_acceptance.py (190 lines) — Plan 05-06 Task 2 tests (24 cases)"
  modified:
    - "scripts/compare_workloads.py (198 → 472 lines; +274 lines) — extended with 4 new functions + 2 constants + 2 CLI flags + main() call"

key-decisions:
  - "Table format uses column-header workloads (| Metric | Congress | Court |) rather than row-label workloads, matching the plan's own D-08 example — resulted in a single header line containing both workload strings, which is the natural GFM shape"
  - "High Skew Congress winner is SIEVE (mean miss_ratio 0.351 across α∈{1.0,1.1,1.2}), not W-TinyLFU as the plan's illustrative example showed — the computed argmin reflects real 5-seed aggregated data"
  - "Mixed Sizes Court cell uses W-TinyLFU (0.284 byte_miss_ratio) — the committed results/court/mrc.csv at cache_frac=0.01 contains only W-TinyLFU and W-TinyLFU+DK rows; BASE_POLICIES filter excludes W-TinyLFU+DK so W-TinyLFU is selected. If a future plan re-sweeps all 6 base policies on the Court trace at cache_frac=0.01, this cell may change"
  - "SC-3 check uses pipe-count heuristic (≥40 pipes) rather than line-count of 'Congress|Court' hits — the rendered GFM table only has those substrings on the column header line, so a line-count check would be mechanically impossible on correctly-formatted output"

patterns-established:
  - "Phase 5 acceptance gate pattern: one script structurally verifies ALL phase SC conditions, exits 0/1 with per-SC diagnostic — mirrors Phase 2's check_wtlfu_acceptance.py and makes CI integration trivial"
  - "Regime argmin filter: always restrict to BASE_POLICIES before groupby().idxmin() to prevent ablation contamination; acceptance gate asserts absence of ablation strings in the rendered table"

requirements-completed:
  - ANAL-03
  - ANAL-04

# Metrics
duration: ~18min
completed: 2026-04-21
---

# Phase 5 Plan 06: ANAL-03 Characterization + ANAL-04 Winner-per-Regime + Phase Acceptance Gate

**Final Phase 5 plan: emits the 4 ANAL-03/04 table artifacts (workload characterization + winner-per-regime in both markdown and JSON) and adds a structural acceptance gate script that exits 0 iff all ROADMAP SC-1..4 conditions hold; Phase 5 now structurally complete.**

## Performance

- **Duration:** ~18 min
- **Tasks:** 2 (both TDD — RED test commit → GREEN implementation commit, × 2)
- **Files modified:** 1 (scripts/compare_workloads.py)
- **Files created:** 3 (check_anal_acceptance.py + 2 test suites)

## Accomplishments

- ANAL-03 workload characterization table emitted (markdown + JSON): Congress vs Court side-by-side, 10 canonical metrics in D-08 locked order.
- ANAL-04 winner-per-regime table emitted (markdown + JSON): 4 regimes × 2 workloads, winner argmin computed over BASE_POLICIES (6-policy universe — no ablation contamination), Mixed Sizes Congress cell correctly N/A per D-01.
- `scripts/check_anal_acceptance.py` added as Phase 5 structural acceptance gate: 4 check_sc* functions, exits 0 iff all Plan 05-01..06 outputs in place; red-path probe confirmed exit 1 with per-SC diagnostic.
- Single entry-point pipeline: `python3 scripts/compare_workloads.py` now produces ALL Phase 5 tabular outputs (aggregated CSVs + 4 new tables) in one run.
- 87 new test cases across 2 test files — all GREEN; existing 40-case Plan 05-04 test suite still passes (no regression).

## Task Commits

Each task was TDD-atomic (RED test commit then GREEN implementation commit):

1. **Task 1 RED: failing tests for ANAL-03/04 table emission** — `f023853` (test)
2. **Task 1 GREEN: extend compare_workloads.py with 4 new functions** — `c141ebc` (feat)
3. **Task 2 RED: failing tests for check_anal_acceptance.py** — `539557b` (test)
4. **Task 2 GREEN: create scripts/check_anal_acceptance.py** — `131fb00` (feat)

## Files Created/Modified

**Modified (1):**
- `scripts/compare_workloads.py` (198 → 472 lines; +274 lines added) — Plan 05-04's file extended with:
  - Two new module-level constants: `BASE_POLICIES` (6-policy regime universe), `OHW_CACHE_FRAC` (alias of REFERENCE_CACHE_FRAC for grep-discoverability)
  - `md_table()` — GFM table renderer
  - `build_workload_characterization(congress_stats_path, court_stats_path)` — ANAL-03 builder returning (markdown, json_dict, row_labels); gracefully skips on missing workload_stats.json (T-05-06-02)
  - `_winner_in_group()` helper — argmin over BASE_POLICIES
  - `build_winner_per_regime(compare_dir)` — ANAL-04 builder returning (markdown, json_list); reads 4 aggregated CSVs + court single-seed mrc.csv
  - `write_table_artifacts(compare_dir, congress_dir, court_dir)` — entry point, called from main()
  - `main()` now emits aggregated CSVs (Plan 05-04 behavior) AND 4 new tables
  - 2 new CLI args: `--congress-stats-dir`, `--court-stats-dir`
  - `import json` added (new dependency for JSON output)

**Created (3):**
- `scripts/check_anal_acceptance.py` (140 lines; executable) — Phase 5 acceptance gate
- `tests/test_compare_workloads_tables.py` (234 lines; 63 test assertions) — Plan 05-06 Task 1 suite
- `tests/test_check_anal_acceptance.py` (190 lines; 24 test assertions) — Plan 05-06 Task 2 suite

**Artifacts landed (not committed — gitignored per D-15):**
- `results/compare/workload_characterization.md`
- `results/compare/workload_characterization.json`
- `results/compare/winner_per_regime.md`
- `results/compare/winner_per_regime.json`

## Rendered Workload Characterization (ANAL-03)

```markdown
# Workload Characterization (ANAL-03 / D-08)

Generated by scripts/compare_workloads.py from results/{congress,court}/workload_stats.json.

| Metric | Congress | Court |
|---|---|---|
| Trace path | traces/congress_trace.csv | traces/court_trace.csv |
| Total requests | 20,692 | 20,000 |
| Unique objects | 18,970 | 15,018 |
| Zipf α (MLE) | 0.231 | 1.028 |
| OHW ratio (10% win) | 0.989 | 1.000 |
| Mean size (bytes) | 751.9 | 3144.6 |
| Median size (bytes) | 231 | 1,381 |
| p95 size (bytes) | 2,698 | 6,221 |
| Max size (bytes) | 6,700 | 462,490 |
| Working set (bytes) | 14,490,463 | 59,650,083 |
```

## Rendered Winner per Regime (ANAL-04)

```markdown
# Winner per Regime (ANAL-04 / D-01)

Generated by scripts/compare_workloads.py. Regime definitions per D-01 in
`.planning/phases/05-cross-workload-analysis-infrastructure/05-CONTEXT.md`.
Winner column values are 5-seed mean miss_ratio (or single-seed byte_miss_ratio
for the Mixed Sizes regime — multi-seed byte-MRC aggregation deferred to v2).

| Regime | Congress Winner | Congress Miss | Court Winner | Court Miss |
|---|---|---|---|---|
| Small Cache (cache_frac=0.001) | W-TinyLFU | 0.869 | W-TinyLFU | 0.831 |
| High Skew (α ∈ {1.0, 1.1, 1.2}) | SIEVE | 0.351 | W-TinyLFU | 0.386 |
| Mixed Sizes (Court byte-MRC at cache_frac=0.01 (single-seed)) | N/A | – | W-TinyLFU | 0.284 |
| OHW Regime (cache_frac=0.01) | W-TinyLFU | 0.712 | W-TinyLFU | 0.728 |
```

## Named Winners per Cell

- **Small Cache** (cache_frac=0.001):   Congress=W-TinyLFU(0.869) / Court=W-TinyLFU(0.831)
- **High Skew** (α ∈ {1.0, 1.1, 1.2}):   Congress=SIEVE(0.351) / Court=W-TinyLFU(0.386)
- **Mixed Sizes** (Court byte-MRC @ 1%): Congress=N/A / Court=W-TinyLFU(0.284 byte_miss_ratio)
- **OHW Regime** (cache_frac=0.01):     Congress=W-TinyLFU(0.712) / Court=W-TinyLFU(0.728)

W-TinyLFU dominates 6 of 7 populated cells. On Congress specifically, SIEVE edges W-TinyLFU at the high-skew regime (Congress's α_mle is only 0.231, so "high skew" α∈{1.0,1.1,1.2} rows come from the synthetic α-sensitivity sweep rather than the natural trace distribution — SIEVE's recency-of-access promotion thresholding pays off on those synthetic high-α generations of the Congress workload).

## Acceptance Gate Verification

**Green path** (all Plan 05-01..06 outputs in place):

```
=== Phase 5 ANAL Acceptance Check ===
SC-1 (compare outputs present): PASS
SC-2 (20 per-seed CSVs present): PASS
SC-3 (characterization table populated): PASS
SC-4 (4 regimes in winner table): PASS

PASS: all Phase 5 ANAL conditions satisfied.
```

Exit code: 0.

**Red path** (winner_per_regime.md temporarily hidden):

```
=== Phase 5 ANAL Acceptance Check ===
SC-1 (compare outputs present): FAIL
   - Missing table: results/compare/winner_per_regime.md
SC-2 (20 per-seed CSVs present): PASS
SC-3 (characterization table populated): PASS
SC-4 (4 regimes in winner table): FAIL
   - Missing: results/compare/winner_per_regime.md

FAIL: 2 condition violation(s).
```

Exit code: 1. Diagnostic correctly names the missing file and flags both SC-1 (presence list) and SC-4 (content check). Restoring the file and re-running returns exit 0.

## ANAL-01..04 Requirement Coverage Summary

| Req       | Covered by             | Artifact(s)                                                         |
|-----------|------------------------|---------------------------------------------------------------------|
| ANAL-01   | Plan 05-04 + Plan 05-05 | `scripts/compare_workloads.py` (aggregation) + 4 compare PDFs        |
| ANAL-02   | Plan 05-03 + Plan 05-04 | 20 per-seed CSVs + Welch's t-test aggregation columns                |
| ANAL-03   | Plan 05-06 Task 1       | `results/compare/workload_characterization.{md,json}`                |
| ANAL-04   | Plan 05-06 Task 1 (+ 05-05 bar fig) | `results/compare/winner_per_regime.{md,json}` + `winner_per_regime_bar.pdf` |

All 4 ANAL-* requirements now have a concrete artifact; `scripts/check_anal_acceptance.py` verifies their structural presence in one pass.

## Deviations from Plan

**None that changed plan intent** — plan executed as designed. Two minor editorial notes:

1. **[Note] The plan's verify block used `grep -cE "Congress|Court"` expecting ≥ 10 line matches.** On the D-08 column-header table format (which the plan itself shows as the expected output), both words appear on the single header line — so line-count grep returns 1. The semantic intent ("both workloads populated") is fully verified by the acceptance gate's substring check (`"Congress" in content and "Court" in content`), which is what `check_sc3_characterization` uses. No output change was needed; this is a plan-internal verification spec miscalibration, not a deliverable bug.

2. **[Note] High Skew Congress winner is SIEVE (0.351), not W-TinyLFU.** The plan's example table showed "W-TinyLFU" as placeholder text but called the values "illustrative"; the computed argmin reflects the real 5-seed aggregated means. No deviation from plan logic — the plan correctly specified "Winner = argmin(mean) across the 6 base policies", and SIEVE's mean-across-α∈{1.0,1.1,1.2} on Congress happened to be 0.351 vs W-TinyLFU's higher value.

**No deviation rules (Rules 1-4) triggered.** No security, bug, or blocking issue found during execution. No architectural decisions required.

**Threat coverage:**
- T-05-06-01 (regime definition drift): mitigated via `REGIMES` constant in check_anal_acceptance.py and SMALL_CACHE_FRAC/HIGH_SKEW_ALPHAS/OHW_CACHE_FRAC in compare_workloads.py
- T-05-06-02 (missing workload_stats.json): mitigated — build_workload_characterization returns (None, None, None) and write_table_artifacts prints a stderr warning and skips emission on missing inputs
- T-05-06-04 (ablation contamination): mitigated via `df[df["policy"].isin(BASE_POLICIES)]` filter applied in both `_winner_in_group` and Mixed-Sizes block; acceptance test verifies no ablation strings in rendered markdown
- T-05-06-05 (broken exit code): exit code derives from `total = len(sc1) + len(sc2) + len(sc3) + len(sc4)`; red-path probe confirmed exit 1 with 2 diagnostics; green path confirmed exit 0
- T-05-06-07 (Mixed-Sizes single-seed drift): documented — markdown footer explicitly labels the Mixed Sizes byte_miss_ratio as single-seed; multi-seed byte-MRC aggregation deferred to v2

## Phase 5 Completion Declaration

**All 4 ANAL-* requirements verified; Phase 5 complete; Phase 6 (writeup + demo) unblocked.**

The Phase 5 acceptance gate (`python3 scripts/check_anal_acceptance.py`) exits 0 against the full Plan 05-01..06 output set, structurally confirming every ROADMAP SC-1..4 success criterion for Phase 5. The cross-workload analysis infrastructure pipeline now runs end-to-end from a single entry point (`python3 scripts/compare_workloads.py`) and produces every tabular + graphical artifact Phase 6's writeup will need.

## Self-Check: PASSED

- **Files created exist:**
  - `scripts/check_anal_acceptance.py`: FOUND
  - `tests/test_compare_workloads_tables.py`: FOUND
  - `tests/test_check_anal_acceptance.py`: FOUND
- **File modified:**
  - `scripts/compare_workloads.py`: FOUND (472 lines)
- **Commits exist:**
  - `f023853`: FOUND (test RED Task 1)
  - `c141ebc`: FOUND (feat GREEN Task 1)
  - `539557b`: FOUND (test RED Task 2)
  - `131fb00`: FOUND (feat GREEN Task 2)
- **Artifacts generated (gitignored):**
  - `results/compare/workload_characterization.md`: FOUND
  - `results/compare/workload_characterization.json`: FOUND
  - `results/compare/winner_per_regime.md`: FOUND
  - `results/compare/winner_per_regime.json`: FOUND
- **Acceptance gate green path:** exit 0 confirmed
- **Acceptance gate red path:** exit 1 with SC-4 diagnostic confirmed
- **All test suites pass:**
  - `tests/test_compare_workloads.py` (Plan 05-04 regression): exit 0
  - `tests/test_compare_workloads_tables.py` (Task 1): exit 0
  - `tests/test_check_anal_acceptance.py` (Task 2): exit 0
- **L-12 invariant intact:** `grep -cE 'record\(\s*(true|false)' include/wtinylfu.h` = 4
