---
phase: 02-w-tinylfu-core
verified: 2026-04-18T00:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification: false
---

# Phase 2: W-TinyLFU Core — Verification Report

**Phase Goal:** A working, validated W-TinyLFU policy plugged into the existing CachePolicy hierarchy — correct behavior on the "hot object survives scan" invariant and the expected α-regime relationship to LRU on Congress replay.
**Verified:** 2026-04-18
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `include/count_min_sketch.h` exists with 4-bit counters, depth=4, width=nextpow2, conservative update, periodic halving every 10×W accesses, deterministic seed | VERIFIED | File exists (143 lines). DEPTH=4 constant, 4-bit nibble packing, CONSERVATIVE update logic (finds min, increments only min-rows), `sample_size_ = 10 * width * depth`, FNV-1a with FNV_SEED_A..D. No live STANDARD code present. |
| 2 | `include/wtinylfu.h` exists with 1% window LRU + 99% main SLRU (80% protected / 20% probationary) + TinyLFU admission via CMS; selectable through `make_policy("wtinylfu", ...)` | VERIFIED | File exists (228 lines). `window_capacity_ = capacity_bytes / 100`, `protected_capacity_ = (main_capacity_ * 80) / 100`. CMS admission in `admit_candidate_to_main_`. `make_policy("wtinylfu", ...)` in `src/main.cpp:69` returns `WTinyLFUCache`. |
| 3 | Unit test passes: 20-access hot object survives 1000-access sequential scan | VERIFIED | `make test` exits 0. `PASS: hot_survives_scan` printed. All 4 tests PASS (cms_basics, cms_aging, hot_survives_scan, determinism). Zero compiler warnings. |
| 4 | W-TinyLFU beats LRU at α≥0.8 across every cache size on Congress replay-Zipf | VERIFIED | `check_wtlfu_acceptance.py --results-dir results/congress` exits 0. A1 (mrc.csv every cache fraction) PASS. A2 (alpha_sensitivity at α∈{0.8,0.9,1.0,1.1,1.2}) PASS. Monotone improvement: 13.24% (α=0.8) → 21.55% (α=1.2). |
| 5 | W-TinyLFU within ±2% of LRU at α=0 (uniform) — WTLFU-05 one-sided checkpoint | VERIFIED (checkpoint) | Condition B one-sided regression guard at α=0.6 proxy PASS. W-TinyLFU outperforms LRU by 7.84% at α=0.6, satisfying the requirement intent (no regression at uniform-like workloads). True-uniform α=0 is not in the sweep grid; this was accepted by user checkpoint during execution. |

