---
phase: 06-writeup-demo
plan: 02
subsystem: infra
tags: [shell, makefile, demo, gitignore, replay-zipf]

# Dependency graph
requires:
  - phase: 06-writeup-demo
    provides: ".PHONY: demo token added on Makefile line 13 (Plan 01, B-1 ownership-refactor)"
  - phase: 01-foundation
    provides: "./cache_sim binary + Makefile libexpat workaround env-var pattern"
  - phase: 02-wtinylfu
    provides: "wtinylfu policy (6th of 6) available via --policies flag"
  - phase: 03-courtlistener-trace-collection-replay-sweep
    provides: "traces/congress_trace.csv (source for demo slice per D-19)"
provides:
  - "demo.sh — repo-root executable live 6-policy sweep orchestrator (DOC-04)"
  - "traces/demo_trace.csv — 5001-line verbatim first-5K slice of congress_trace.csv"
  - ".gitignore affirmative re-include for traces/demo_trace.csv with D-19 citation"
  - "Makefile demo: target body (recipe-only; .PHONY entry owned by Plan 01)"
affects: [phase-06-writeup-demo-plan-07-rehearsal]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - ".env sourcing via [[ -f .env ]] guard + source .env (D-17)"
    - "macOS libexpat workaround replicated from Makefile:70-73 (DYLD_LIBRARY_PATH + PYTHONPATH)"
    - "3-step banner orchestration shape (=== Step N/3: ... ===) adapted from Makefile ablation-s3fifo"
    - "Wall-clock capture via date +%s deltas for rehearsal-log evidence"
    - "column -t -s, for live CSV pretty-print (D-15 narration)"
    - "set -euo pipefail fail-fast hygiene"

key-files:
  created:
    - "demo.sh"
    - "traces/demo_trace.csv"
    - ".planning/phases/06-writeup-demo/06-02-SUMMARY.md"
  modified:
    - ".gitignore"
    - "Makefile"

key-decisions:
  - "D-19 Option A: verbatim first-5K slice (deterministic, byte-identical across runs) over seeded sample"
  - "Guard `source .env` with `[[ -f .env ]]` so demo.sh works on fresh checkouts without .env (CI-friendly)"
  - "Export DYLD_LIBRARY_PATH + PYTHONPATH unconditionally (simpler than Makefile's per-target inline env)"

patterns-established:
  - "Demo orchestration: source .env guarded → set libexpat env vars → wall-clock start → 3 steps → wall-clock end"
  - "Gitignore re-include comments cite Phase + decision ID (Phase 6 (D-19)) for grep-discoverability"
  - "Makefile target-body ownership-refactor: Plan 01 owns .PHONY edits; downstream plans only append recipes"

requirements-completed: [DOC-04]

# Metrics
duration: ~10min
completed: 2026-04-22
---

# Phase 6 Plan 02: Live-Demo Pipeline Summary

**Repo-root `demo.sh` runs the full 6-policy sweep on a pre-generated 5K-request Congress slice in 4s wall-clock (60s budget), closing DOC-04 SC-3.**

## Performance

- **Duration:** ~10 min (sequential executor)
- **Started:** 2026-04-22T01:40Z (approximate)
- **Completed:** 2026-04-22T01:49:49Z
- **Tasks:** 3/3 completed
- **Files modified:** 4 (1 created binary asset, 1 created script, 2 modified config)

## Accomplishments

- **Live demo exists and runs end-to-end.** Dry-run against the main repo (where `./cache_sim`, `.venv`, and `.env` are available) completed in **4 seconds wall-clock** with a full miss-ratio table printed and `results/demo/figures/mrc.pdf` rendered — well under the 60s D-15 budget with ~56s headroom for Plan 07 narration.
- **Deterministic 5K demo trace committed** (5001 lines = 1 header + 5000 data rows, verbatim first-5K of `traces/congress_trace.csv` per D-19 Option A). Byte-identical across runs — no seed bookkeeping.
- **Makefile `make demo` target operational** — delegates to `./demo.sh`. Both `paper` (Plan 01) and `demo` targets coexist with exactly one `.PHONY:` line preserved (B-1 ownership-refactor invariant held).
- **`.gitignore` affirmative re-include** groups `!traces/demo_trace.csv` next to the existing Phase 3 trace re-includes with a `Phase 6 (D-19)` decision-ID comment for grep discoverability.

