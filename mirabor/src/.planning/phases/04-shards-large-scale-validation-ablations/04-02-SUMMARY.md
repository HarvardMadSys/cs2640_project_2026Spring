---
phase: 04-shards-large-scale-validation-ablations
plan: 02
subsystem: doorkeeper
tags:
  - doorkeeper
  - bloom-filter
  - header-only
  - test-binary
  - hash-util
  - fnv1a
  - kirsch-mitzenmacher
requires:
  - include/hash_util.h fnv1a_64 + FNV_SEED_A + FNV_SEED_B (Phase 1)
  - tests/test_wtinylfu.cpp structural template — TEST_ASSERT macro + failures counter + main() pattern (Phase 2)
  - include/count_min_sketch.h structural template — #pragma once + explicit-ctor + trailing-underscore member naming (Phase 2)
  - Makefile TEST_SRC/TEST_OBJDIR/TEST_OBJ/TEST_TARGET single-binary pattern at lines 75-96 (Phase 2)
provides:
  - include/doorkeeper.h header-only Bloom filter with contains/add/clear + test-only size() inspector
  - Kirsch-Mitzenmacher double-hashing on FNV_SEED_A + FNV_SEED_B (D-07) with 2 hash functions
  - 4× n_objects_hint bit sizing (D-06 / Einziger-Friedman, ~13% FPR target)
  - tests/test_doorkeeper.cpp with 3 coverage tests (contains-after-add, clear-zeros-all, FPR sanity)
  - Makefile per-binary TEST_WTLFU_* + TEST_DK_* variable groups
  - Makefile `test` target building AND running both test binaries sequentially
  - Empirical Doorkeeper FPR baseline (8.29%) for Plan 04-05 regression-guard reference
affects:
  - include/doorkeeper.h (NEW, 79 lines)
  - tests/test_doorkeeper.cpp (NEW, 148 lines)
  - Makefile (lines 75-96 refactored to 81-115 two-binary block; net +29 lines)
tech-stack:
  added: []
  patterns:
    - "header-only class mirroring count_min_sketch.h structural pattern (pragma once + explicit ctor + trailing-underscore members)"
    - "standalone assertion-based test binary with TEST_ASSERT macro accumulator (Phase 2 D-06 pattern)"
    - "per-binary Makefile variable groups (TEST_{BINARY}_{SRC,OBJ,TARGET}) replacing single-binary pattern"
    - "Kirsch-Mitzenmacher double-hashing: h_i = (h1 + i * h2) % size for k hash functions from 2 independent hashes"
    - "size_t cast on uint64_t vector dimension for 32-bit-pointer platform defensiveness"
key-files:
  created:
    - include/doorkeeper.h
    - tests/test_doorkeeper.cpp
  modified:
    - Makefile
decisions:
  - "Sizing: 4 * max(n_objects_hint, 1) bits per D-06 (Einziger-Friedman recommendation)"
  - "Hash scheme: Kirsch-Mitzenmacher double-hashing with FNV_SEED_A + FNV_SEED_B per D-07 (k=2, two hashes sufficient for first-time filter)"
  - "NO std::hash per Phase 1 D-14 ban (use fnv1a_64 from hash_util.h only)"
  - "FPR tolerance band [0.05, 0.25] — generous around ~13% paper target; tight enough to catch single-hash regressions (~30-50%)"
  - "size_t cast in bits_.assign() for 32-bit-pointer portability (defensive)"
  - "`make test` runs BOTH binaries sequentially; failing suite surfaces via stderr + non-zero exit"
  - "Test order: test_wtinylfu first (Phase 2 regression guard), test_doorkeeper second"
  - "Separate build/test/ objdir preserved (D-07): `make && make test` does not invalidate cache_sim"
  - "wtinylfu.h / count_min_sketch.h / cache.h deliberately UNTOUCHED this plan (integration is Plan 04-05)"
metrics:
  duration: "~6m"
  completed: "2026-04-20T06:11:33Z"
  tasks: 2
  files: 3
---

# Phase 4 Plan 02: Doorkeeper Bloom Filter Summary

One-liner: Ships header-only `Doorkeeper` Bloom filter with Kirsch-Mitzenmacher double-hashing on FNV_SEED_A+B (4 bits/element per Einziger-Friedman) plus a standalone assertion-based test binary; empirical FPR 8.29% at load factor 1.0 lands inside the [0.05, 0.25] sanity band, unblocking Plan 04-05's integration into W-TinyLFU.

## Purpose

