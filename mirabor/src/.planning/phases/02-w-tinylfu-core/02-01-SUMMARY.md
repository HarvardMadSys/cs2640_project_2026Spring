---
phase: 02-w-tinylfu-core
plan: 01
subsystem: research-prework
tags: [caffeine, w-tinylfu, count-min-sketch, reference-notes, pre-implementation]

# Dependency graph
requires:
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: include/hash_util.h (FNV-1a + 4 seeds for downstream CMS rows)
provides:
  - .planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md (verbatim Caffeine v3.1.8 reference for sampleSize, increment, reset, D-08a..D-08e)
  - locked update-rule decision (CONSERVATIVE per WTLFU-01) and 6 documented deliberate deviations from Caffeine
affects: [02-02 (count_min_sketch.h), 02-03 (wtinylfu.h), 02-04 (test_wtinylfu.cpp)]

# Tech tracking
tech-stack:
  added: []  # documentation-only; no new C++/Python deps
  patterns:
    - "Pre-implementation reference notes pinned to upstream tag (Caffeine v3.1.8) committed to repo"
    - "Per-row deviation table cross-references REQUIREMENTS.md and CONTEXT.md as authority sources"

key-files:
  created:
    - .planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md
  modified: []

key-decisions:
  - "Caffeine v3.1.8 source confirmed: increment() uses STANDARD update (bitwise OR of incrementAt at FrequencySketch.java:L161-L164). Our port DELIBERATELY deviates by using CONSERVATIVE update per WTLFU-01."
  - "Caffeine sample_size = 10 * maximum (FrequencySketch.java:L96), NOT 10 * width * depth. Our port uses 10 * width * depth = 40 * width per CONTEXT.md L-5/D-03 (4× slower halving)."
  - "Caffeine post-reset uses (size - count/4) >> 1 fractional residual; our port uses sample_count_ = 0 full reset per CONTEXT.md D-10."
  - "D-08a (empty-probation short-circuit) does NOT have a literal Caffeine analogue — Caffeine pulls victims from protected/window when probation is empty. Our port simplifies to unconditional admission during this warmup-only condition."
  - "D-08e tie-breaker: Caffeine adds 1/128 random admission for warm candidates (freq>=6) as hash-DoS defense (BoundedLocalCache.java:L884-L897, ADMIT_HASHDOS_THRESHOLD=6 at L224). Our port omits — no adversarial threat model in research simulator; preserves D-05 determinism."
  - "D-08b/D-08c/D-08d match Caffeine verbatim — no deviations recorded."

patterns-established:
  - "Pre-work reference notes for upstream-mirroring policies: pin source tag, cite by file:line, explicit deviation table with REQUIREMENTS-anchored justifications."
  - "Conservative-update CMS in C++17: locked formula in §7 SUMMARY block ready for direct paste into 02-02."

requirements-completed: []  # this plan is pre-work, not a requirement-satisfying implementation

# Metrics
duration: ~7min
completed: 2026-04-19
---

# Phase 02 Plan 01: Caffeine v3.x Pre-Work Summary

**Pulled Caffeine v3.1.8 FrequencySketch.java + BoundedLocalCache.java; verbatim-cited 549-line reference notes locking sampleSize, increment update rule, reset/halving, and D-08a..D-08e admission/promotion edge cases for Plans 02-02 and 02-03.**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-04-19T03:30:30Z
- **Completed:** 2026-04-19T03:37:19Z
- **Tasks:** 1
- **Files modified:** 1 (created)

## Accomplishments

- Fetched Caffeine v3.1.8 source (FrequencySketch.java 214 lines + BoundedLocalCache.java 4679 lines) via curl from raw.githubusercontent.com (WebFetch-equivalent path).
- Authored `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` (549 lines, 7 `##` sections per plan spec).
- Resolved the WTLFU-01 conflict authoritatively: Caffeine uses STANDARD update; our port DELIBERATELY uses CONSERVATIVE per the requirement. §6 row 1 documents the deviation; §7 SUMMARY block restates the locked rule for 02-02 to consume verbatim.
- Mapped all five D-08a..D-08e admission/promotion edge cases to Caffeine source line citations with verbatim Java quoted in code blocks.
- Recorded **6 deliberate deviations** from Caffeine in §6 deviation table, each with a REQUIREMENTS- or CONTEXT-anchored justification.