## Task Commits

Each task was committed atomically:

1. **Task 1: Generate traces/demo_trace.csv + .gitignore re-include** — `42190df` (feat)
2. **Task 2: Author demo.sh at repo root** — `3da4b2e` (feat)
3. **Task 3: Append `demo:` target body to Makefile** — `9c2e5ee` (feat)

_No TDD gates (plan is `type: execute`, not `type: tdd`); all commits are `feat`._

## Files Created/Modified

- `demo.sh` (new, 60 lines, chmod +x) — Live 6-policy sweep orchestrator; sources `.env`, sets `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` + `PYTHONPATH=.venv/lib/python3.14/site-packages`, invokes `./cache_sim --trace traces/demo_trace.csv --replay-zipf --policies lru,fifo,clock,s3fifo,sieve,wtinylfu --cache-sizes 0.001,0.01,0.05,0.1 --output-dir results/demo`, pretty-prints the miss-ratio table via `column -t -s, results/demo/mrc.csv`, renders a figure via `scripts/plot_results.py --workload demo`, and reports `wall-clock: ${ELAPSED}s`.
- `traces/demo_trace.csv` (new, 5001 lines) — Verbatim first-5K slice of `traces/congress_trace.csv`. Schema: `timestamp,key,size`.
- `.gitignore` (modified) — Added `!traces/demo_trace.csv` re-include with `Phase 6 (D-19)` comment, placed directly after the Phase 3 `court_trace.csv`/`court_pilot.csv` re-includes so all committed trace overrides cluster together.
- `Makefile` (modified) — Appended 9-line `demo:` target block at EOF (after Plan 01's `paper:` target). Recipe is `./demo.sh`. `.PHONY:` line on Makefile:13 UNCHANGED by this plan (verified: `grep -c '^\.PHONY:' Makefile` returns 1; Plan 01's edit already includes both `paper` AND `demo` tokens).

## Decisions Made

- **D-19 Option A chosen over Option B** — Verbatim first-5K slice rather than seeded sample. Simpler, byte-deterministic, and PATTERNS.md §"`traces/demo_trace.csv`" (lines 302-308) already favored it.
- **`source .env` guarded by `[[ -f .env ]]`** — Keeps `demo.sh` runnable on fresh checkouts or CI where `.env` is absent (gitignored per `.gitignore:68`). Harmless on the target laptop where `.env` exists.
- **`export` keyword on `DYLD_LIBRARY_PATH` and `PYTHONPATH`** — Required so the `$PLOT_PYTHON` subprocess (spawned by `scripts/plot_results.py` invocation) inherits them. `PLOT_PYTHON` itself intentionally left as a plain shell variable since only the direct `"$PLOT_PYTHON"` invocation needs it.

## Deviations from Plan

**None — plan executed exactly as written.**

All three tasks followed PATTERNS.md §demo.sh and the 06-02-PLAN.md action blocks verbatim. No Rule 1/2/3/4 deviations encountered. The B-1 ownership-refactor held (Plan 01 had already edited `.PHONY:` to include `demo` before this executor began).

## Issues Encountered

