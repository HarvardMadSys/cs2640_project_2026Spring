---
phase: 04-shards-large-scale-validation-ablations
plan: 05
subsystem: doorkeeper-wtinylfu-integration
tags:
  - doorkeeper
  - wtinylfu
  - ablation
  - integration
  - cms-callback
  - figures
  - phase-4-closer
requires:
  - include/doorkeeper.h standalone Bloom filter (Plan 04-02)
  - include/count_min_sketch.h halve_all_() aging hook point (Phase 2)
  - include/wtinylfu.h ctor init-list precedent (Phase 2)
  - src/main.cpp make_policy factory + policy-label maps (Plans 04-03/04 precedent)
  - scripts/plot_results.py plot_ablation_sieve linestyle-distinction template (Plan 04-04)
  - Makefile ablation-{axis} per-workload rename pattern (Plans 04-03/04)
provides:
  - CountMinSketch on_age_cb_ std::function hook fired from halve_all_() (D-09)
  - WTinyLFUCache 3rd ctor parameter `bool use_doorkeeper = false` (D-08)
  - D-05 paper-faithful pre-CMS record filter in access() when flag is true
  - WTinyLFUCache::name() returns "W-TinyLFU" or "W-TinyLFU+DK" based on flag
  - reset() extended to clear embedded Doorkeeper when flag is true
  - make_policy "wtinylfu-dk" branch + symmetric label-map entries
  - Makefile ablation-doorkeeper target running both workloads
  - phase-04 composition now covers all 4 axes (shards + 3 ablations)
  - scripts/plot_results.py plot_ablation_doorkeeper function + POLICY dict entries
  - results/{congress,court}/ablation_doorkeeper.csv (2 policies × 7 alphas each)
  - results/{congress,court}/figures/ablation_doorkeeper.pdf (2-panel figures)
affects:
  - include/count_min_sketch.h (+17 lines: <functional> include + setter + on_age_cb_ member + callback fire in halve_all_)
  - include/wtinylfu.h (+73/-8: doorkeeper.h include + 3rd ctor param + DK init + ctor body callback registration + access() pre-filter + name() ternary + reset() DK clear + 2 new private members)
  - src/main.cpp (+9 lines: 1 make_policy branch + 2 label-map mixed-case overrides)
  - Makefile (+22 lines: .PHONY extension + ablation-doorkeeper target + phase-04 dep extension)
  - scripts/plot_results.py (+87 lines: POLICY_COLORS + POLICY_MARKERS entries + plot_ablation_doorkeeper function + main() registration)
tech-stack:
  added: []
  patterns:
    - "ctor flag + init-list conditional minimum-element allocation preserves zero-overhead default path while keeping the member always validly constructed"
    - "std::function callback registration pattern: CMS exposes set_on_age_cb(); downstream cache registers a [this]-capturing lambda only when the feature flag is on; default-empty check is branch-predictable → zero overhead"
    - "pre-filter wrapping a SINGLE existing record call in if/else branches that each still emit exactly ONE record per access — preserves stats-single-source invariant without duplicate counting"
    - "linestyle-distinguished within-color variant pair: same POLICY_COLORS entry, dashed linestyle via endswith('+DK') selector — reads as 'same policy, doorkeeper on/off' in the figure"
    - "sibling variant via factory branch + shared ctor with flag — legacy policy string remains a bit-identical alias (Phase 2 back-compat preserved by default-arg convention)"
key-files:
  created:
    - results/congress/ablation_doorkeeper.csv
    - results/court/ablation_doorkeeper.csv
    - results/congress/figures/ablation_doorkeeper.pdf
    - results/court/figures/ablation_doorkeeper.pdf
    - .planning/phases/04-shards-large-scale-validation-ablations/04-05-SUMMARY.md
  modified:
    - include/count_min_sketch.h
    - include/wtinylfu.h
    - src/main.cpp
    - Makefile
    - scripts/plot_results.py
