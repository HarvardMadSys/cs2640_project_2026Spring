---
phase: 02-w-tinylfu-core
plan: 04
subsystem: testing
tags: [wtinylfu, cms, testing, makefile, cpp17]

# Dependency graph
requires:
  - phase: 02-w-tinylfu-core
    provides: "include/count_min_sketch.h (WTLFU-01), include/wtinylfu.h (WTLFU-02), make_policy wtinylfu dispatch (WTLFU-03)"
provides:
  - "Standalone assertion-based test binary (tests/test_wtinylfu.cpp, 198 lines) exercising CMS basics, CMS aging via force_age()+natural threshold, hot-object-survives-scan (WTLFU-04 literal), and determinism"
  - "`make test` target building into build/test/ (separate object dir per D-07) so `make && make test` does not invalidate the main simulator"
  - "Grader can run `make clean && make && make test` in a fresh clone and see 4 PASS lines in <1s wall-clock, exit 0"
affects: [phase-02-06-validation-sweep, phase-04-ablations]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure C++17 assert() via TEST_ASSERT macro — accumulates failures across all tests rather than abort-on-first, so a single run surfaces every broken invariant"
    - "Separate build/test/ object directory (D-07) so test builds never touch the main simulator's build/"
    - "Test source is self-contained — no trace dependency (grader can run on a clean checkout)"

key-files:
  created:
    - "tests/test_wtinylfu.cpp (198 lines, 4 test functions + int main)"
  modified:
    - "Makefile (.PHONY += test; new TEST_SRC/TEST_OBJDIR/TEST_OBJ/TEST_TARGET block; clean extended to rm -rf $(TEST_OBJDIR))"

key-decisions:
  - "CMS basics N=10 (well below 4-bit saturation at COUNTER_MAX=15) to allow exact-estimate reads with CONSERVATIVE update"
  - "CMS aging test uses BOTH force_age() (deterministic halve) AND a sample_size+10 burst to exercise the natural auto-halve path; asserts sample_count()==10 exactly after the burst"
  - "Hot-survives-scan cache sizing: cap_bytes=20000, obj_size=100, n_obj_hint=200 — scan of 1000 unique keys overflows capacity 5x but hot's CMS freq (>=20) dominates scan freq (1 each) under D-08e strict `>`"
  - "Determinism test is a 500-access mixed workload (every 5th access = HOT, rest = scan_i) run twice into std::vector<bool>; asserts bit-identical output sequences"
  - "TEST_ASSERT accumulates failures (not abort-on-first) so one `make test` run surfaces every broken invariant"
  - "Makefile uses `| $(TEST_OBJDIR)` order-only prerequisite + explicit mkdir target, matching the existing main OBJDIR pattern"

patterns-established:
  - "Standalone test binary pattern: separate source tree (tests/), separate object dir (build/test/), single self-contained translation unit compiled with `-Iinclude`, no third-party dependencies"
  - "TEST_ASSERT(expr, test_name) macro: prints `FAIL: <tn> — assertion \"expr\" failed at file:line` + increments global failure counter; main returns (failures > 0) ? 1 : 0"

requirements-completed: [WTLFU-04]

# Metrics
duration: 3m 20s
completed: 2026-04-19
---

# Phase 2 Plan 4: W-TinyLFU Test Binary Summary

**Standalone C++17 assertion-based test binary (tests/test_wtinylfu.cpp, 198 lines) + `make test` target in build/test/ — 4 tests encode D-05 invariants (CMS basics, CMS aging via force_age(), WTLFU-04 literal hot-survives-1000-scan, determinism); `make clean && make && make test` exits 0 with zero warnings.**

## Performance

- **Duration:** 3m 20s
- **Started:** 2026-04-19T03:58:08Z
- **Completed:** 2026-04-19T04:01:28Z
- **Tasks:** 2
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments

- `tests/test_wtinylfu.cpp` (198 lines) — 4 assertion-based test functions covering all D-05 invariants; no third-party framework, no RNG, no trace dependency
- `Makefile` `test` target + separate `build/test/` object directory per D-07; `make clean` extended to remove both `build/` and `build/test/`
- WTLFU-04 satisfied: the 20-access hot key survives a 1000-access sequential scan of unique keys (literal grep-verifiable via `i < 1000` + `for(.*1000`)
- Aging test is deterministic — uses `force_age()` (test-only hook from D-10) first, then drives the natural `sample_size` threshold with a `sample_size + 10` burst and asserts `sample_count() == 10` exactly
- Determinism test would catch any future RNG/clock-based non-determinism regression

## Task Commits

Each task was committed atomically:

1. **Task 1: tests/test_wtinylfu.cpp** — `2e48457` (test)
2. **Task 2: Makefile test target + build/test/ object dir** — `28cc8ab` (feat)

## Files Created/Modified

- `tests/test_wtinylfu.cpp` (NEW, 198 lines) — Standalone test binary with 4 test functions:
  - `test_cms_basics` — record N=10 of "hot_key", assert estimate in [10, 13]; a never-recorded key estimates to 0
  - `test_cms_aging` — record "A" twice, assert estimate is 2; `force_age()`, assert estimate halves to 1 AND `sample_count()==0`; then a `sample_size+10` burst to exercise natural auto-halve (asserts `sample_count() == 10` exactly)
  - `test_hot_survives_scan` — insert "HOT" with 20 accesses (100-byte objects, 20000-byte cache), scan 1000 unique "scan_i" keys, assert final `cache.access("HOT", 100)` returns true
  - `test_determinism` — 500-access mixed workload run twice, assert bit-identical `std::vector<bool>` hit sequences
