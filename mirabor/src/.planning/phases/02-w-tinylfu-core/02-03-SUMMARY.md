---
phase: 02-w-tinylfu-core
plan: 03
subsystem: cache-policy
tags: [cpp17, w-tinylfu, count-min-sketch, cache-eviction, caffeine-port, slru]

# Dependency graph
requires:
  - phase: 02-w-tinylfu-core
    provides: "Plan 02-01 — Caffeine v3.1.8 reference notes locking D-08a..D-08e edge-case rules and 6 deliberate deviations"
  - phase: 02-w-tinylfu-core
    provides: "Plan 02-02 — include/count_min_sketch.h (4-bit packed CMS, depth=4, conservative update, periodic halving)"
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: "include/hash_util.h (FNV-1a with 4 seeds — consumed transitively through count_min_sketch.h)"
provides:
  - "include/wtinylfu.h — WTinyLFUCache policy class (window LRU + SLRU + CMS admission)"
  - "src/main.cpp make_policy() widened with n_objects_hint parameter (D-02); wtinylfu dispatch"
  - "--policies CLI accepts wtinylfu; label 'W-TinyLFU' in MRC and alpha-sensitivity table headers"
  - "include/cache.h appends #include \"wtinylfu.h\" as subordinate header (L-11)"
affects: ["02-04-tests", "02-05-plots", "02-06-validation-sweep", "phase-03-court-trace", "phase-04-doorkeeper"]

# Tech tracking
tech-stack:
  added: []  # no new external deps (PROJECT.md "no external C++ deps" non-negotiable)
  patterns: ["Header-only policy class pattern mirrored from include/cache.h LRUCache/SIEVECache", "Factory-dispatch signature widening with (void)n_objects_hint ignore-pattern for non-consuming policies"]

key-files:
  created:
    - "include/wtinylfu.h (228 lines — WTinyLFUCache with window LRU + protected/probation SLRU + embedded CMS)"
  modified:
    - "include/cache.h (+4 lines — subordinate-include block at bottom)"
    - "src/main.cpp (+30 / -13 — signature widen, wtinylfu branch, default --policies, n_obj_hint plumbing, label mapping)"

key-decisions:
  - "Dropped unused total_capacity_ and probation_capacity_ members — flagged by -Wunused-private-field under -Wall -Wextra; the admission logic only uses main_capacity_ and protected_capacity_, with probation bounded implicitly by (main - protected). Fix applied at source (no pragma suppression) per plan's build-clean gate."
  - "ws.mean_size reused from outer characterize() call in both MRC and alpha-sweep loops — avoids O(N) per-alpha re-scan (matches REFACTOR-02 hoisting pattern from Phase 1)."
  - "D-08a short-circuit kept as explicit empty-probation admit (CONTEXT.md-locked simplification of Caffeine's victim-escalation path) — CAFFEINE-NOTES §6 row 4 justifies the deviation."
  - "D-08e strict `>` comparison without 1/128 hash-DoS random admission — no adversarial threat model; preserves determinism for D-05 test."

patterns-established:
  - "Stats single-source (L-12): public access() makes exactly 4 record(hit,size) calls (3 hit returns + 1 miss fallthrough); all helpers (evict_window_if_needed_, admit_candidate_to_main_, demote_protected_lru_to_probation_mru_) MUST NOT touch stats. Enforced by grep-countable acceptance criterion."
  - "CMS updated FIRST in access() (before any branch) so admission test reads up-to-date frequencies — matches Caffeine onAccess semantics (FrequencySketch.increment before any region-dispatch)."
  - "Factory signature widening pattern: (void)n_objects_hint at top of factory signals a deliberately-ignored parameter for non-consuming branches (lru/fifo/clock/s3fifo/sieve); only wtinylfu forwards it to the constructor."

requirements-completed: [WTLFU-02, WTLFU-03]

# Metrics
duration: 18min
completed: 2026-04-18
---

# Phase 02 Plan 03: W-TinyLFU Policy Header + Integration Summary

**Header-only WTinyLFUCache with 1% window LRU / 99% SLRU (80% protected / 20% probation), CMS admission via embedded CountMinSketch, wired into make_policy factory with D-02 n_objects_hint plumbing from workload_stats.mean_size.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-04-18 (per plan commit sequence)
- **Completed:** 2026-04-18
- **Tasks:** 3 of 3
- **Files modified:** 3 (1 created, 2 edited)

## Accomplishments