- **Worktree base-commit reset required.** The worktree initialized at commit `44f13ea` (a stale head from Phase 2), but the plan required base `cea8c64` (which includes Plan 01's Makefile `.PHONY:` edit). Resolved via `git reset --mixed cea8c64` followed by `git checkout HEAD -- .` to restore the working tree to `cea8c64`'s committed state before any Plan 02 edits began. Fully recovered; no artifacts lost.
- **Worktree does not contain `.env`, `.venv`, `cache_sim` binary, or `traces/congress_trace.csv`.** These are developer-local artifacts (`.env` gitignored; `cache_sim` + `build/` gitignored; traces/* filtered by ignore with specific re-includes; `.venv/` gitignored). The demo-trace generation pulled from the main-repo copy at `/Users/mirayu/civicache/traces/congress_trace.csv` (same file system; worktree shares the repo working-tree but keeps its own branch HEAD). The verbatim-first-5K slice is byte-identical regardless of which physical copy you `head` from — the slice itself is the committed artifact.
- **Dry-run ran from main repo (per plan output spec).** Used `bash /tmp/demo-test.sh` from `/Users/mirayu/civicache` where `./cache_sim`, `.venv`, and `.env` are present. Captured wall-clock: **4 seconds total**. Full 3-step pipeline completed (build → 6-policy sweep → figure render); `results/demo/figures/mrc.pdf` written. Note: the plan's output spec called for a "single dry-run" to confirm wall-clock — this is distinct from Plan 07's formal 3-rehearsal evidence log. The dry-run leftover files (`/Users/mirayu/civicache/traces/demo_trace.csv` copy, `/Users/mirayu/civicache/results/demo/`, `/tmp/demo-test.sh`) were not cleaned up due to sandbox `rm` permission denial; these are outside the worktree and do not affect any committed state. They should be cleaned manually before Plan 07.

## B-1 Ownership-Refactor Invariant Verified

Per plan output spec: **Makefile `.PHONY:` line was NOT edited by this plan.**

- Before any Task 3 edits: `grep -c '^\.PHONY:' Makefile` → `1`
- After Task 3 commit: `grep -c '^\.PHONY:' Makefile` → `1`
- `.PHONY:` line content is byte-identical to `cea8c64`'s version; Plan 02 only appended a recipe block at EOF.
- Plan 02 touched exactly one region of Makefile: bytes after Plan 01's `paper:` target.
- Plan 01's `.PHONY: ... paper demo` edit from commit `e87bf91` is preserved.

## User Setup Required

None — no external service configuration required by this plan. `.env` is still the gitignored dev-local file (CONGRESS_API_KEY + COURTLISTENER_API_KEY) but the demo itself does not need either key (cache_sim replay-Zipf reads only the committed CSV).

## Next Phase Readiness

- **Plan 07 (demo rehearsal)** can now invoke `./demo.sh` or `make demo` directly on the target laptop and pipe to `demo-rehearsal.log`. The dry-run evidence from this plan (4s wall-clock, 56s headroom) gives high confidence that Plan 07's 3 rehearsals will fit the <60s budget with narration room.
- **All acceptance criteria met** for DOC-04 SC-3 ("demo.sh exists, sources .env, sets DYLD_LIBRARY_PATH, runs a pre-loaded <10K-access trace through the simulator in <60s"). The "tested 3 times on target laptop" portion is formally closed by Plan 07.

## Self-Check: PASSED

- **FOUND:** demo.sh (executable, 60 lines, passes `bash -n`)
- **FOUND:** traces/demo_trace.csv (5001 lines, header `timestamp,key,size`)
- **FOUND:** Makefile demo: target body (recipe `./demo.sh`, PHONY-line count still 1)
- **FOUND:** .gitignore `!traces/demo_trace.csv` re-include with Phase 6 (D-19) comment
- **FOUND:** commit 42190df (Task 1 — demo_trace.csv + .gitignore)
- **FOUND:** commit 3da4b2e (Task 2 — demo.sh)
- **FOUND:** commit 9c2e5ee (Task 3 — Makefile demo: target)
- **NOT MODIFIED (correct):** STATE.md, ROADMAP.md (per parallel_execution directive)

---
*Phase: 06-writeup-demo*
*Completed: 2026-04-22*