- `Makefile` (MODIFIED) — `.PHONY` adds `test`; new `TEST_SRC`/`TEST_OBJDIR=build/test`/`TEST_OBJ`/`TEST_TARGET` block with its own compile+link rules; `clean` extended to `rm -rf $(OBJDIR) $(TEST_OBJDIR) $(TARGET)`

## Decisions Made

- **N=10 for CMS basics** (not 200) — stays below 4-bit COUNTER_MAX=15 so CONSERVATIVE update (locked by WTLFU-01) produces an exact estimate at width≥1024. A larger N would saturate and the `est <= N+3` tolerance would need to be replaced with `est <= COUNTER_MAX`, obscuring the test intent.
- **Cache sizing for WTLFU-04** — 20000 bytes / 100-byte objects = ~200-resident, 1000-unique-scan = 100,000 bytes = 5× overflow. Picking smaller cache (e.g., 1000 bytes = 10 residents) would make window+admission dynamics more fragile; 200 residents is a comfortable margin where hot's CMS freq clearly dominates under D-08e strict `>`.
- **force_age() + natural-threshold both tested** — CONTEXT.md D-05 second bullet literally says "aging cadence test deterministically." `force_age()` covers the deterministic half; the natural `sample_size+10` burst covers the auto-path. Asserting `sample_count() == 10` exactly after the burst is a strong structural check (fires if `halve_all_` forgot to zero `sample_count_`, or if `sample_size` formula drifts).
- **TEST_ASSERT accumulates, not aborts** — A single `make test` run surfaces every broken invariant rather than failing at the first assertion. Matches the D-06 intent: exit code signals pass/fail; stderr lines document which invariants broke.
- **Comment edit for literal-string D-06 compliance** — A single comment in `tests/test_wtinylfu.cpp` originally read "no Catch2/googletest" (documenting what was NOT used). Since the success criteria include a strict `grep -riE "catch2|gtest|doctest|googletest"` check that would fire on framework NAME strings regardless of context, reworded to "no third-party test framework."

## Deviations from Plan

None — plan executed exactly as written. Minor edit made for literal-string compliance (see last bullet above) was not a deviation but a precision-of-wording tweak committed as part of Task 2.

## Issues Encountered

None.

## Verification Output (full `make test` stdout — pastes the pass state so SUMMARY alone documents it)

```
mkdir -p build/test
g++ -std=c++17 -O2 -Wall -Wextra -Iinclude -MMD -MP -c -o build/test/test_wtinylfu.o tests/test_wtinylfu.cpp
g++ -std=c++17 -O2 -Wall -Wextra -Iinclude -MMD -MP -o build/test/test_wtinylfu build/test/test_wtinylfu.o
=== Running W-TinyLFU test suite ===
build/test/test_wtinylfu
=== W-TinyLFU + CountMinSketch test suite ===
PASS: cms_basics
PASS: cms_aging
PASS: hot_survives_scan
PASS: determinism

All tests PASSED.
```

Exit code: **0**. Wall-clock of binary execution (excluding compile): **~19 ms**.

Idempotency confirmed: back-to-back `make` after `make test` reports `make: Nothing to be done for 'all'.` (no stale rebuilds).

## Acceptance Criteria Pass Table

| Criterion | Status |
| --- | --- |
| `tests/test_wtinylfu.cpp` exists, ≥120 lines, contains `int main(` | PASS (198 lines) |
| `Makefile` has `test` in `.PHONY` list | PASS |
| `make test` → exit 0 (all tests pass) | PASS |
| `grep -qE "i\s*<\s*1000" tests/test_wtinylfu.cpp` passes | PASS |
| `grep -qE "for\s*\(.*1000" tests/test_wtinylfu.cpp` passes | PASS |
| No `catch2`/`gtest`/`doctest`/`googletest` strings anywhere in tests/ or Makefile | PASS |
| `make clean && make && make test` → exit 0, zero warnings, main cache_sim binary also built | PASS |
| `.planning/phases/02-w-tinylfu-core/02-04-SUMMARY.md` created | PASS |
| STATE.md updated; ROADMAP 02-04 checkbox `[x]` | PENDING (handled at plan close) |

## Next Phase Readiness

- WTLFU-04 acceptance satisfied; `make test` is now the grader's green-light litmus check for the W-TinyLFU port
- Unblocks Plan 02-05 (plot_results.py POLICY_COLORS/MARKERS entry) and Plan 02-06 (validation sweep) — both can proceed in parallel as Wave 4/5 work
- No blockers introduced

## Self-Check: PASSED

Files claimed FOUND:
- `tests/test_wtinylfu.cpp`
- `Makefile`
- `.planning/phases/02-w-tinylfu-core/02-04-SUMMARY.md`

Commit hashes FOUND in `git log --oneline --all`:
- `2e48457` — Task 1 (test source)
- `28cc8ab` — Task 2 (Makefile test target)

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-19*