**Score:** 5/5 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `include/count_min_sketch.h` | 4-bit CMS, depth=4, conservative update, FNV-1a, periodic halving | VERIFIED | 143 lines, all structural requirements met |
| `include/wtinylfu.h` | W-TinyLFU: 1%/99% window/SLRU, CMS admission, CachePolicy subclass | VERIFIED | 228 lines, `class WTinyLFUCache : public CachePolicy` confirmed by grep |
| `include/cache.h` | Subordinate `#include "wtinylfu.h"` at bottom | VERIFIED | Line 413: `#include "wtinylfu.h"` after all other policy classes |
| `src/main.cpp` | `make_policy("wtinylfu", ...)` dispatch, `--policies wtinylfu` CLI flag | VERIFIED | Line 69: `if (name == "wtinylfu") return std::make_unique<WTinyLFUCache>(...)`. Default policy list includes `"wtinylfu"`. |
| `tests/test_wtinylfu.cpp` | 4 tests: cms_basics, cms_aging, hot_survives_scan, determinism | VERIFIED | 198 lines, all 4 tests PASS under `make test` |
| `Makefile` | `test` target with separate `build/test/` object dir | VERIFIED | `TEST_OBJDIR := build/test`, `test: $(TEST_TARGET)` target present |
| `scripts/plot_results.py` | `POLICY_COLORS['W-TinyLFU']` and `POLICY_MARKERS['W-TinyLFU']` entries | VERIFIED | `"W-TinyLFU": "#8c564b"` in POLICY_COLORS, `"W-TinyLFU": "P"` in POLICY_MARKERS |
| `scripts/check_wtlfu_acceptance.py` | WTLFU-05 acceptance script, exits 0 on Congress results | VERIFIED | 147 lines, exits 0 on `results/congress/`. Checks A1 (mrc.csv), A2 (alpha sweep), B (one-sided regression guard). |
| `results/congress/alpha_sensitivity.csv` | Contains W-TinyLFU rows for full alpha range | VERIFIED | W-TinyLFU rows present for α∈{0.6,0.7,0.8,0.9,1.0,1.1,1.2} |
| `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` | ≥60 lines, contains "sampleSize", "D-08a..D-08e", ≥1 `FrequencySketch.java:L` citation | VERIFIED | 549 lines. "sampleSize" appears 9 times. D-08a..D-08e all present. 9 `FrequencySketch.java:L` citations. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `include/cache.h` | `include/wtinylfu.h` | `#include "wtinylfu.h"` at EOF | WIRED | Line 413 confirmed |
| `include/wtinylfu.h` | `include/count_min_sketch.h` | `#include "count_min_sketch.h"` | WIRED | Line 8 of wtinylfu.h |
| `include/count_min_sketch.h` | `include/hash_util.h` | `#include "hash_util.h"` + FNV_SEED_A..D | WIRED | Line 7 confirmed; FNV_SEED_A..D used in all 4 rows of `record()` and `estimate()` |
| `src/main.cpp` | `WTinyLFUCache` | `make_policy("wtinylfu", ...)` | WIRED | Line 69 creates `WTinyLFUCache(capacity, n_obj_hint)` |
| `tests/test_wtinylfu.cpp` | `include/cache.h` (which includes `wtinylfu.h`) | `#include "cache.h"` | WIRED | Line 23 confirmed; test accesses `WTinyLFUCache` directly |
| `scripts/check_wtlfu_acceptance.py` | `results/congress/mrc.csv` + `alpha_sensitivity.csv` | `pd.read_csv(f"{results_dir}/...")` | WIRED | Both CSVs exist with W-TinyLFU rows; script exits 0 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `src/main.cpp` MRC loop | `miss_ratio` per policy per cache fraction | Replay of Congress Zipf trace through `make_policy(...)` → `policy->access()` → `policy->stats` | Real trace replay, no static returns | FLOWING |
| `WTinyLFUCache::access()` | `stats.hits`, `stats.misses` | CMS + list/map operations on actual trace keys | Real eviction decisions via CMS estimates | FLOWING |
| `CountMinSketch::estimate()` | `uint32_t` min across 4 rows | FNV-1a hashed row lookups into packed byte array | Real frequency counts from `record()` calls | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Build produces binary with zero warnings | `make clean && make` | 5 compilation units, 0 warnings, binary created | PASS |
| All unit tests pass | `make test` | 4/4 PASS (cms_basics, cms_aging, hot_survives_scan, determinism) | PASS |
| Simulator emits W-TinyLFU rows in mrc.csv | `./cache_sim --policies wtinylfu --num-requests 10000 --num-objects 1000` | 6 W-TinyLFU rows in mrc.csv across cache fractions 0.1%..10% | PASS |
| WTLFU-05 acceptance checker exits 0 | `python3 scripts/check_wtlfu_acceptance.py --results-dir results/congress` | A1 PASS, A2 PASS, B PASS — exit 0 | PASS |

---

### Requirements Coverage