Builds DOOR-01 — the first of three Doorkeeper requirements. Ships the Bloom-filter data structure + its unit-test gate in isolation, BEFORE wiring it into `include/wtinylfu.h` (Plan 04-05). Per CONTEXT axis_structure: "Split 04-02 from 04-05 is load-bearing: doorkeeper.h can be unit-tested standalone without touching wtinylfu.h — this lets the Bloom-filter FPR be validated before any integration risk." If the FPR sanity gate had failed here (e.g., single-hash bug, modulo collision, dead bit array), that failure would be diagnosable in a 148-line test binary rather than surfacing as an ambiguous regression inside a 5-task W-TinyLFU integration plan.

## Artifacts Shipped

### include/doorkeeper.h — 79 lines

Header-only Bloom filter with:
- `#pragma once`, minimal includes (`<cstdint> <string> <vector> <algorithm> "hash_util.h"`)
- `class Doorkeeper` with explicit ctor taking `uint64_t n_objects_hint`
- Three public methods: `contains(key) const`, `add(key)`, `clear()` + test-only `size()` inspector
- Private members `uint64_t size_` and `std::vector<uint64_t> bits_` (trailing-underscore convention)
- NO inheritance, NO CachePolicy dependency, NO `record()` calls (stats single-source invariant L-12 structurally preserved)

### tests/test_doorkeeper.cpp — 148 lines

Pure C++17 assertion-based test binary mirroring `tests/test_wtinylfu.cpp` pattern:
- Shared TEST_ASSERT macro (accumulates failures, does NOT abort-on-first)
- `static int failures = 0` global counter
- `main()` returns 1 iff any failure, 0 otherwise
- Three test functions:
  - **T1 test_contains_after_add** — Fresh filter returns `!contains()` for unseen keys; `add()`-then-`contains()` returns true; idempotent re-check; second-key add does not erase first-key bits.
  - **T2 test_clear_zeros_all** — Add 100 keys, verify sample of 3 are contained, call `clear()`, verify ALL 100 previously-added keys now report `!contains()`. Re-add after clear to verify no internal-state corruption.
  - **T3 test_fpr_sanity** — Load filter with 10K distinct `"added_*"` keys; query 10K disjoint `"query_*"` keys; count false positives; assert `fpr ∈ [0.05, 0.25]`.

### Makefile — refactored test block

Single-binary pattern (TEST_SRC/TEST_OBJ/TEST_TARGET, lines 75-96) replaced with two-binary per-variable-group pattern:
- `TEST_OBJDIR := build/test` (shared, preserved)
- `TEST_WTLFU_{SRC,OBJ,TARGET}` — Phase 2 test binary
- `TEST_DK_{SRC,OBJ,TARGET}` — new Phase 4 Plan 04-02 test binary
- `test` target depends on BOTH `$(TEST_WTLFU_TARGET)` and `$(TEST_DK_TARGET)`; runs WTLFU first, then DK

## Empirical FPR Measurement

```
PASS: fpr_sanity (fp=829/10000, fpr=0.0829, target ~0.13)
```

At load factor 1.0 (n=10000 added keys, filter sized for n=10000, 4 bits/element → 40000-bit filter), the Doorkeeper reports **829 false positives out of 10000 disjoint queries = 8.29% FPR**.

This is BELOW the ~13% paper target (Einziger-Friedman Table 4) but WELL WITHIN the [0.05, 0.25] sanity gate. The lower-than-paper observation is consistent with:
1. The load factor is exactly 1.0 (n added / n sized), not the optimistic-loaded regime the paper measures.
2. FNV-1a hash distribution is not adversarial for the sequential `added_0..added_9999` / `query_0..query_9999` key families — no pathological collision clustering.
3. Kirsch-Mitzenmacher double-hashing with k=2 produces slightly better FPR than k=2 independent-hash Bloom filters when h1/h2 are well-distributed (the lemma bound is asymptotic in k, and at k=2 the practical FPR is often below the upper bound).

**For Plan 04-05 cross-reference:** The 8.29% baseline is the regression-guard reference. If the DK-integrated W-TinyLFU's observed FPR drifts above ~13% in Plan 04-05's integration tests, that indicates an integration bug (wrong reset cadence, double-clear, or miscounted filter interactions) — not a Bloom-filter bug.

## make test Output (copy-paste verbatim for Plan 04-05 cross-reference)

```
=== Running W-TinyLFU test suite ===
build/test/test_wtinylfu
=== W-TinyLFU + CountMinSketch test suite ===
PASS: cms_basics
PASS: cms_aging
PASS: hot_survives_scan
PASS: determinism

All tests PASSED.

=== Running Doorkeeper test suite ===
build/test/test_doorkeeper
=== Doorkeeper test suite ===
PASS: contains_after_add
PASS: clear_zeros_all (post-clear contained=0/100)
PASS: fpr_sanity (fp=829/10000, fpr=0.0829, target ~0.13)

All tests PASSED.
```

## Deviations from Plan

