---
phase: 02-w-tinylfu-core
plan: 02
subsystem: cache-policy
tags: [count-min-sketch, cms, fnv-1a, w-tinylfu, header-only, cpp17]

# Dependency graph
requires:
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: "include/hash_util.h — fnv1a_64() with FNV_SEED_A..D (4 pre-pinned 64-bit seeds) for CMS rows"
  - phase: 02-w-tinylfu-core (plan 02-01)
    provides: "Caffeine v3.1.8 reference notes + §6 deviation register (STANDARD vs CONSERVATIVE update, 10*maxSize vs 10*width*depth sample_size)"
provides:
  - "include/count_min_sketch.h — 4-bit packed CMS, depth=4, width=nextpow2(hint), FNV-1a seeded rows, CONSERVATIVE update (WTLFU-01), periodic halving every 10*width*depth records"
  - "Public API per D-10: record(), estimate(), reset(), force_age() (test hook); read-only width()/sample_size()/sample_count() accessors"
affects: [02-03 wtinylfu.h (consumer), 02-04 test_wtinylfu.cpp (drives record/estimate/force_age/reset directly)]

# Tech tracking
tech-stack:
  added: []  # no new deps; pure C++17 + existing hash_util.h
  patterns:
    - "Header-only peer module (not a CachePolicy subclass) for standalone testability — matches research/ARCHITECTURE.md L-11 / CONTEXT.md §Code Insights"
    - "4-bit counter packing: two counters per byte, nibble-indexed via (col & 1); halving via (b >> 1) & 0x77 to prevent cross-nibble bit bleed"
    - "Deterministic multi-row hashing via a single FNV-1a primitive called with 4 distinct seed constants — avoids Caffeine's spread/rehash chain (02-01 §6 row 6)"

key-files:
  created:
    - "include/count_min_sketch.h (142 lines)"
  modified: []

key-decisions:
  - "Update rule is CONSERVATIVE — locked unconditionally by REQUIREMENTS.md WTLFU-01 and ROADMAP.md §Phase 2 success-criterion 1. STANDARD update code path is absent from the file entirely (not commented, not #if 0'd, just absent). This is a deliberate deviation from Caffeine v3.1.8 FrequencySketch.increment which uses STANDARD (bitwise-OR of four incrementAt calls, FrequencySketch.java:L161-L164); see 02-01-CAFFEINE-NOTES.md §6 row 1 for the full rationale."
  - "sample_size = 10 * width * DEPTH (= 40 * width) per CONTEXT.md L-5 / D-03 — deliberately 4× larger than Caffeine's 10 * maximumSize. Rationale: follows Einziger-Friedman counter-operation convention; revisitable in 02-06 if validation under-performs."
  - "Halving is a full reset of sample_count_ to 0 (not Caffeine's (size - count/4) / 2 lost-count compensation) per CONTEXT.md D-10 — simplification acceptable at our 20K–100K trace scale."
  - "force_age() is public (test-only per D-10) — exposes direct halving trigger so tests/test_wtinylfu.cpp (02-04) can verify aging deterministically without pumping 10*W records through record()."

patterns-established:
  - "CMS consumption pattern for WTinyLFUCache: construct with n_objects_hint = capacity_bytes / avg_object_size (D-02); call record() on every key access; call estimate() inside admission test; call reset() from outer reset()."
  - "Nibble-packed counter access via get_counter(row, col) / set_counter(row, col, val) helpers — keeps the update loop readable and confines bit-twiddling to two 3-line functions."

requirements-completed: [WTLFU-01]

# Metrics
duration: 2min
completed: 2026-04-19
---

# Phase 2 Plan 02: Count-Min Sketch Summary

**Header-only 4-bit packed CMS (depth=4, width=nextpow2(hint)) with FNV-1a seeded rows and CONSERVATIVE update locked by WTLFU-01, ready for consumption by W-TinyLFU in 02-03.**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-19T03:41:34Z
- **Completed:** 2026-04-19T03:43:03Z
- **Tasks:** 1 / 1
- **Files modified:** 1 (created)