- `include/wtinylfu.h` (228 lines) — WTinyLFUCache with byte-bounded regions, Caffeine-verbatim D-08a..D-08e admission pipeline, and stats-single-source discipline (exactly 4 record(true|false) calls, grep-enforced)
- `include/cache.h` subordinate-include: 4-line block at bottom preserves single-include invariant from main.cpp (only `#include "cache.h"` needed)
- `src/main.cpp::make_policy` widened to `(name, capacity, n_objects_hint)`; wtinylfu branch dispatches `WTinyLFUCache(capacity, n_objects_hint)`; both MRC and alpha-sweep loops compute `n_obj_hint = max(1, cache_bytes / ws.mean_size)` from the outer workload_stats
- `--policies` default list and usage help include `wtinylfu`; CLI label map renders `W-TinyLFU` in both MRC and alpha-sweep table headers
- Smoke run: `./cache_sim --policies wtinylfu --num-requests 10000 --num-objects 1000 --output-dir /tmp/wtlfu_smoke` completes in ~0.25s wall; mrc.csv contains 6 W-TinyLFU rows
- Regression smoke: existing 5 policies emit 30 rows (LRU/FIFO/CLOCK/S3-FIFO/SIEVE × 6 cache fractions) unchanged; LRU at 1% cache_frac = 0.8993 miss ratio (baseline preserved — widening add `(void)n_objects_hint` is structurally inert for non-consuming branches)

## Task Commits

1. **Task 1: Create include/wtinylfu.h** — `9613a30` (feat)
2. **Task 2: Append #include "wtinylfu.h" to cache.h + drop unused fields** — `6880a34` (feat)
3. **Task 3: Widen make_policy + wire wtinylfu into MRC/alpha-sweep loops** — `8599b60` (feat)

**Plan metadata commit:** pending final docs commit

## Files Created/Modified

- `include/wtinylfu.h` (created, 228 lines) — WTinyLFUCache with window LRU + protected/probation SLRU + CMS admission; inline-documented citation back to CAFFEINE-NOTES.md §4 + §6
- `include/cache.h` (modified, +4) — subordinate `#include "wtinylfu.h"` block after SIEVECache closing brace
- `src/main.cpp` (modified, +30 / -13) — make_policy signature widen, wtinylfu dispatch, default --policies list update, usage-help update, n_obj_hint computation in both loops, W-TinyLFU label mapping in both loop headers

## D-08a..D-08e Verification (grep-match against CAFFEINE-NOTES.md §4)

| Rule | Location | Status |
|------|----------|--------|
| D-08a (empty probation → admit unconditionally) | `include/wtinylfu.h:172` (`admit = true; // D-08a`) | grep-match OK |
| D-08b (spare main capacity → admit unconditionally) | `include/wtinylfu.h:175` (`admit = true; // D-08b`) | grep-match OK |
| D-08c (ANY probation hit → promote to protected MRU) | `include/wtinylfu.h:86` and probation-hit branch in access() (push_back into protected_list_) | grep-match OK |
| D-08d (protected overflow → demote LRU to probation MRU) | `include/wtinylfu.h:201` with `probation_list_.push_back(demoted)` (NOT push_front) | grep-match OK |
| D-08e (strict `fc > fv` reject-on-tie) | `include/wtinylfu.h:180` (`admit = (fc > fv)`); no `>=` anywhere | grep-match OK |

Additional grep-enforced invariants:

- `grep -cE "record\(\s*(true|false)" include/wtinylfu.h` = **4** (L-12 stats single-source preserved)
- `cms_.record(key)` at line 55; first `window_map_.find` at line 58 — CMS-update-first-before-branching confirmed
- No `LRUCache|SIEVECache|FIFOCache|CLOCKCache|S3FIFOCache` tokens in wtinylfu.h (no policy-class composition per L-12)
- `cms_.reset()` and `stats = {}` both present in `reset()` — discipline preserved

## Smoke Run Metrics

**W-TinyLFU-only run** (`/tmp/wtlfu_smoke/mrc.csv`):

| cache_frac | miss_ratio | byte_miss_ratio | accesses_per_sec |
|-----------|-----------|----------------|-----------------|
| 0.001 | 0.9465 | 0.9891 | 2.79M/s |
| 0.005 | 0.7511 | 0.9505 | 4.25M/s |
| 0.01  | 0.7488 | 0.9116 | 7.22M/s |
| 0.02  | 0.6996 | 0.7814 | 6.95M/s |
| 0.05  | 0.5576 | 0.6332 | 7.36M/s |
| 0.1   | 0.4519 | 0.5654 | 8.56M/s |

**Wall-clock:** ~0.25 sec for the 10000-request / 1000-object smoke on 6 cache sizes.

**Regression-smoke** (`/tmp/other_smoke/mrc.csv` for lru+fifo+clock+s3fifo+sieve) produced **exactly 30 rows** (5 policies × 6 cache fractions) — pre-widening baseline preserved. LRU at 1% cache_frac miss_ratio = 0.8993 (no regression from the make_policy widening, as expected since non-wtinylfu branches ignore `n_objects_hint` via `(void)` discard).

## Decisions Made