decisions:
  - "D-09 callback hook: CMS gains a std::function<void()> on_age_cb_ member fired at end of halve_all_(); default-empty std::function is a zero-overhead branch-predictable skip for baseline wtinylfu. force_age() fires the callback too (it calls halve_all_) so tests can observe DK clears deterministically. reset() deliberately does NOT fire the callback — reset is a full wipe, not an aging event."
  - "D-08 ctor flag: WTinyLFUCache ctor gains 3rd param `bool use_doorkeeper = false`. Default preserves Phase 2 bit-identity (verified via `make run-sweep` on Congress + scripts/check_wtlfu_acceptance.py exit 0). Init-list allocates doorkeeper_ with n_objects_hint bits when flag is true, minimum 1 element (64 bits) when false — always validly constructed, 64 bits of overhead on baseline is noise."
  - "D-05 paper-faithful pre-CMS filter: `if (use_doorkeeper_) { if (doorkeeper_.contains(key)) cms_.record(key); else doorkeeper_.add(key); /* SKIP cms */ } else { cms_.record(key); }` — exactly ONE cms_.record(key) call executes per access() in either branch (L-12 preserved). Admission logic (admit_candidate_to_main_) UNCHANGED per D-10 'same admission, different counting'."
  - "name() ternary: `return use_doorkeeper_ ? \"W-TinyLFU+DK\" : \"W-TinyLFU\";` — legacy policy string `wtinylfu` produces 'W-TinyLFU' label (Phase 2 bit-identical); new `wtinylfu-dk` policy string produces 'W-TinyLFU+DK' label. No delegating-ctor overload needed (unlike Plan 04-03's S3FIFOCache) — the 2 variants are fully flag-determined."
  - "Same-color-different-linestyle visual convention adopted from Plan 04-04: POLICY_COLORS['W-TinyLFU+DK'] = '#8c564b' (identical to legacy W-TinyLFU); plot_ablation_doorkeeper selects `linestyle = '--' if policy.endswith('+DK') else '-'`. Distinct marker ('X' vs legacy 'P') because the two W-TinyLFU lines cross each other on some workload×alpha combinations and same-marker reads poorly."
  - "Explicit mixed-case label-map overrides in BOTH symmetric blocks (mrc.csv + alpha_sensitivity.csv) — default toupper() would mangle 'wtinylfu-dk' → 'WTINYLFU-DK' and break the CSV/plot dict key alignment. Legacy 'wtinylfu' → 'W-TinyLFU' entry UNCHANGED."
  - "Makefile ablation-doorkeeper runs --alpha-sweep --policies wtinylfu,wtinylfu-dk twice (Congress + Court) and renames alpha_sensitivity.csv → ablation_doorkeeper.csv so ablation output is namespaced and `make run-sweep` does not clobber it. Mirrors Plans 04-03/04 pattern verbatim."
  - "Stats single-source invariant (L-12): the record-counting grep `record\\(\\s*(true|false)` returns 4 in include/wtinylfu.h (unchanged from Phase 2), 0 in include/doorkeeper.h (unchanged from Plan 04-02), 0 in include/count_min_sketch.h (unchanged from Phase 2 — CMS uses record(key), not record(true|false)). LOCKED post-integration — proves Doorkeeper didn't introduce double-counting."
metrics:
  duration: "~13m"
  completed: "2026-04-20T10:15:00Z"
  tasks: 3
  files: 9
---

# Phase 4 Plan 05: Doorkeeper × W-TinyLFU Integration Summary

One-liner: Integrates Plan 04-02's standalone Doorkeeper Bloom filter into W-TinyLFU as a sibling variant `wtinylfu-dk` gated by a 3rd ctor flag (D-08), wires a paper-faithful D-05 pre-CMS record filter in `access()` plus a CMS `on_age_cb_` hook (D-09) for synchronized DK/CMS freshness windows, and produces the DOOR-03 ablation figure on both Congress and Court workloads — closing Axis B and Phase 4 overall (5/5 plans, 8/8 requirement IDs).

## Purpose

Phase 4 ROADMAP success criterion 3: "W-TinyLFU+Doorkeeper variant gated by ctor flag, ablation figure on both workloads". Integrates the Plan 04-02 Doorkeeper into the Phase 2 W-TinyLFU access path without touching the Phase 2 WTLFU-01..05 acceptance gates. The load-bearing gate is L-12 (stats single-source invariant) — the `record\(\s*(true|false)` grep-count in wtinylfu.h must remain exactly 4 post-integration. Verified.

