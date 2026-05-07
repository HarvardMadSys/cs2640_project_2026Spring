---
phase: 01-enabling-refactors-courtlistener-pilot
plan: 01
subsystem: infra
tags: [cpp17, fnv-1a, hashing, refactor, determinism]

# Dependency graph
requires: []
provides:
  - "include/hash_util.h — shared FNV-1a 64-bit hasher with 4 deterministic seeds"
  - "hash_util_self_test() startup gate in main() aborting on FNV regression"
  - "SHARDS::hash_key delegates to fnv1a_64 (behavior-preserving)"
affects:
  - 01-02-replay-zipf-refactor (sibling, no dependency)
  - 01-03-throughput-measurement (sibling, no dependency)
  - 02-wtinylfu (CMS consumes FNV_SEED_A..D)
  - 02-count-min-sketch (consumes fnv1a_64 + 4 seeds)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Shared inline C++ utility header (#pragma once, constexpr constants, inline functions)"
    - "Startup invariant self-test gate (prints stderr + return 1 on failure)"
    - "constexpr uint64_t at namespace scope for hash constants (no #define, per CONVENTIONS.md)"

key-files:
  created:
    - "include/hash_util.h"
  modified:
    - "src/shards.cpp"
    - "src/main.cpp"

key-decisions:
  - "Picked 0x9e3779b97f4a7c15 / 0xbf58476d1ce4e5b9 / 0x94d049bb133111eb / 0xda942042e4dd58b5 as the 4 FNV seeds (splitmix64/murmur3 finalizer constants, golden-ratio-derived, pairwise distinct)"
  - "FNV_SEED_A/B/C/D declared at namespace scope as constexpr uint64_t (not macro, not static) so Phase 2 CMS can take their address if needed"
  - "hash_util_self_test uses fnv1a_64(\"hello\") == 0xa430d84680aabd0b as the canonical FNV-1a-64 test vector"
  - "No namespace wrapper — matches existing bare-global convention in cache.h, trace_gen.h, shards.h"
  - "Include ordering in shards.cpp: project headers stay first, then stdlib, then new hash_util.h at the end (matches existing main.cpp ordering per 01-PATTERNS.md)"

patterns-established:
  - "hash_util.h as the canonical home for deterministic hashing primitives (Phase 2 CMS + WTinyLFU will consume)"
  - "Startup self-test gate for correctness-critical inline functions"

requirements-completed: [REFACTOR-01]

# Metrics
duration: 2min 25s
completed: 2026-04-19
---

# Phase 01 Plan 01: Shared FNV-1a Hash Utility Summary

**Extracted the FNV-1a 64-bit hash from `src/shards.cpp` into `include/hash_util.h` with FNV_BASIS/FNV_PRIME + 4 golden-ratio-derived seeds + a startup self-test, and pointed `SHARDS::hash_key` at the shared implementation — unblocking Phase 2's W-TinyLFU Count-Min Sketch.**

## Performance

- **Duration:** 2 min 25 s
- **Started:** 2026-04-19T01:09:22Z
- **Completed:** 2026-04-19T01:11:47Z
- **Tasks:** 3
- **Files modified:** 3 (1 created, 2 edited)

## Accomplishments

- New `include/hash_util.h` (37 lines) exposing: `FNV_BASIS`, `FNV_PRIME`, `FNV_SEED_A..D` (all `constexpr uint64_t`), `fnv1a_64(const std::string&, uint64_t seed = FNV_BASIS)`, and `hash_util_self_test()`.
- `src/shards.cpp` no longer contains the FNV-1a magic literals `14695981039346656037ULL` or `1099511628211ULL`; `SHARDS::hash_key` is now a one-liner delegating to `fnv1a_64(key)`.
- `src/main.cpp` invokes `hash_util_self_test()` at startup between argument parsing and the simulator banner; on failure it prints `hash_util self-test failed — aborting` to stderr and returns 1.
- Project-wide ban on `std::hash<std::string>` enforced — `grep -rn 'std::hash<' src/ include/` returns zero actual uses (only a single comment citation inside `hash_util.h` that documents the ban).
- `make clean && make` builds cleanly under `-Wall -Wextra` with no warnings.
- 1,000-request synthetic smoke run (`./cache_sim --num-requests 1000 --num-objects 100 --output-dir /tmp/hash_check`) exits 0, no `self-test failed` in log.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create include/hash_util.h with FNV-1a, 4 seeds, and self-test** — `aea2296` (feat)
2. **Task 2: Replace local FNV-1a in src/shards.cpp with include/hash_util.h** — `87facf5` (refactor)
3. **Task 3: Wire hash_util_self_test() into src/main.cpp startup + confirm build** — `978c76e` (feat)

_Plan metadata commit (SUMMARY.md) is made by the orchestrator after this worktree merges back._

## Exact contents of `include/hash_util.h` as shipped

37 lines, no `#define`, no `std::hash<>` instantiation. Key exports:

```cpp
constexpr uint64_t FNV_BASIS = 14695981039346656037ULL;
constexpr uint64_t FNV_PRIME = 1099511628211ULL;
constexpr uint64_t FNV_SEED_A = 0x9e3779b97f4a7c15ULL;
constexpr uint64_t FNV_SEED_B = 0xbf58476d1ce4e5b9ULL;
constexpr uint64_t FNV_SEED_C = 0x94d049bb133111ebULL;
constexpr uint64_t FNV_SEED_D = 0xda942042e4dd58b5ULL;

inline uint64_t fnv1a_64(const std::string& s, uint64_t seed = FNV_BASIS);
inline bool hash_util_self_test();  // fnv1a_64("hello") == 0xa430d84680aabd0b
```

All 6 constant declarations match the exact literal lines required by Task 1's automated grep check (one constant per line with leading `^constexpr` anchor).

## Files Created/Modified

- **`include/hash_util.h`** (created, 37 lines) — canonical deterministic hashing primitives. Single `#pragma once` + `<cstdint>` + `<string>` includes. No namespace wrapper (matches existing bare-global convention).
- **`src/shards.cpp`** (modified, +2 −7 LOC) — added `#include "hash_util.h"` at line 7 (after stdlib headers, project-header-last ordering). Replaced the 9-line FNV-1a loop in `SHARDS::hash_key` with `return fnv1a_64(key);`. Method signature preserved (`uint64_t SHARDS::hash_key(const std::string& key) const`) so `include/shards.h:46` declaration is unchanged.
- **`src/main.cpp`** (modified, +6 LOC) — added `#include "hash_util.h"` at line 15 after the other project headers. Inserted the 4-line self-test block between the arg-parsing `for` loop and the `=== Cache Policy Simulator ===` banner.

## Decisions Made

- **Seed constants:** Chose published golden-ratio/avalanche constants (`0x9e3779b97f4a7c15`, `0xbf58476d1ce4e5b9`, `0x94d049bb133111eb`, `0xda942042e4dd58b5`) — these are the splitmix64 / Murmur3 finalizer family and are standard choices for pseudo-independent hash seeds. All 4 are pairwise distinct, which is all Phase 2 CMS needs.
- **Test vector:** Used `fnv1a_64("hello") == 0xa430d84680aabd0bULL`, the published FNV-1a-64 vector. This simultaneously checks basis, prime, and 64-bit unsigned arithmetic.
- **No namespace:** Bare global symbols, matching existing `cache.h` / `shards.h` / `trace_gen.h` style. A `hashing::` namespace would have been stylistically nicer but inconsistent with the current codebase.
- **Include ordering:** Followed the `main.cpp` convention (project headers last, after stdlib) per `01-PATTERNS.md`. `shards.cpp` kept `#include "shards.h"` at line 1 (self-header always first) and slotted `hash_util.h` after the stdlib group.

## Deviations from Plan

None — plan executed exactly as written. The target-shape block in `01-PATTERNS.md` / `01-01-PLAN.md` was followed byte-for-byte for the new header. No Rule 1/2/3 auto-fixes were necessary; no Rule 4 architectural decision was hit.

## Issues Encountered

None. Build was clean on first try; the 1K smoke run passed on first execution.

## Verification Results (post-plan)

Commands run and their outcomes:

| Check | Command | Result |
|-------|---------|--------|
| V1: file exists | `test -f include/hash_util.h` | PASS |
| V1: all 6 constants + 2 inlines + no `#define` | Task 1 grep chain from plan | PASS |
| V2: FNV_BASIS literal only in hash_util.h | `grep -rn '14695981039346656037ULL' src/ include/` | 1 match only: `include/hash_util.h:10` |
| V3: no `std::hash<>` uses | `grep -rn 'std::hash<' src/ include/` | 1 match, comment-only (documentation of the ban inside `hash_util.h`) |
| V4: clean build | `make clean && make` | PASS, no warnings under `-Wall -Wextra` |
| V5: 1K smoke exit 0 | `./cache_sim --num-requests 1000 --num-objects 100 --output-dir /tmp/hash_check` | exit 0, no `self-test failed` in `/tmp/hash_check.log` |
| V6: SHARDS determinism | Ran `--shards --num-requests 5000 --num-objects 500` twice into separate output dirs, `diff -q` on `shards_mrc.csv` | IDENTICAL (post-refactor runs are deterministic) |

### Byte-identity vs. pre-refactor SHARDS output

**No pre-refactor baseline CSV was captured** before this plan began (the plan's acceptance-criteria note at `01-01-PLAN.md:304` acknowledges this as an allowed fallback).

Byte-identity is nonetheless provable by inspection:

- **Old** `SHARDS::hash_key` body (src/shards.cpp:85-93 pre-refactor):
  ```cpp
  uint64_t hash = 14695981039346656037ULL;   // == FNV_BASIS
  for (char c : key) {
      hash ^= (uint64_t)(unsigned char)c;
      hash *= 1099511628211ULL;               // == FNV_PRIME
  }
  return hash;
  ```
- **New** `SHARDS::hash_key` body, via `fnv1a_64(key)` with default `seed = FNV_BASIS`:
  ```cpp
  uint64_t hash = seed;                       // seed == FNV_BASIS
  for (char c : s) {
      hash ^= (uint64_t)(unsigned char)c;
      hash *= FNV_PRIME;                      // FNV_PRIME == 1099511628211ULL
  }
  return hash;
  ```

The bodies are textually equivalent modulo identifier renaming (`FNV_BASIS` ↔ `14695981039346656037ULL`, `FNV_PRIME` ↔ `1099511628211ULL`, `key` ↔ `s`). Same unsigned 64-bit arithmetic, same iteration order, same initial value. SHARDS output is therefore byte-identical on any identical input trace. The in-repo runtime smoke run (V6 above) further confirms post-refactor SHARDS is deterministic across invocations on the same seed.

## Threat Model Mitigations Applied

- **T-01-01 (Tampering, include/hash_util.h constants — disposition: mitigate):** `hash_util_self_test()` checks `fnv1a_64("hello") == 0xa430d84680aabd0b` at program startup in `main()`. Any future edit that mistypes FNV_BASIS/FNV_PRIME or the loop body would be caught before any simulation runs. Tested: self-test passes in the 1K smoke run, error path tested via static reasoning (if the equality fails, stderr receives `hash_util self-test failed — aborting` and `main` returns 1).
- **T-01-02 (Information Disclosure via std::hash — disposition: mitigate):** Enforced via `grep -rn 'std::hash<' src/ include/` returning only a documentation comment, not a template instantiation. The ban is policy now; future violations would appear in this grep and fail the acceptance criteria.
- **T-01-03 (DoS from header ordering — disposition: accept):** Not applicable — accepted risk, Makefile's `-MMD -MP` auto-generated `hash_util.h` dependency tracking without any Makefile edit.

No new threat surface introduced (pure internal refactor, no network, no external input, no credentials).

## Next Phase Readiness

- **Phase 2 W-TinyLFU CMS** can immediately `#include "hash_util.h"` and use `fnv1a_64(key, FNV_SEED_A)` through `fnv1a_64(key, FNV_SEED_D)` as its 4 depth-rows. No additional plumbing needed.
- **Phase 2 W-TinyLFU main** gets the same `fnv1a_64` for its frequency-sketch lookups.
- **Phase 1 siblings (01-02, 01-03, 01-04, 01-05, 01-06)** do not depend on this plan's output; they can run in parallel (confirmed: this plan's `depends_on` is empty in the frontmatter).

No blockers. Plan 01-01 is complete and byte-compatible with pre-refactor SHARDS output.

## Self-Check: PASSED

File existence verified:
- `include/hash_util.h` — FOUND (created in Task 1)
- `src/shards.cpp` modifications — FOUND (Task 2)
- `src/main.cpp` modifications — FOUND (Task 3)

Commit existence verified (in current branch `worktree-agent-ae489e68`):
- `aea2296` — FOUND (`feat(01-01): add include/hash_util.h with FNV-1a and 4 seeds`)
- `87facf5` — FOUND (`refactor(01-01): delegate SHARDS::hash_key to shared fnv1a_64`)
- `978c76e` — FOUND (`feat(01-01): add hash_util self-test gate at main() startup`)

All claims in this SUMMARY are backed by commits on disk and test output captured in the Verification Results table.

---
*Phase: 01-enabling-refactors-courtlistener-pilot*
*Completed: 2026-04-19*