## Accomplishments
- New `include/count_min_sketch.h` — 142 lines, header-only, compiles clean under `-std=c++17 -Wall -Wextra -Werror`
- All five locked CMS parameters implemented per CAFFEINE-NOTES §7: counter width 4 bits, depth 4, width nextpow2(n_objects_hint), halving trigger `sample_count_ >= sample_size_`, sample_size `10 * width * DEPTH`
- FNV-1a seeded hashing wired to the four pre-pinned seeds from Phase 1's `hash_util.h` — one FNV-1a call per row
- Full D-10 API surface present: `record(key)`, `estimate(key) const`, `reset()`, `force_age()`, plus `width()` / `sample_size()` / `sample_count()` accessors
- Full project `make all` clean rebuild: no warnings, no errors

## Task Commits

1. **Task 1: Write include/count_min_sketch.h with 4-bit packed counters, FNV-1a seeded rows, CONSERVATIVE update, periodic halving** — `ee6edf7` (feat)

## Files Created/Modified
- `include/count_min_sketch.h` — 4-bit packed CMS (depth=4, nextpow2 width); FNV-1a with `FNV_SEED_A..D`; CONSERVATIVE update (WTLFU-01 locked); periodic halving every `10 * width * DEPTH` records; halving mask `0x77` for nibble-independent shift; nibble-packed counter accessors; test-only `force_age()` hook

## Decisions Made

### CONSERVATIVE update — locked by WTLFU-01, confirmed to deviate from Caffeine

Caffeine v3.1.8's `FrequencySketch.increment` (pulled in Plan 02-01, documented at
`.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` §2) uses **STANDARD update**:
all four counters are incremented unconditionally via a bitwise `|` of four `incrementAt`
calls (suppressed short-circuit) at `FrequencySketch.java:L161-L164`.

Our port uses **CONSERVATIVE update** — only counters whose value equals the current
row-minimum are incremented, capping at 15. This is mandated unconditionally by
`REQUIREMENTS.md` WTLFU-01 (line 42: "conservative update") and `ROADMAP.md` §Phase 2
Success Criterion 1. The requirement is authoritative; the Caffeine notes are reference
material.

**This deviation is the first row in CAFFEINE-NOTES §6** and is preserved in this plan
exactly as 02-01 recorded it. No change to §6 was needed during this plan; the checker
in the revision cycle already verified §6 row 1 is populated.

### sample_size formula

`sample_size_ = 10ULL * width_ * DEPTH` (i.e., `40 * width` for the locked DEPTH=4)
per CONTEXT.md L-5 / D-03. Caffeine uses `10 * maximumSize` (FrequencySketch.java:L96),
so our halving cadence is **4× slower** than Caffeine's for the same logical capacity.
This is CAFFEINE-NOTES §6 row 2 and is a deliberate deviation — justified by the paper's
counter-operation-unit convention. Revisitable in 02-06 if W-TinyLFU under-performs.

### Full build remains clean

`make clean && make all` completes without warnings or errors. The new header is not yet
included by `cache.h` (that wiring happens in 02-03), so this run is purely a regression
guard. Confirmed: the rebuild compiled all four translation units and linked `cache_sim`
with zero diagnostics.

## Deviations from Plan

None — plan executed exactly as written. Task 1's action steps were followed verbatim,
the compile check passed on the first attempt, and every acceptance-criteria grep passed
without modification.

## Issues Encountered

None.

## Self-Check

- `include/count_min_sketch.h`: FOUND (142 lines; min_lines=80 satisfied)
- Commit `ee6edf7`: FOUND in `git log --oneline --all`
- All 20 acceptance-criteria greps: PASSED (see task execution log)
- Standalone compile `g++ -std=c++17 -Wall -Wextra -Werror -Iinclude -fsyntax-only -x c++ /dev/null -include count_min_sketch.h`: exit 0, zero diagnostics
- Full project `make clean && make all`: exit 0, zero warnings, zero errors

## Self-Check: PASSED

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `include/count_min_sketch.h` is ready to be `#include`d by `include/wtinylfu.h` in plan 02-03
- Public API matches the contract `tests/test_wtinylfu.cpp` (02-04) will drive: `record()` → `estimate()` → `force_age()` → `estimate()` → `reset()`
- WTLFU-01 ("4-bit counters, depth=4, width=nextpow2(capacity-objects), conservative update, periodic halving every 10×W accesses") is **fully satisfied** — verifiable by grep for the conservative update block and absence of any STANDARD update path
- No blockers for 02-03. W-TinyLFU can construct `CountMinSketch(n_objects_hint)` in its ctor and call through the D-10 API directly.

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-19*