Implements DOOR-02 (gated variant) and DOOR-03 (ablation figure). With this plan complete, Phase 4's 8 requirement IDs (SHARDS-01/02/03, DOOR-01/02/03, ABLA-01/02) are all satisfied.

## Exact Diff Signatures

### include/count_min_sketch.h (3 coordinated additions, ~17 LOC)

1. `#include <functional>` at top (after `<cstdint>`)
2. New public method `void set_on_age_cb(std::function<void()> cb)` with move-assign into member
3. New private member `std::function<void()> on_age_cb_;` after `rows_`
4. One-line callback fire appended to `halve_all_()` after `sample_count_ = 0;`:
   ```cpp
   if (on_age_cb_) on_age_cb_();
   ```

WTLFU-01 conservative-update rule, sample_size = 10·width·depth formula, 0x77 halving mask, `force_age()` test hook — all byte-identical to Phase 2.

### include/wtinylfu.h (5 changes, +73/-8 LOC)

1. `#include "doorkeeper.h"` after `#include "count_min_sketch.h"`
2. Ctor signature extended:
   ```cpp
   WTinyLFUCache(uint64_t capacity_bytes, uint64_t n_objects_hint,
                 bool use_doorkeeper = false)
   ```
   Init list adds `use_doorkeeper_(use_doorkeeper)` and `doorkeeper_(use_doorkeeper ? std::max<uint64_t>(1, n_objects_hint) : 1)`. Ctor body registers `cms_.set_on_age_cb([this]{ doorkeeper_.clear(); })` ONLY when flag is true.
3. access() pre-CMS filter replaces the single `cms_.record(key)` at the entry:
   ```cpp
   if (use_doorkeeper_) {
       if (doorkeeper_.contains(key)) cms_.record(key);
       else doorkeeper_.add(key);  // DK miss: first-touch absorbed, skip CMS
   } else {
       cms_.record(key);  // Phase 2 baseline path — bit-identical
   }
   ```
4. `name()` returns ternary on flag: `"W-TinyLFU+DK"` or `"W-TinyLFU"`.
5. `reset()` adds `if (use_doorkeeper_) doorkeeper_.clear();` before `stats = {};`
6. Two new private members `bool use_doorkeeper_;` and `Doorkeeper doorkeeper_;` after `cms_`.

Admission helpers (`admit_candidate_to_main_`, `demote_protected_lru_to_probation_mru_`, `evict_window_if_needed_`) UNCHANGED per D-10.

### src/main.cpp (+9 LOC)

- `make_policy` gains `wtinylfu-dk` branch after the legacy `wtinylfu` branch:
  ```cpp
  if (name == "wtinylfu-dk") return std::make_unique<WTinyLFUCache>(capacity, n_objects_hint, /*use_doorkeeper=*/true);
  ```
- Both symmetric label-map blocks (mrc.csv at ~line 249 + alpha_sensitivity.csv at ~line 308) gain:
  ```cpp
  else if (pn == "wtinylfu-dk") label = "W-TinyLFU+DK";
  ```

### Makefile (+22 LOC)

- `.PHONY` gains `ablation-doorkeeper`
- New `ablation-doorkeeper` target runs the sweep twice (Congress + Court) with per-workload rename to `ablation_doorkeeper.csv`
- `phase-04` composition extended: `shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper`

### scripts/plot_results.py (+87 LOC)

- `POLICY_COLORS["W-TinyLFU+DK"] = "#8c564b"` (same brown as legacy W-TinyLFU)
- `POLICY_MARKERS["W-TinyLFU+DK"] = "X"` (distinct from legacy "P")
- `plot_ablation_doorkeeper(figures_dir, congress_dir, court_dir)` function — 2-panel figure (Congress | Court), shared y-axis, baseline-first legend sort via `endswith("+DK")` key, solid-vs-dashed linestyle per variant
- `plot_ablation_doorkeeper(figures_dir)` registered in `main()` after `plot_ablation_sieve`

## Post-Edit Grep Gates (Load-Bearing)