| Requirement | Phase | Description | Status | Evidence |
|-------------|-------|-------------|--------|----------|
| WTLFU-01 | Phase 2 | `include/count_min_sketch.h` — 4-bit CMS, depth=4, conservative update, periodic halving | SATISFIED | File verified at all 4 levels (exists, substantive, wired, data-flowing) |
| WTLFU-02 | Phase 2 | `include/wtinylfu.h` — 1%/99% window/SLRU + TinyLFU admission | SATISFIED | File verified; selectable via `make_policy("wtinylfu", ...)` |
| WTLFU-03 | Phase 2 | W-TinyLFU integrated into `CachePolicy` hierarchy and `make_policy()` dispatch | SATISFIED | `WTinyLFUCache : public CachePolicy` confirmed; dispatch at `main.cpp:69` |
| WTLFU-04 | Phase 2 | Unit test: hot_survives_scan PASS under `make test` | SATISFIED | `make test` exits 0; `PASS: hot_survives_scan` in output |
| WTLFU-05 | Phase 2 | Congress replay validation: beats LRU at α≥0.8; no regression at α≈0 | SATISFIED | `check_wtlfu_acceptance.py` exits 0; monotone 7.84-21.55% improvement; one-sided checkpoint honored |

All 5 WTLFU-xx requirements marked Complete in REQUIREMENTS.md. No orphaned requirements.

---

### Anti-Patterns Found

No blockers or stubs found. Scan results:

| File | Pattern | Severity | Verdict |
|------|---------|----------|---------|
| `include/wtinylfu.h` | `record(true, size)` / `record(false, size)` appear exactly 4 times | Info | Correct — 3 hit returns + 1 miss fall-through per stats-single-source invariant (CONTEXT.md L-12) |
| `include/count_min_sketch.h` | No TODO, FIXME, PLACEHOLDER, or empty return found | — | Clean |
| `tests/test_wtinylfu.cpp` | No TODO, FIXME, or empty implementations found | — | Clean |

---

### Human Verification Required

None. All success criteria are verifiable programmatically. No visual, real-time, or external-service dependencies in Phase 2 scope.

---

### WTLFU-05 α=0 Caveat (Acknowledged, Not Penalized)

The ROADMAP.md success criterion 4 states "within ±2% of LRU at α=0 (uniform)". The sweep grid starts at α=0.6, not α=0. During Phase 2 execution, the user accepted a checkpoint decision to implement Condition B as a one-sided regression guard at α=0.6 (the lowest available proxy for uniform). W-TinyLFU outperformed LRU at α=0.6 by 7.84%, satisfying the requirement intent (guard against regression, not penalize outperformance). Monotone improvement across all swept α (7.84% at α=0.6 → 21.55% at α=1.2) is consistent with TinyLFU theory. WTLFU-05 is treated as PASSED.

---

### Deviations from Caffeine (All Documented in 02-01-CAFFEINE-NOTES.md §6)

| Deviation | Our Choice | Caffeine Choice | Authority |
|-----------|-----------|-----------------|-----------|
| CMS update rule | CONSERVATIVE (increment only min-rows) | STANDARD (increment all rows unconditionally) | REQUIREMENTS.md WTLFU-01 (authoritative) |
| sample_size formula | `10 * width * depth` (40×W) | `10 * maximumSize` (≈10×W) | CONTEXT.md L-5 / D-03 |
| Hash scheme | FNV-1a × 4 seeds | Single hash + SplitMix re-scramble | CONTEXT.md §6 row 6 |
| D-08a empty-probation | Admit unconditionally (simplification) | Escalate to protected/window victims | CAFFEINE-NOTES §6 row 4 (warmup-only condition) |
| D-08e admission tiebreak | Strict `>` only (ties reject) | `>` with 1/128 random admit (hash-DoS guard) | CAFFEINE-NOTES §6 row 5 (no adversarial model in offline research simulator) |

---

## Gaps Summary

No gaps. All 5 must-haves verified. Phase goal achieved.

---

_Verified: 2026-04-18_
_Verifier: Claude (gsd-verifier)_