- **Dropped `total_capacity_` and `probation_capacity_` unused fields.** Build under `-Wall -Wextra` flagged both as `-Wunused-private-field`; removing them at the source (no `[[maybe_unused]]` or pragma suppression per plan's build-clean gate) is cleaner since they are implied by invariants (`total = window + main`; `probation = main - protected`). The admission logic only ever references `window_capacity_`, `main_capacity_`, and `protected_capacity_`.
- **Preserved outer `ws` reuse across alpha sweep** per existing REFACTOR-02 hoisting pattern — Per-alpha recomputation is unnecessary because sweep traces resample the same prepared_objects key set, so `ws.mean_size` is stable across alpha values.
- **Kept D-08a explicit short-circuit** (Caffeine does NOT have a literal "empty probation → admit" branch; CAFFEINE-NOTES §6 row 4 documents this deliberate simplification).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Compile Warning / Build-Clean Gate] Removed unused `total_capacity_` and `probation_capacity_` members**

- **Found during:** Task 2 (cache.h include + full rebuild)
- **Issue:** g++ under `-std=c++17 -Wall -Wextra` flagged `total_capacity_` and `probation_capacity_` as `-Wunused-private-field` (4 instances × 4 translation units = 8 warning lines total across main.o, shards.o, trace_gen.o, workload_stats.o). Plan acceptance criterion is zero warnings.
- **Fix:** Removed both fields from the member declarations AND the constructor initializer list; added `(void)capacity_bytes;` in the constructor body with a comment explaining the invariants (`total = window + main`; `probation = main - protected`). `main_capacity_` is the effective admission budget.
- **Files modified:** `include/wtinylfu.h`
- **Verification:** `make clean && make 2>&1 | grep -iE "warning:|error:"` returns empty; all Task 1 grep gates still pass (1% window literal, 80% protected literal, record count=4).
- **Committed in:** `6880a34` (folded into Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 build-clean correction)
**Impact on plan:** Necessary for build-clean acceptance criterion; no scope creep, no semantic change. Both removed fields were informational (never read); retaining them would have required `[[maybe_unused]]` annotations which the plan explicitly prohibits.

## Issues Encountered

- None beyond the unused-field warnings auto-fixed above.
- No linker errors; transitive include chain `main.cpp → cache.h → wtinylfu.h → count_min_sketch.h → hash_util.h` resolves cleanly under the existing include-path conventions.

## Threat Surface Scan

Plan `<threat_model>` declared T-02-03-01 (hash-collision flood) and T-02-03-02 (DoS via unbounded region growth) with `accept` and `mitigate` dispositions respectively. The `mitigate` for T-02-03-02 required byte-budget eviction with early-termination guards — verified: all three internal eviction loops (`evict_window_if_needed_`, `admit_candidate_to_main_`, `demote_protected_lru_to_probation_mru_`) use `while (... && !list.empty())` patterns and drop the candidate/demoted entry if the main budget still cannot fit after victim eviction, preventing unbounded growth.

No new attack surface was introduced beyond what the plan anticipated.

## Next Phase Readiness

- **02-04 (tests):** `tests/test_wtinylfu.cpp` can `#include "cache.h"` (which transitively pulls in `wtinylfu.h` and `count_min_sketch.h`) and construct `WTinyLFUCache(capacity, n_objects_hint)` directly. Hot-object-survives-scan invariant (WTLFU-04 literal) can be exercised now.
- **02-05 (plots):** `scripts/plot_results.py` needs a `POLICY_COLORS["W-TinyLFU"]` and `POLICY_MARKERS["W-TinyLFU"]` entry; the CSV row already emits the `W-TinyLFU` policy string ready to match.
- **02-06 (validation sweep):** `./cache_sim --policies lru,fifo,clock,s3fifo,sieve,wtinylfu --trace traces/congress_trace.csv --replay-zipf --alpha-sweep --output-dir results/congress` will run end-to-end and produce the α-regime acceptance data.
- No blockers for downstream work.

## Self-Check: PASSED

- `include/wtinylfu.h` — FOUND (228 lines, `class WTinyLFUCache : public CachePolicy` present)
- `include/cache.h` — contains `#include "wtinylfu.h"` subordinate block
- `src/main.cpp` — contains `name == "wtinylfu"`, `WTinyLFUCache>(capacity, n_objects_hint)`, `n_obj_hint` defined twice, `make_policy(pn, cache_bytes, n_obj_hint)` called twice, `label = "W-TinyLFU"` twice
- Commits `9613a30`, `6880a34`, `8599b60` — all FOUND in `git log --oneline`
- Build clean under `-Wall -Wextra` — no warnings in `/tmp/build.log`
- Smoke CSV `/tmp/wtlfu_smoke/mrc.csv` contains W-TinyLFU rows; regression CSV has 30 rows for the other 5 policies

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-18*