## Task Commits

1. **Task 1: Fetch Caffeine source + transcribe reference notes** — `665c767` (docs)

## Caffeine source metadata (per `<output>` requirement)

- **Source repo:** `ben-manes/caffeine`
- **Tag fetched:** `v3.1.8`
- **Files:** `caffeine/src/main/java/com/github/benmanes/caffeine/cache/FrequencySketch.java` (214 lines), `caffeine/src/main/java/com/github/benmanes/caffeine/cache/BoundedLocalCache.java` (4679 lines)
- **Caffeine's observed update rule:** **STANDARD** (all 4 counters incremented unconditionally via bitwise `|` of `incrementAt` calls at `FrequencySketch.java:L161-L164`; `@SuppressWarnings("ShortCircuitBoolean")` at L144 confirms the OR is non-short-circuiting)
- **Our port deviates from Caffeine on the update rule:** **YES** — our port uses CONSERVATIVE update per REQUIREMENTS.md WTLFU-01 (line 42) and ROADMAP.md §Phase 2 Success Criterion 1, both of which mandate "conservative update" unconditionally. The requirement is authoritative.
- **Deliberate deviations count:** **6** (see CAFFEINE-NOTES.md §6 table):
  1. increment update rule (STANDARD → CONSERVATIVE; per WTLFU-01)
  2. sample_size formula (`10 * maximum` → `10 * width * depth`; per CONTEXT.md L-5/D-03)
  3. post-reset residual (`(size - count/4) >> 1` → `sample_count_ = 0`; per CONTEXT.md D-10)
  4. D-08a empty-probation short-circuit (Caffeine escalates to protected/window → our port admits unconditionally; warmup simplification)
  5. D-08e hash-DoS random tiebreaker (Caffeine 1/128 random admission for `freq>=6` → our port strict reject; preserves D-05 determinism)
  6. hash scheme (`spread`+`rehash` mixing constants → FNV-1a × 4 seeds; per CONTEXT.md L-10/D-11)

## §7 "Summary — values locked" inline quote (per `<output>` requirement)

The next executor (Plan 02-02 for CMS, Plan 02-03 for W-TinyLFU) does not need to re-read CAFFEINE-NOTES.md — the locked values are reproduced here verbatim:

### CMS (Plan 02-02 — `include/count_min_sketch.h`)