| Grep | File | Expected | Actual | Pass |
|------|------|----------|--------|------|
| `record\(\s*(true\|false)` | include/wtinylfu.h | 4 (L-12 invariant, unchanged from Phase 2) | **4** | Yes |
| `record\(\s*(true\|false)` | include/doorkeeper.h | 0 (DK never touches stats, unchanged from 04-02) | **0** | Yes |
| `record\(\s*(true\|false)` | include/count_min_sketch.h | 0 (CMS uses record(key), not record(hit/miss)) | **0** | Yes |
| `record\(\s*(true\|false)` | include/cache.h | 11 (unchanged from Plan 04-04 baseline) | **11** | Yes |
| `cms_.record(key)` | include/wtinylfu.h | ≥ 2 (call sites in both if/else branches — only one fires per access) | **4** (2 calls + 2 comment refs) | Yes |
| `use_doorkeeper_` | include/wtinylfu.h | ≥ 4 | **11** (member + ctor init + body check + access() check + reset() check + name() ternary + doc comment) | Yes |
| `doorkeeper_` | include/wtinylfu.h | ≥ 4 | **18** (member + include + ctor init + access() add/contains + reset() clear + doc refs + callback lambda) | Yes |
| `#include "doorkeeper.h"` | include/wtinylfu.h | 1 | **1** | Yes |
| `set_on_age_cb` | include/count_min_sketch.h | 1 | **1** | Yes |
| `on_age_cb_` | include/count_min_sketch.h | ≥ 3 (member + setter + fire) | **4** | Yes |
| `#include <functional>` | include/count_min_sketch.h | 1 | **1** | Yes |
| `wtinylfu-dk` | src/main.cpp | 3 (1 make_policy + 2 label-maps) | **3** | Yes |
| `label = "W-TinyLFU+DK"` | src/main.cpp | 2 (both symmetric blocks) | **2** | Yes |
| `ablation-doorkeeper` | Makefile | ≥ 3 (.PHONY + target + phase-04 dep) | **3** | Yes |
| `W-TinyLFU+DK` | scripts/plot_results.py | ≥ 2 (POLICY_COLORS + POLICY_MARKERS) | **7** (dicts + plot function uses) | Yes |
| `plot_ablation_doorkeeper` | scripts/plot_results.py | 2 (def + main() call) | **2** | Yes |

## Phase 2 Back-Compat Verified

`python3 scripts/check_wtlfu_acceptance.py --results-dir results/congress` exits 0:

```
=== WTLFU-05 Acceptance Check ===
A1 (mrc.csv: WTLFU < LRU at every cache fraction): PASS
A2 (alpha_sensitivity.csv: WTLFU < LRU at alpha in [0.8, 0.9, 1.0, 1.1, 1.2]): PASS
B (alpha=0.6: WTLFU regression vs LRU <= 2% — one-sided): PASS

PASS: all WTLFU-05 conditions satisfied.
```

The legacy `wtinylfu` policy (default `use_doorkeeper=false`) continues to monotonically dominate LRU across the full alpha grid. Phase 2 WTLFU-01..05 invariants intact.

## Ablation CSV Outputs

### results/congress/ablation_doorkeeper.csv (15 lines: header + 14 data)

| α   | W-TinyLFU | W-TinyLFU+DK | Δ (pp) |
|-----|-----------|--------------|--------|
| 0.6 | 0.8803    | 0.8809       | +0.06  |
| 0.7 | 0.8124    | 0.8122       | -0.02  |
| 0.8 | 0.7223    | 0.7220       | -0.04  |
| 0.9 | 0.6053    | 0.6044       | -0.09  |
| 1.0 | 0.4757    | 0.4755       | -0.01  |
| 1.1 | 0.3572    | 0.3550       | -0.22  |
| 1.2 | 0.2578    | 0.2582       | +0.04  |

### results/court/ablation_doorkeeper.csv (15 lines: header + 14 data)

| α   | W-TinyLFU | W-TinyLFU+DK | Δ (pp) |
|-----|-----------|--------------|--------|
| 0.6 | 0.9144    | 0.9196       | +0.52  |
| 0.7 | 0.8671    | 0.8635       | -0.36  |
| 0.8 | 0.7830    | 0.7831       | +0.01  |
| 0.9 | 0.6768    | 0.6755       | -0.13  |
| 1.0 | 0.5519    | 0.5497       | -0.22  |
| 1.1 | 0.4343    | 0.4271       | -0.72  |
| 1.2 | 0.3540    | 0.3473       | -0.66  |

## Ablation Headline Finding

