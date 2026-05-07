---
phase: 02-w-tinylfu-core
plan: 05
subsystem: viz
tags: [matplotlib, plot_results, POLICY_COLORS, POLICY_MARKERS, wtinylfu, python]

# Dependency graph
requires:
  - phase: 02-w-tinylfu-core
    provides: "Plan 02-03 W-TinyLFU simulator — emits 'W-TinyLFU' as policy name in CSV rows"
provides:
  - "POLICY_COLORS['W-TinyLFU'] = '#8c564b' (matplotlib tab:brown) in scripts/plot_results.py"
  - "POLICY_MARKERS['W-TinyLFU'] = 'P' (filled plus) in scripts/plot_results.py"
  - "Automatic rendering of W-TinyLFU on MRC / byte-MRC / alpha-sensitivity plots once 02-06 generates CSVs containing that policy"
affects: [02-06, phase-3-courtlistener, phase-5-writeup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single source of truth for policy visual identity — both dicts keyed on the exact CSV policy string emitted by src/main.cpp (here 'W-TinyLFU', not 'wtinylfu')"

key-files:
  created: []
  modified:
    - "scripts/plot_results.py (+2 lines — one POLICY_COLORS entry, one POLICY_MARKERS entry)"
    - ".gitignore (+4 lines — __pycache__/ and *.pyc rules; Rule 3 hygiene)"

key-decisions:
  - "W-TinyLFU color = #8c564b (matplotlib tab:brown) — deliberately NOT CONTEXT.md's suggested purple #9467bd because SIEVE already owns it"
  - "W-TinyLFU marker = P (filled plus) — deliberately NOT CONTEXT.md's suggested D because S3-FIFO already owns it"
  - "No plot-function edits — existing .get(policy, fallback) calls at lines 79/113/150 pick up new dict entries automatically"
  - ".gitignore pycache rule added as Rule 3 hygiene (our import smoke test created scripts/__pycache__/); prevents future untracked-file churn"

patterns-established:
  - "Dict-key matching CSV policy string: the exact string emitted by include/wtinylfu.h::name() ('W-TinyLFU') is the plot-dict key. Future policies added to src/main.cpp must use this exact string in both the cache policy's name() method AND the plot dicts, or plots will gray-fallback."
  - "Color/marker uniqueness invariant: plan-level threat model T-02-05-01 mandates every (color, marker) pair be distinct across the 6 policies; verified programmatically (len(set(values)) == len(dict))."

requirements-completed: [WTLFU-02]

# Metrics
duration: 2min
completed: 2026-04-19
---

# Phase 02 Plan 05: plot_results W-TinyLFU entry Summary

**Two dict entries added to scripts/plot_results.py (POLICY_COLORS['W-TinyLFU']='#8c564b' tab:brown, POLICY_MARKERS['W-TinyLFU']='P' filled plus) — distinct from all 5 existing policies, so once 02-06 regenerates CSVs with W-TinyLFU rows, the MRC / byte-MRC / alpha-sensitivity plots render W-TinyLFU automatically via the existing `.get(policy, fallback)` loops at lines 79/113/150; CONTEXT.md's suggested purple/D both taken (SIEVE, S3-FIFO)**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-19T04:05:30Z
- **Completed:** 2026-04-19T04:07:05Z
- **Tasks:** 1 / 1 (single-task plan)
- **Files modified:** 2 (scripts/plot_results.py + .gitignore)

## Accomplishments

- POLICY_COLORS gains a `"W-TinyLFU": "#8c564b"` entry (matplotlib's tab:brown — the 6th tab10 color, maximally distinct from the 5 tab10 colors already assigned to LRU / FIFO / CLOCK / S3-FIFO / SIEVE)
- POLICY_MARKERS gains a `"W-TinyLFU": "P"` entry (matplotlib's filled plus — visually distinct from the 5 existing markers o / s / ^ / D / v)
- Regression confirmed: `make plots` on existing Congress CSVs still generates all 6 PDFs (mrc, byte_mrc, alpha_sensitivity, ohw, shards_mrc, workload) with zero crashes. W-TinyLFU does not appear in current legends because the CSVs predate the policy; 02-06 will regenerate the sweep CSVs and then W-TinyLFU will render automatically.
- All 6 POLICY_COLORS values and all 6 POLICY_MARKERS values verified unique (T-02-05-01 mitigation programmatically asserted)
- `.gitignore` gains `__pycache__/` and `*.pyc` rules (Rule 3 hygiene — prevents the `scripts/__pycache__/` created by our import smoke test from being picked up as untracked on future commits)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add "W-TinyLFU" entries to POLICY_COLORS / POLICY_MARKERS** — `b089209` (feat)

_Plan metadata (SUMMARY + STATE + ROADMAP + REQUIREMENTS) commit hash appended after state updates._

## Files Created/Modified

- `scripts/plot_results.py` — added `"W-TinyLFU": "#8c564b"` to POLICY_COLORS (line 51) and `"W-TinyLFU": "P"` to POLICY_MARKERS (line 60). Exactly 2 insertions per `git diff --stat`. No plot-function edits.
- `.gitignore` — added `__pycache__/` and `*.pyc` rules under a new "Python bytecode caches" heading. Rule 3 hygiene; unrelated to W-TinyLFU rendering but necessary to prevent bytecode dirs created by the import smoke test from polluting future `git status` output.

## Decisions Made

- **Color = `#8c564b` (tab:brown), NOT the `#9467bd` (tab:purple) suggested by CONTEXT.md "Claude's Discretion":** CONTEXT.md §decisions line 86 suggests purple/D as a starter. But SIEVE already owns `#9467bd` in POLICY_COLORS (line 50). Reusing purple would overlay SIEVE and W-TinyLFU lines on every MRC / byte-MRC / alpha-sensitivity plot, making the two policies indistinguishable — a direct violation of T-02-05-01 ("visual confusion hiding a policy"). `#8c564b` is the next entry in matplotlib's tab10 cycle and is the canonical choice for a 6th policy in a tab10-themed project.
- **Marker = `"P"` (filled plus), NOT `"D"` (diamond):** S3-FIFO already owns `"D"` in POLICY_MARKERS (line 58). Same rationale as above — reusing `D` would collide legend markers between S3-FIFO and W-TinyLFU. `"P"` (filled plus) is a matplotlib marker visually distinct from circle (LRU), square (FIFO), triangle-up (CLOCK), diamond (S3-FIFO), and triangle-down (SIEVE), and remains readable at the 5pt markersize the existing loops use.
- **No plot-function edits:** The existing loops at lines 79, 113, 150 use `POLICY_COLORS.get(policy, "gray")` and `POLICY_MARKERS.get(policy, "x")`. These already handle arbitrary policy strings via dict lookup with a fallback. Adding W-TinyLFU to the dicts is sufficient for the three core plots to render it; no per-policy special-casing required. This preserves the plan's 2-line diff contract.
- **Dict key string `"W-TinyLFU"` (not `"wtinylfu"`):** The CLI accepts `wtinylfu` as a `--policies` arg, but `include/wtinylfu.h::name()` returns `"W-TinyLFU"`, and main.cpp writes that name into the CSV `policy` column. Plots lookup the CSV string, so the dict key must be `"W-TinyLFU"` verbatim. Confirmed via Wave 3 plan 02-03 decision log (STATE.md line 76).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking hygiene] Added `__pycache__/` + `*.pyc` to .gitignore**
- **Found during:** Task 1 verification (running the import smoke test)
- **Issue:** Running `python3 -c "import sys; sys.path.insert(0, 'scripts'); import plot_results; ..."` creates `scripts/__pycache__/plot_results.cpython-314.pyc` as a side effect. With no .gitignore entry for Python bytecode, this showed up as an untracked directory in `git status` at commit time. Without the rule, every future execution of our own acceptance criteria would re-create the same untracked dir, either polluting commits or requiring manual stash every time.
- **Fix:** Added a "Python bytecode caches" heading under the existing "Python venv" block in `.gitignore` with two lines: `__pycache__/` and `*.pyc`. Consistent with standard Python project `.gitignore` templates.
- **Files modified:** `.gitignore`
- **Verification:** `git status --short` after the rule change no longer lists `scripts/__pycache__/`
- **Committed in:** `b089209` (part of Task 1 commit — same logical change)

---

**Total deviations:** 1 auto-fixed (Rule 3 blocking hygiene)
**Impact on plan:** Minimal — `.gitignore` change does not touch `scripts/plot_results.py` so the plan's "exactly 2 insertions to scripts/plot_results.py" acceptance criterion still holds. The pycache rule prevents future untracked-file churn and has zero runtime effect.

## Issues Encountered

- **matplotlib not in system python3:** Initial smoke test with plain `python3` failed with `ModuleNotFoundError: No module named 'matplotlib'`. The repo's matplotlib lives in `.venv/lib/python3.14/site-packages/` and is invoked via the `plots` target's custom PATH/DYLD setup (`PLOT_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14`, `PLOT_PYTHONPATH=.venv/lib/python3.14/site-packages`, `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` — macOS libexpat workaround inherited from Phase 01). Re-ran verification with the Makefile's env and all assertions passed. Not a plan deviation — just a reminder that this repo's Python env is non-standard on macOS and any future plan that runs Python verification must invoke `PLOT_PYTHON` with the Homebrew env or call `make plots` directly.

## User Setup Required

None - no external service configuration required. This is a pure code change to a Python module.

## Next Phase Readiness

- Wave 4 Plan 05 done; the final plan in Phase 02 is 02-06 (validation sweep that regenerates MRC / byte-MRC / alpha_sensitivity CSVs with the 6-policy list including `wtinylfu`).
- Once 02-06 runs, `make plots` will automatically render W-TinyLFU as brown/plus on the three main figures — no further code changes required.
- All phase-02 success criteria except the sweep validation are now satisfied. WTLFU-02 deliverable list (requirements mark-complete in this plan) now checks the plot-render item off.

## Self-Check: PASSED

Verification:
- [x] `scripts/plot_results.py` exists and contains both new entries (grep count = 2)
- [x] Commit `b089209` exists in `git log --oneline` (verified via `git rev-parse --short HEAD`)
- [x] `make plots` completes with 6 PDFs generated on existing Congress CSVs (regression safety)
- [x] All POLICY_COLORS values unique (6/6), all POLICY_MARKERS values unique (6/6)
- [x] No syntax error (AST parse OK)
- [x] No unintended file deletions in commit (`git diff --diff-filter=D --name-only HEAD~1 HEAD` returned empty)

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-19*
