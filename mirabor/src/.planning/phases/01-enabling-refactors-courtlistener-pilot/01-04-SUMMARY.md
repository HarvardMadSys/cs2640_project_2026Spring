---
phase: 01-enabling-refactors-courtlistener-pilot
plan: 04
subsystem: infra
tags: [makefile, python, argparse, results-layout, workload-routing]

# Dependency graph
requires:
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: "Plan 03 added a CSV schemas comment block and _has_throughput helper in scripts/plot_results.py; both preserved verbatim by this plan."
provides:
  - "Per-workload results tree: results/{congress,court,shards_large,compare}/ with Congress CSVs migrated into congress/."
  - "--workload flag on scripts/plot_results.py (default congress) that derives results/{workload}/ as the CSV source and figures/ destination."
  - "Workload-parameterized trace filename in plot_workload (traces/{workload}_trace.csv) so Phase 3's court trace plots without edits."
  - "Makefile run and run-sweep targets pass --output-dir results/congress so default runs land in the workload subdir (D-06)."
affects: [03-courtlistener-production-collection, 04-wtinylfu, 04-shards-1m, 05-cross-workload-comparison]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-workload results subdirs chosen over per-workload tree roots — simpler for scripts/compare_workloads.py to join in Phase 5."
    - "Caller-chooses-output-dir pattern: simulator stays workload-agnostic, Makefile/scripts pass --output-dir."

key-files:
  created:
    - .planning/phases/01-enabling-refactors-courtlistener-pilot/01-04-SUMMARY.md
  modified:
    - scripts/plot_results.py
    - Makefile
  migrated:
    - results/mrc.csv -> results/congress/mrc.csv (plain mv; file is gitignored)
    - results/alpha_sensitivity.csv -> results/congress/alpha_sensitivity.csv (plain mv)
    - results/one_hit_wonder.csv -> results/congress/one_hit_wonder.csv (plain mv)
    - results/shards_mrc.csv -> results/congress/shards_mrc.csv (plain mv)
    - results/figures/ -> results/congress/figures/ (plain mv)

key-decisions:
  - "Empty workload stub dirs (court/, shards_large/, compare/) created without .gitkeep. results/ is gitignored and adding an exception was explicitly out of scope; Phase 3/4/5 writers will mkdir -p their own target on first write."
  - "CSV migration used plain mv (not git mv) because every file was untracked — results/ is gitignored at the top level."
  - "results/validation/ was preserved at results/validation/ (predates the workload split, not REFACTOR-04 scope per plan action step 4)."
  - "results/court/pilot_report.txt preserved in place — it was force-added in plan 01-06 past the gitignore and is not a Congress artifact."
  - "make plots target text updated but its python3 invocation is unchanged (pre-existing libexpat env-var workaround still applies; fixing that is out of scope per CONTEXT.md specifics)."

patterns-established:
  - "args.results_dir or os.path.join('results', args.workload) — the derivation rule for workload-scoped paths. Future plotters (compare_workloads.py in Phase 5) can follow or extend."
  - "plot_workload(traces_dir, figures_dir, workload='congress') — signature convention for workload-aware helpers."

requirements-completed: [REFACTOR-04]

# Metrics
duration: ~6min
completed: 2026-04-18
---

# Phase 1 Plan 04: Results Layout + --workload Flag Summary

**Reorganized results/ into per-workload subdirs (congress/court/shards_large/compare) and taught scripts/plot_results.py a --workload flag (default congress) with workload-parameterized trace filename.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-04-18 (fbaacc0 base)
- **Completed:** 2026-04-19T01:40Z
- **Tasks:** 3
- **Files modified:** 2 (scripts/plot_results.py, Makefile)
- **Files migrated:** 4 CSVs + 1 figures/ dir (working-tree only — all gitignored)

## Accomplishments