**Doorkeeper buys us small but context-dependent wins, with the benefit concentrated at high skew on Court.**

The plan's sanity-check framing ("context-dependent, with the gap visible at the alpha extremes") is validated:

1. **Congress (homogeneous skew):** W-TinyLFU+DK ≈ baseline at all alpha — the gap is within ±0.22pp and straddles zero. The Doorkeeper's first-touch filtering adds no measurable value when CMS already has enough budget for Congress's reference distribution. On this workload the pre-CMS filter is effectively a no-op accounting-wise.
2. **Court (heavier-tailed distribution with one-hit-wonder spikes):** W-TinyLFU+DK loses -0.36 to -0.72pp relative to baseline at α ∈ [0.7, 1.1] (DK wins) but loses +0.52pp at α = 0.6 (DK costs us on the uniform end). The DK absorbs one-hit wonders effectively on heavy-tailed Court, matching the Einziger-Friedman §4.3 paper claim; at low skew where first-touch keys ARE valuable (uniform distribution), absorbing them hurts slightly.
3. **Magnitude context:** These DK gains (<1pp) are 10-20× smaller than Plan 04-04's SIEVE visited-bit ablation (+15.4pp peak on Congress). This is expected and consistent with the Caffeine production decision to omit Doorkeeper — the integration complexity is not amortized by the benefit on typical workloads.

The **writeup answer to "what does Doorkeeper buy us"** is: a marginal hedge against one-hit-wonder spikes on heavy-tailed workloads, paid for by a marginal penalty on near-uniform access patterns. On our two workloads the effect is under 1pp either direction — well below the magnitude at which it would change the policy ranking. This matches STACK.md's original framing of DK as "ablation, not baseline."

## Verification Results

| Gate | Value | Pass |
|------|-------|------|
| `make clean && make` | 0 warnings, 0 errors | Yes |
| `make test` (both suites) | test_wtinylfu + test_doorkeeper both PASS | Yes |
| L-12 invariant wtinylfu.h | 4 (unchanged from Phase 2) | Yes |
| L-12 invariant doorkeeper.h | 0 (unchanged from 04-02) | Yes |
| L-12 invariant count_min_sketch.h | 0 (unchanged from Phase 2) | Yes |
| L-12 invariant cache.h | 11 (unchanged from 04-04) | Yes |
| Phase 2 acceptance (check_wtlfu_acceptance.py) | exit 0, all 3 conditions PASS | Yes |
| Phase 2 bit-identity (wtinylfu label in mrc.csv) | "W-TinyLFU" (not +DK) | Yes |
| make -n ablation-doorkeeper | 2 cache_sim invocations + 2 renames | Yes |
| results/congress/ablation_doorkeeper.csv | 15 lines (1 header + 14 data) | Yes |
| results/court/ablation_doorkeeper.csv | 15 lines (1 header + 14 data) | Yes |
| Policy uniques in both CSVs | {W-TinyLFU, W-TinyLFU+DK} | Yes |
| results/congress/figures/ablation_doorkeeper.pdf | 20526 bytes (MD5 48691ba3) | Yes |
| results/court/figures/ablation_doorkeeper.pdf | 20526 bytes (MD5 9fec0f51, distinct) | Yes |
| `plot_ablation_doorkeeper` defined | AST walk confirms | Yes |
| `plot_ablation_doorkeeper` registered in main() | 1 call site | Yes |
| `.PHONY` contains ablation-doorkeeper | confirmed | Yes |
| phase-04 composition target | depends on all 4 axes | Yes |

## Final phase-04 Composition Target

```makefile
phase-04: shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper
	@echo "phase-04 complete: shards-large + ablation-s3fifo + ablation-sieve + ablation-doorkeeper"
```

One invocation (`make phase-04`) now regenerates all of Phase 4's data artifacts (SHARDS 1M validation CSVs + 3 ablation CSVs × 2 workloads) and leaves the figures one `make plots WORKLOAD=X` call away.

## Deviations from Plan

### Cosmetic/structural (Rule 3)

**1. [Rule 3 - Cosmetic] Rephrased inline L-12 grep-reference comment to avoid self-matching**

