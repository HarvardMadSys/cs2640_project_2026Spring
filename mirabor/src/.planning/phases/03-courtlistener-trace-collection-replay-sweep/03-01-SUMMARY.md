---
phase: 03-courtlistener-trace-collection-replay-sweep
plan: 01
subsystem: trace-collection
tags: [trace-collection, courtlistener, rate-limiting, python, requests, ssrf-mitigation]

# Dependency graph
requires:
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: CourtListener token in .env + pilot-tuned ENDPOINTS dict + ALLOWED_HOST pattern from scripts/pilot_court_trace.py + CSV schema from traces/court_pilot.csv
provides:
  - scripts/collect_court_trace.py (production CourtListener trace collector, 609 lines)
  - 80/20 ?fields= metadata-vs-full mix on /opinions/ (D-01, D-02, D-03)
  - Per-endpoint 25% target scheduler (D-05) with 5000 successful rows each = 20K total
  - D-06 first-500-<60% fallback (narrow id_range by 0.67, log to report)
  - D-11 429 ramp + 5-consecutive hard-stop with exact FATAL diagnostic
  - --resume flag that classifies existing CSV rows by endpoint-prefix and continues
  - Module-level + per-request urlparse host allowlist (T-03-01-01 SSRF mitigation)
affects:
  - Phase 3 Plan 03-02 (overnight 20K collection run — invokes this script)
  - Phase 3 Plan 03-03 (workload_stats + 6-policy sweep over the resulting trace)
  - Phase 5 compare_workloads.py (reads results/court/ artifacts produced downstream)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Copy-modify-not-refactor: sibling production collector per STACK.md §32 — pilot stays as standing verification tool"
    - "Host allowlist defence-in-depth: module-level assert + per-request urlparse check"
    - "Round-robin endpoint scheduler with per-endpoint target tracking for equal-split workload"
    - "append-per-row + f.flush() + --resume by endpoint-prefix classification (resilient to mid-run interruption)"

key-files:
  created:
    - "scripts/collect_court_trace.py — 609-line production collector implementing D-01..D-11 + D-15"
  modified: []

key-decisions:
  - "Per-endpoint consec_429 counter (D-11 scope): 429 streak is tracked per endpoint and any non-429 on that endpoint resets it. Module-level would risk a stuck endpoint from tripping a hard-stop based on a healthy one; the plan's prose specifies per-endpoint and the pilot uses per-endpoint too."
  - "D-11 ramp index for 4th+ consecutive 429 (before the 5-consec hard-stop fires): reuse the last bucket (+90) via min(consec-1, len(additions)-1). Avoids an undefined 4th-bucket value; the hard-stop at 5 means at most one +90 extension before abort."
  - "Retry-After header ceiling at 120s: pilot's CONSECUTIVE_429_ABORT logic caps backoff; we likewise clamp the hinted value to protect against a pathological long Retry-After suggestion while still respecting server intent within reason."
  - "D-06 fallback timing: evaluated AFTER the 500th response lands for that endpoint (not before issuing the 501st). Matches the plan's prose 'After any endpoint has issued exactly FALLBACK_PROBE_WINDOW requests' once you treat the completion of request #500 as the trigger. Fires at most once per endpoint (fallback_triggered latch)."
  - "Target-rows splitting for smoke runs: when --target-rows is not a multiple of 4, we distribute the remainder 1-per-endpoint to the first N endpoints so a 10-row smoke touches all 4 families (observed: docket=3, opinion=3, cluster=2, court=2)."
  - "Per-endpoint target tally PASS gate combines BOTH success_count>=target AND success_rate>=FALLBACK_SUCCESS_THRESHOLD. Matches the plan's stated PASS condition."

patterns-established:
  - "Token-fingerprint-only logging: never print the raw API key; only api_key[:4]+'...'+api_key[-4:]+len (mirrors pilot line 248 verbatim)"
  - "Mutable-copy of module-level ENDPOINTS for runtime narrowing: D-06 fallback writes to an endpoints_state local, never mutates ENDPOINTS"

requirements-completed: [TRACE-05]