- `results/congress/` holds the previously top-level Congress CSVs + figures.
- `results/court/`, `results/shards_large/`, `results/compare/` exist as empty stubs ready for downstream phases.
- `scripts/plot_results.py --workload NAME` (default `congress`) writes PDFs under `results/{workload}/figures/` and reads `traces/{workload}_trace.csv`.
- `--results-dir` still overrides `--workload` (back-compat verified via smoke test).
- `make run` and `make run-sweep` pass `--output-dir results/congress` so default runs land in the workload subdir (verified: "Done. Results written to results/congress/").
- Plan-03's schemas comment block and `_has_throughput` helper preserved verbatim.

## Task Commits

Each task was committed atomically. Task 1 is a working-tree-only migration (no git-visible change) because all migrated files are under the gitignored `results/` tree — this is documented here and in the plan action step 5.

1. **Task 1: Migrate results into results/congress/ + stub dirs** — no commit (all files gitignored; purely local working-tree reorganization). Verified by filesystem checks.
2. **Task 2: --workload flag in scripts/plot_results.py** — `a424263` (feat)
3. **Task 3: Makefile run/run-sweep target results/congress** — `c96d83a` (feat)

**Plan metadata commit:** (this SUMMARY.md commit, separate from per-task commits)

## Files Created/Modified

- `scripts/plot_results.py` — Added `--workload` argparse flag (default `congress`); changed `--results-dir` default to `None` with derivation `args.results_dir or os.path.join("results", args.workload)`; parameterized `plot_workload` signature `(traces_dir, figures_dir, workload="congress")` and trace filename `f"{workload}_trace.csv"`. Plan-03's `_has_throughput` helper and CSV schemas comment block preserved.
- `Makefile` — `run` and `run-sweep` targets now `mkdir -p results/congress` and pass `--output-dir results/congress`; comments updated to reflect new defaults. Tab indentation preserved.
- `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-04-SUMMARY.md` — this file.

## Migration Commands Executed

Task 1 ran these commands from repo root (see plan 01-04 action for exact sequence):

```bash
mkdir -p results/congress results/court results/shards_large results/compare
# All four CSVs were UNTRACKED (results/ is gitignored), so the loop
# resolved to plain mv for every file:
mv results/mrc.csv                results/congress/mrc.csv
mv results/alpha_sensitivity.csv  results/congress/alpha_sensitivity.csv
mv results/one_hit_wonder.csv     results/congress/one_hit_wonder.csv
mv results/shards_mrc.csv         results/congress/shards_mrc.csv
# results/figures/ was also untracked, so:
mv results/figures results/congress/figures
```