- **Counter width:** 4 bits, saturating at 15 (matches Caffeine).
- **Depth:** 4 rows (matches Caffeine).
- **Width:** `nextpow2(n_objects_hint)` where `n_objects_hint = capacity_bytes / avg_object_size_from_workload_stats` (CONTEXT.md D-02; matches Caffeine's `ceilingPowerOfTwo(maximum)`).
- **Update rule: CONSERVATIVE** — only increment counters whose value equals the row-minimum. **(Locked by REQUIREMENTS.md WTLFU-01; deliberately differs from Caffeine's STANDARD update — see §6 row 1.)**
- **Halving trigger:** when `sample_count_ >= sample_size_`.
- **Halving operation:** for each 4-bit counter, `counter = counter >> 1` (integer right shift; no rounding).
- **sample_size formula:** `10 * width * depth` (CONTEXT.md L-5 / D-03; deliberately 4× larger than Caffeine's `10 * maximum` — see §6 row 2).
- **Post-halve:** `sample_count_ = 0` (full reset; deliberately simpler than Caffeine — see §6 row 3).
- **Hash scheme:** FNV-1a from `include/hash_util.h` with 4 seeds (`FNV_SEED_A..D`); one call per row, keyed on `(seed, key_bytes)` — see §6 row 6.
- **Public API:** `record(const std::string& key)`, `estimate(const std::string& key) const`, `reset()`, `force_age()` (test hook).

### W-TinyLFU (Plan 02-03 — `include/wtinylfu.h`)

- **Window LRU byte budget:** 1% of total byte capacity (CONTEXT.md L-6).
- **Main SLRU byte budget:** 99% of total byte capacity.
- **Protected byte budget:** 80% of main SLRU.
- **Probation byte budget:** 20% of main SLRU.
- **Admission test:** `freq(candidate) > freq(victim)` (strict `>` only).
- **Tie behavior (D-08e):** `freq(candidate) == freq(victim)` → reject candidate (favor incumbent). No randomization. **(Deliberately omits Caffeine's hash-DoS 1/128 random admission — see §6 row 5.)**
- **Probation→protected promotion (D-08c):** fires on ANY hit during probation residency (matches Caffeine `reorderProbation`).
- **Protected overflow demotion (D-08d):** demoted protected-LRU entry → probation MRU position (matches Caffeine `demoteFromMainProtected`).
- **Empty-probation short-circuit (D-08a):** candidate admitted unconditionally when probation is empty. **(Deliberate simplification of Caffeine's victim-escalation logic — see §6 row 4.)**
- **Spare-main-capacity short-circuit (D-08b):** admission test only runs when main SLRU is at-or-over byte budget (matches Caffeine's `while (weightedSize() > maximum())` gate).
- **Stats recording:** `record(hit, size)` called exactly once per outer `access()` call (CONTEXT.md L-12); never from inside sub-region operations.
- **Internal lists:** window, protected, probation are private std::list-based deques; NOT reused `LRUCache` instances (CONTEXT.md L-12).

## Files Created/Modified

- `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` (created, 549 lines) — Verbatim Caffeine v3.1.8 reference for sampleSize, increment, reset, D-08a..D-08e + 6 deliberate-deviation table + §7 locked-values block consumed by 02-02 and 02-03

## Decisions Made

All 6 deviation decisions are listed in the `key-decisions` frontmatter block and reproduced in detail in CAFFEINE-NOTES.md §6 + §7. The single high-level decision: **WTLFU-01's CONSERVATIVE update rule is authoritative; Caffeine's STANDARD update is reference material only.** This unblocks Plan 02-02's `count_min_sketch.h` implementation to lock to CONSERVATIVE without a checkpoint round-trip.

## Deviations from Plan

None — plan executed exactly as written. (The "deviations" listed in CAFFEINE-NOTES.md §6 are deviations from CAFFEINE, not from the PLAN; the plan explicitly required documenting these.)

## Issues Encountered

None. WebFetch was not directly available (no MCP tool present), so the documented `curl` fallback path was used per the `<caffeine_source_guidance>` block in the executor prompt. Caffeine v3.1.8 raw URLs returned 200 on first attempt for both files.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- **Plan 02-02 (count_min_sketch.h)** is unblocked. The §7 "CMS" block above is the complete spec; 02-02's executor can paste it verbatim into the header doc-comment.
- **Plan 02-03 (wtinylfu.h)** is unblocked. The §7 "W-TinyLFU" block above is the complete admission/promotion spec.
- **Plan 02-04 (test_wtinylfu.cpp)** can rely on the locked CONSERVATIVE update rule for its CMS basics test (D-05 first bullet).
- No outstanding concerns or blockers. Phase 02 BLOCKING gate cleared.

## Self-Check: PASSED

- File `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` exists (549 lines, 7 `##` headers).
- Commit `665c767` exists in git log.
- All required literals present: sampleSize, increment, reset(), D-08a, D-08b, D-08c, D-08d, D-08e, v3.1.8, FrequencySketch.java, BoundedLocalCache.java, CONSERVATIVE.
- §2 documents Caffeine's STANDARD update AND explicitly states our CONSERVATIVE choice per WTLFU-01.
- §7 restates the locked CONSERVATIVE update rule.
- Match-status determinations present for all 5 D-08 rules (D-08a deviates, D-08b matches, D-08c matches, D-08d matches, D-08e partially matches with documented variance).

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-19*