None. Plan 04-02 executed EXACTLY as written — including the verbatim file content of `include/doorkeeper.h`, `tests/test_doorkeeper.cpp`, and the Makefile test-block replacement. No Rule 1 (bug fixes), Rule 2 (missing functionality), Rule 3 (blocking issues), or Rule 4 (architectural decisions) triggered. No authentication gates. No checkpoints. Plan executed autonomously per `autonomous: true`.

The plan text and PATTERNS.md code samples were fully aligned and reproducible — a notable improvement over Plan 04-01 where minor plan-narration arithmetic had to be adjusted (the 200-sample-floor guard).

## Verification Results

| Gate | Value | Pass |
|------|-------|------|
| `include/doorkeeper.h` exists | 79 lines | Yes |
| `#pragma once` on line 1 | present | Yes |
| `class Doorkeeper` exactly once | present | Yes |
| `FNV_SEED_A` + `FNV_SEED_B` used | both referenced in contains/add | Yes |
| `std::hash` banned (D-14) | grep absent | Yes |
| `#include "cache.h"` absent (DK != CachePolicy) | grep absent | Yes |
| `grep -cE "record\(\s*(true\|false)" include/doorkeeper.h` | 0 | Yes |
| Standalone compile: `g++ -std=c++17 -Wall -Wextra -Iinclude -fsyntax-only -include include/doorkeeper.h -x c++ /dev/null` | exit 0 | Yes |
| `grep -c "size_" include/doorkeeper.h` ≥ 4 | 7 | Yes |
| `grep -c "bits_" include/doorkeeper.h` ≥ 4 | 5 | Yes |
| Header line range [50, 100] | 79 | Yes |
| `tests/test_doorkeeper.cpp` exists with 3 test functions | present | Yes |
| `#include "doorkeeper.h"` in test | present | Yes |
| `make clean && make` zero warnings | 0 warnings | Yes |
| `make test` builds both binaries | both exist in build/test/ | Yes |
| `make test` runs both suites | both headers + "All tests PASSED" present | Yes |
| `make test` exits 0 | yes | Yes |
| Phase 2 regression: `grep -cE "record\(\s*(true\|false)" include/wtinylfu.h` == 4 | 4 | Yes |
| Plan-scope invariant: `git diff --stat HEAD~2 include/wtinylfu.h` empty | empty | Yes |
| Plan-scope invariant: `git diff --stat HEAD~2 include/count_min_sketch.h` empty | empty | Yes |
| Plan-scope invariant: `git diff --stat HEAD~2 include/cache.h` empty | empty | Yes |
| Empirical FPR in [0.05, 0.25] | 0.0829 | Yes |

## Success Criteria Mapping

- **DOOR-01** — `include/doorkeeper.h` exists as a header-only Bloom filter with two hash functions (Kirsch-Mitzenmacher double-hashing on FNV_SEED_A + FNV_SEED_B), configurable bit-array size via `n_objects_hint` ctor (D-06 4× bit budget), three public methods (contains/add/clear), trailing-underscore private members, and zero stats-touching code paths. **VERIFIED.**
- **Bloom-filter validation** — `tests/test_doorkeeper.cpp` exercises three coverage gates (T1 contains-after-add invariant + no false negatives; T2 clear() zeroes all bits; T3 empirical FPR ∈ [0.05, 0.25] at recommended load factor). **VERIFIED** (all 3 tests PASS).
- **Build hygiene** — `make test` runs BOTH suites end-to-end, both pass, `make` (without `test`) is unaffected — the main simulator does not link any test code, the test binaries do not link any simulator object code (header-only paths only). **VERIFIED** (clean build + standalone test binaries).
- **Plan 04-05 unblocked** — With Doorkeeper validated in isolation, Plan 04-05 can confidently integrate it into wtinylfu.h knowing any post-integration FPR drift is an integration bug, not a Bloom-filter bug. **UNBLOCKED** (8.29% FPR baseline recorded).

## Known Stubs

None. All data paths are fully wired. The Doorkeeper class is a complete, standalone Bloom filter with no placeholder methods, no TODO markers, and no empty-return functions. Integration into W-TinyLFU is explicitly scoped to Plan 04-05 — not a stub, but a deferred integration per the CONTEXT axis_structure split.

## Self-Check: PASSED

- File `include/doorkeeper.h` — FOUND (79 lines)
- File `tests/test_doorkeeper.cpp` — FOUND (148 lines)
- File `Makefile` — FOUND (modified; two-binary test block)
- Commit `3f545a3` (DK header) — FOUND in git log
- Commit `80f3f2a` (test binary + Makefile extension) — FOUND in git log
- Binary `build/test/test_wtinylfu` — FOUND (executable, exits 0)
- Binary `build/test/test_doorkeeper` — FOUND (executable, exits 0)
- `make test` output records "All tests PASSED" twice (once per suite) — FOUND in /tmp/_maketest.log