`results/validation/` was NOT moved (predates the workload split, out of scope).
`results/court/pilot_report.txt` was NOT touched (force-added in plan 01-06 past the gitignore; it's a court artifact, already in the right subdir).

## Decisions Made

- **No `.gitkeep` files** in the empty workload stubs. `results/` is gitignored and the plan explicitly forbids adding an exception. Downstream Phase 3/4/5 writers will `mkdir -p` their own target dir before first write (pattern: the new `Makefile` `run` target does exactly this with `mkdir -p results/congress`).
- **`--results-dir` default changed from `"results"` to `None`** so the derivation rule `args.results_dir or os.path.join("results", args.workload)` picks up `--workload` when callers don't pass an explicit `--results-dir`. Passing `--results-dir results/congress` still works (verified by smoke test), so pre-existing tooling keeps working.
- **`plot_workload` signature was extended, not rewritten.** Added a trailing `workload="congress"` kwarg so any caller passing only `(traces_dir, figures_dir)` still works; `main()` now passes `args.workload` as the third arg.

## Deviations from Plan

None - plan executed exactly as written.

Plan action step 5 explicitly warns: "Do NOT add .gitkeep files to the empty stubs." This was honored. Task 1 produced no git-visible changes (all migrated files are under the gitignored `results/` subtree), which is the plan's expected outcome per the same action step.

## Issues Encountered

**Pre-existing env issue (out of scope):** `make plots` invokes `python3` which resolves to `/opt/homebrew/bin/python3` (Python 3.14), whose `pyexpat` can't link against the system `libexpat.1.dylib`. The fix is the documented `.venv` activation + `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` workaround from `CONTEXT.md` (specifics block). Smoke tests in this plan ran under that exact activation and passed. `make plots` itself is unchanged from pre-plan state — the plan didn't touch its recipe body. CONTEXT.md explicitly defers fixing libexpat: "don't try to fix the Homebrew Python libexpat issue in this phase". Logged here for visibility; no action taken.

## Verification Evidence

Ran under `.venv` activation + `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`:

- `python3 scripts/plot_results.py --workload congress --traces-dir traces` — exit 0; produced 6 PDFs in `results/congress/figures/` (mrc.pdf, byte_mrc.pdf, alpha_sensitivity.pdf, ohw.pdf, shards_mrc.pdf, workload.pdf).
- `python3 scripts/plot_results.py --results-dir results/congress --traces-dir traces` — exit 0 (back-compat path).
- `python3 scripts/plot_results.py --workload court --traces-dir traces` — exit 0 against the empty `results/court/` stub; every plot function logs "Skipping ... not found" and the script completes without traceback (no crash on missing court data).
- `make clean && make` — exit 0; clean build.
- `make run` — exit 0; output contains "Done. Results written to results/congress/"; `results/congress/mrc.csv`, `shards_mrc.csv` (not produced here — only MRC + OHW in default mode), and `one_hit_wonder.csv` regenerated.

Key greps verifying the artifact contract (all pass):

- `grep 'default="congress"' scripts/plot_results.py` — matches.
- `grep 'args.results_dir or os.path.join' scripts/plot_results.py` — matches.
- `grep 'def plot_workload(traces_dir, figures_dir, workload=' scripts/plot_results.py` — matches.
- `grep '{workload}_trace.csv' scripts/plot_results.py` — matches.
- `grep 'def _has_throughput(df):' scripts/plot_results.py` — still matches (plan 01-03 helper preserved).
- `grep '^run: \$(TARGET)$' Makefile` — matches.
- `grep 'mkdir -p results/congress' Makefile` — matches (twice, run and run-sweep).
- `grep -- '--output-dir results/congress' Makefile` — matches (twice).

## User Setup Required

None — no external service configuration required by this plan.

Note for future phases: to actually run `make plots` on this machine without seeing libexpat tracebacks, activate `.venv` and export `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`. This is a documented CONTEXT.md specific, not a plan-introduced requirement.

## Next Phase Readiness

- **Phase 3 (CourtListener production collection)** can now write directly to `results/court/` via `cache_sim --output-dir results/court` and will plot via `python3 scripts/plot_results.py --workload court` without edits.
- **Phase 4 (1M SHARDS)** can write to `results/shards_large/` using the same pattern.
- **Phase 5 (cross-workload)** has a predictable `results/{workload}/` layout for a compare_workloads.py driver to walk.
- No blockers for downstream phases.

## Self-Check: PASSED

Verifications:
- `test -d results/congress` — FOUND
- `test -d results/court` — FOUND
- `test -d results/shards_large` — FOUND
- `test -d results/compare` — FOUND
- `test -f results/congress/mrc.csv` — FOUND
- `test -f results/congress/alpha_sensitivity.csv` — FOUND
- `test -f results/congress/one_hit_wonder.csv` — FOUND
- `test -f results/congress/shards_mrc.csv` — FOUND
- `test -f results/congress/figures/mrc.pdf` — FOUND
- `git log --oneline | grep a424263` — FOUND (Task 2 commit)
- `git log --oneline | grep c96d83a` — FOUND (Task 3 commit)
- `grep '_has_throughput' scripts/plot_results.py` — FOUND (01-03 helper preserved)

---
*Phase: 01-enabling-refactors-courtlistener-pilot*
*Plan: 04*
*Completed: 2026-04-18*