- **Found during:** Task 2 verification
- **Issue:** My initial comment inside `access()` literally wrote "the four record(true|false) calls below" which caused the L-12 grep (`record\(\s*(true|false)`) to match the comment text itself, returning 5 instead of 4.
- **Fix:** Rephrased to "the four record-hit/miss calls below" with a parenthetical spec note `(grep `record\(\s*(true|false)` still returns 4, same as Phase 2)` — the backslash-escaped version doesn't match the non-escaped grep pattern.
- **Result:** grep count = 4, L-12 invariant intact.
- **Commit:** a43f032

**Interpretation:** This was caught BEFORE the task commit (part of iterative verification inside Task 2) — not a post-commit fix. The commit itself landed with the invariant green.

### None otherwise

No Rule 1 (bug fixes), Rule 2 (missing functionality), or Rule 4 (architectural decisions) triggered. No authentication gates. No checkpoints. Plan executed autonomously per `autonomous: true`.

## Cross-Plan Dependencies

- **Blocks:** None (Plan 04-05 is the final plan in Phase 4 — Axis B closer)
- **Blocked by:** Plan 04-02 (include/doorkeeper.h must exist on disk; verified present and unchanged)
- **Shared-file co-tenants:** Plans 04-01 (src/main.cpp, Makefile, scripts/plot_results.py — non-overlapping regions), 04-02 (Makefile test block — unchanged region), 04-03 (src/main.cpp, Makefile, scripts/plot_results.py — non-overlapping per D-19 axis decomposition; cache.h S3FIFOCache region UNTOUCHED by 04-05), 04-04 (src/main.cpp, Makefile, scripts/plot_results.py — non-overlapping; cache.h SIEVECache region UNTOUCHED by 04-05).

Verified via `git diff HEAD~3 HEAD` — the 3 Plan 04-05 commits touch ONLY `include/count_min_sketch.h`, `include/wtinylfu.h`, `src/main.cpp`, `Makefile`, `scripts/plot_results.py`. No drift into other Phase 4 plans' regions.

## Phase 4 Closing Status

Plan 04-05 is the final plan in Phase 4. With this plan committed, Phase 4 is COMPLETE:

| Plan | Axis | Requirement IDs | Status |
|------|------|-----------------|--------|
| 04-01 | A (SHARDS 1M validation) | SHARDS-01/02/03 | Complete |
| 04-02 | B (Doorkeeper standalone) | DOOR-01 | Complete |
| 04-03 | C (S3-FIFO small_frac ablation) | ABLA-01 | Complete |
| 04-04 | D (SIEVE visited-bit ablation) | ABLA-02 | Complete |
| **04-05** | **B closer (DK × W-TinyLFU integration)** | **DOOR-02/03** | **Complete** |

Phase 4 ROADMAP success criteria 1-4 all satisfied. Phase 5 (cross-workload analysis) unblocked.

## Known Stubs

None. All data paths are fully wired — the W-TinyLFU+DK variant is a complete sibling policy, both ablation CSVs are fully populated, both ablation PDFs render with 2 lines each across the full alpha grid, and Phase 2 back-compat is verified bit-identical.

## Self-Check: PASSED

### Files (all FOUND)

- `include/count_min_sketch.h` — modified in commit 2ae822a
- `include/wtinylfu.h` — modified in commit a43f032
- `src/main.cpp` — modified in commit ff660b1
- `Makefile` — modified in commit ff660b1
- `scripts/plot_results.py` — modified in commit ff660b1
- `results/congress/ablation_doorkeeper.csv` (15 lines, gitignored data artifact)
- `results/court/ablation_doorkeeper.csv` (15 lines, gitignored data artifact)
- `results/congress/figures/ablation_doorkeeper.pdf` (20526 bytes, gitignored)
- `results/court/figures/ablation_doorkeeper.pdf` (20526 bytes, gitignored)
- `.planning/phases/04-shards-large-scale-validation-ablations/04-05-SUMMARY.md` (this file)

### Commits (all FOUND in git log)

- `2ae822a` — feat(04-05): add on_age_cb_ callback hook to CountMinSketch (D-09)
- `a43f032` — feat(04-05): extend WTinyLFUCache with use_doorkeeper ctor flag + D-05 pre-CMS filter
- `ff660b1` — feat(04-05): add wtinylfu-dk policy + ablation-doorkeeper target + plot function