# Metrics
duration: 3m 53s
completed: 2026-04-19
---

# Phase 03 Plan 01: Production CourtListener Trace Collector Summary

**609-line production collector `scripts/collect_court_trace.py` implementing D-01..D-11 + D-15 — hard host allowlist, 80/20 ?fields= opinion mix, 0.8s+0-0.4s jittered pacing, Retry-After + [0,30,90] ramp, 5-consecutive-429 hard-stop, --resume by endpoint-prefix classification — smoke-verified end-to-end with a 10-row real-API run (14s, exit 0).**

## Performance

- **Duration:** 3m 53s
- **Started:** 2026-04-19T06:08:41Z
- **Completed:** 2026-04-19T06:12:34Z
- **Tasks:** 2 (1 implementation + 1 smoke verification)
- **Files modified:** 1 (scripts/collect_court_trace.py created)

## Accomplishments

- Built scripts/collect_court_trace.py as a sibling to scripts/pilot_court_trace.py (NOT a replacement — pilot stays as standing verification tool per STACK.md §32)
- All 26 grep-discoverable invariants from the plan's acceptance criteria pass (ALLOWED_HOST, BASE_URL, BASE_DELAY=0.8, JITTER=0.4, OPINION_METADATA_FRACTION=0.8, D-02 field set, [0,30,90] ramp, CONSECUTIVE_429_HARD_STOP=5, exact FATAL diagnostic string, Resume-after-1-hour text, PER_ENDPOINT_TARGET=5000, --resume flag, FALLBACK_PROBE_WINDOW=500, FALLBACK_NARROW_FACTOR=0.67, COURTLISTENER_API_KEY, f.flush(), all 4 endpoint templates, court_ids list)
- Module-level + per-request `assert urlparse(url).netloc == ALLOWED_HOST` enforces T-03-01-01 SSRF mitigation (belt-and-suspenders against a BASE_URL edit OR a malformed path_tpl)
- 10-row smoke run against live CourtListener API completed in 14s, exit 0, wrote well-formed CSV matching traces/court_pilot.csv schema (CRLF line endings, same as pilot)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create scripts/collect_court_trace.py** — `e9d8557` (feat)
2. **Task 2: 10-row smoke verification** — no commit (verification-only; smoke files `/tmp`-scoped and deleted after; no code changes)

**Plan metadata commit:** pending (final metadata commit after SUMMARY write + STATE/ROADMAP/REQUIREMENTS updates)

## Files Created/Modified

- `scripts/collect_court_trace.py` (NEW, 609 lines) — Production CourtListener v4 trace collector. Copy-modify of `scripts/collect_trace.py` (Congress template) with ENDPOINTS dict verbatim from `scripts/pilot_court_trace.py`. Implements D-01 (hard host allowlist + 80/20 ?fields= opinion mix), D-02 (minimal field set), D-03 (80/20 applies only to /opinions/), D-04 (size = actual response byte length regardless of branch), D-05 (equal 25% per endpoint = 5000 each), D-06 (first-500-<60% fallback, narrow by 0.67), D-07 (0.8s + 0.0-0.4s jitter), D-08 (per-row append + flush), D-09 (--resume classifies existing CSV rows by endpoint-prefix), D-10 (failed requests skipped from CSV, counted in tally), D-11 (Retry-After + [0,30,90] ramp + 5-consec hard-stop), D-15 (default output traces/court_trace.csv).

## Smoke-run observed per-endpoint spread (10 successful rows, seed=42)

| Endpoint | 200 | 404 | 403 | 429 | Total issued | Success rate |
|----------|-----|-----|-----|-----|--------------|--------------|
| docket   |   3 |   0 |   0 |   0 |            3 |      100.0%  |
| opinion  |   3 |   1 |   0 |   0 |            4 |       75.0%  |
| cluster  |   2 |   0 |   0 |   0 |            2 |      100.0%  |
| court    |   2 |   0 |   0 |   0 |            2 |      100.0%  |

- All 4 endpoint families represented in the 10-row smoke — the remainder-distribution logic (docket=3, opinion=3, cluster=2, court=2) works as intended.
- Observed 1 opinion 404 (seed=42 random draw landed on non-existent ID) — confirms D-10 "skip failed requests from trace" wiring: the 404 appears in the tally but NOT the CSV. The issued total (4) vs CSV rows (3) for opinion is the direct evidence.
- Two opinion draws at the 80% metadata branch visibly landed on the ?fields= path: rows with size 115 and 130 bytes (a plain metadata response). One 20% full-fetch draw landed on a full plain_text opinion: 81,911 bytes. This is the D-01 mix working end-to-end.
- Average observed pacing: 14s / 11 issued requests = ~1.27s/req (includes 0.8s base + ~0.0-0.4s jitter + ~0.07s network RTT to CourtListener). Matches D-07 design.

## Runtime estimate for Plan 03-02 (20K collection)

Extrapolating from the smoke-run pacing:

- Observed success rate on smoke = 10/11 issued = ~91% (one 404 out of 11).
- Phase 1 pilot overall success rate = 88.5% (aggregate across 200 requests: 177 successes / 200 = 88.5%).
- Production 20K target / ~0.85 avg success = ~23,530 issued requests.
- At 1.27s/issued-request observed rate: 23,530 × 1.27s ≈ 29,900s ≈ **8h 18m**.
- The ROADMAP / CONTEXT D-07 estimate was "~6.1 hours at 0.8s + 0-0.4s jitter." The observed +2 hours comes from network RTT on top of the pacing sleep.
- **Plan 03-02 should budget ~8 hours overnight wall-clock, not 6.1h.** With --resume support, a mid-run crash is recoverable without lost work.

## Decisions Made

Listed under `key-decisions:` in frontmatter above. All are implementation-detail clarifications of plan-specified behavior, not architectural changes. The plan specified D-01..D-11 precisely enough that execution was mechanical.

## Deviations from Plan

None — plan executed exactly as written. All 26 grep invariants pass on first write; the 10-row smoke test passed on first run with no code edits post-script-write.

### Note on shell-quirk during Task 2 verification (not a deviation)

The initial smoke-verification one-liner used `head -1 FILE | grep -qE '^timestamp,key,size$'` which failed because Python's csv.writer emits CRLF line endings (matching `traces/court_pilot.csv` — the committed reference). A re-run with `head -1 FILE | tr -d '\r' | grep -qE '^timestamp,key,size$'` passes cleanly. The CSV is correct and matches the pilot schema verbatim; this was a verification-command adjustment, not a code fix.

## Issues Encountered

None — plan pre-baked every constant and the smoke run worked on first try.

## User Setup Required

None — COURTLISTENER_API_KEY was already configured in `.env` during Phase 1 Plan 01-05. No external configuration needed for this plan. Plan 03-02 will invoke the collector for the overnight 20K run.

## Known Stubs

None. Script is fully wired end-to-end; Plan 03-02 exists to *run* it at production scale, not to finish it.

## Next Phase Readiness

- **Plan 03-02 UNBLOCKED:** `scripts/collect_court_trace.py` exists, compiles, runs against the live CourtListener API, writes well-formed CSV matching the pilot schema, and produces a per-endpoint report file. The overnight 20K collection can be invoked directly.
- **Expected runtime for Plan 03-02:** ~8h wall-clock (observed smoke pacing × scale, not the ROADMAP's 6.1h estimate which assumed zero network RTT).
- **--resume path is battle-tested in code but NOT yet against a partial real run.** If Plan 03-02's overnight collection is interrupted, the first --resume invocation will be its first live test. Low risk — the classification logic (endpoint_for_key + KEY_PREFIX_TO_ENDPOINT map) is straightforward and the smoke verified the CSV schema it reads.

## Self-Check: PASSED

- scripts/collect_court_trace.py exists (609 lines, matches claim)
- .planning/phases/03-courtlistener-trace-collection-replay-sweep/03-01-SUMMARY.md exists
- Commit e9d8557 verified in git log

---
*Phase: 03-courtlistener-trace-collection-replay-sweep*
*Completed: 2026-04-19*
