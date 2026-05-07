---
phase: 01-enabling-refactors-courtlistener-pilot
plan: 06
subsystem: trace-collection
tags: [courtlistener, python, requests, rate-limit, pilot, http]

# Dependency graph
requires:
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: COURTLISTENER_API_KEY configured in .env and verified (Plan 01-05)
provides:
  - scripts/pilot_court_trace.py (throwaway Phase-1 pilot collector, separate from Phase-3 production collector)
  - traces/court_pilot.csv (177-row pilot trace in timestamp,key,size schema)
  - results/court/pilot_report.txt (per-endpoint tally + ALL PASS verdict)
  - Empirical validation of 4 CourtListener endpoints (/dockets, /opinions, /clusters, /courts) above 70% success gate
  - Confirmed safe rate cadence (0.8s base + 0-0.4s jitter, no 429s across 200 requests)
affects:
  - Phase 3 (CourtListener production collector — now unblocked; endpoint structure + auth + rate cadence all validated)
  - Phase 5 (cross-workload comparison — court trace collection plan is de-risked)

# Tech tracking
tech-stack:
  added: []  # No new dependencies. requests>=2.32 already in requirements.txt.
  patterns: [copy-modify collector pattern (not generalization), round-robin endpoint mixing, per-endpoint gate enforcement, host-allowlist guard]

key-files:
  created:
    - scripts/pilot_court_trace.py
    - traces/court_pilot.csv
    - results/court/pilot_report.txt
    - .planning/phases/01-enabling-refactors-courtlistener-pilot/01-06-SUMMARY.md
  modified: []

key-decisions:
  - "Intentional duplication over generalization: scripts/pilot_court_trace.py is a copy-modification of scripts/collect_trace.py, not a refactor into a shared module. This matches CONTEXT.md line 93 and STACK.md line 32 — the pilot is throwaway Phase-1 verification, the Phase-3 production collector lives separately at scripts/collect_court_trace.py."
  - "Round-robin endpoint selection (50 req each) chosen over weighted random per D-08 — yields a clean 50-request denominator for each of the 4 gate verdicts."
  - "Force-added traces/court_pilot.csv and results/court/pilot_report.txt with git add -f — traces/ and results/ are gitignored but these Phase-1 artifacts are preserved as audit evidence that Phase-3 can cite."

patterns-established:
  - "Copy-modify collector pattern: new HTTP trace collectors start as a verbatim copy of scripts/collect_trace.py and are adapted for the new API surface. Shared abstraction is avoided until at least 3 collectors exist (rule of three)."
  - "Per-endpoint success-gate pattern: pilot runs emit {200, 404, 403, 429, other} tallies per endpoint family, with a SUCCESS_GATE constant enforced before multi-hour production collections are attempted."
  - "Host-allowlist guard: the BASE_URL + ALLOWED_HOST literal pair is validated on every request before dispatch, catching accidental redirects or code-path typos that would aim at pacer.uscourts.gov."

requirements-completed: [TRACE-04]

# Metrics
duration: ~5min
completed: 2026-04-18
---

# Phase 1 Plan 06: CourtListener Pilot Summary

**200-request pilot against CourtListener v4 across /dockets/, /opinions/, /clusters/, /courts/ — all 4 endpoints cleared the 70% success gate with zero 429s and zero 403s, unblocking the Phase-3 production collector.**

## Performance

- **Duration:** ~5 min (pilot wall-clock 264s = 4.4 min; scaffolding + commits + summary ~0.5 min)
- **Started:** 2026-04-19T01:22:06Z (pilot run start)
- **Completed:** 2026-04-19T01:26:30Z (pilot run end)
- **Tasks:** 2 (pilot script creation + pilot execution)
- **Files created:** 3 (script + trace CSV + report)

## Accomplishments

- Created `scripts/pilot_court_trace.py` as a deliberate copy-modification of `scripts/collect_trace.py`, NOT a generalization. Host allowlist (`www.courtlistener.com`), Token auth header, 0.8s+0-0.4s jittered sleep, exponential backoff, 5-consecutive-429 per-endpoint abort, and 5-consecutive-403-from-start early drop are all implemented per PITFALLS C1/C2/C3 and CONTEXT.md D-07/D-08/D-09.
- Ran the 200-request pilot end-to-end against the live CourtListener v4 API. All 4 endpoints cleared the 70% gate on the first attempt — no remediation (neither ID-range narrowing nor endpoint dropping) was needed.
- Produced `traces/court_pilot.csv` with 177 HTTP-200 rows in the project-standard `timestamp,key,size` schema, confirming Phase 3's real collector will drop cleanly into the existing replay-Zipf and cache-simulator pipeline.
- Verified the rate cadence (0.8s base + 0-0.4s jitter) is safe: zero 429s observed across 200 requests. This validates the ~3,200 req/hour pacing for the upcoming 20K Phase-3 collection (will fit in ~6.3 hours; well under the 5,000 req/hour quota).

## Task Commits

1. **Task 1: Create `scripts/pilot_court_trace.py` (copy-modified from `scripts/collect_trace.py`)** — `b58489a` (feat)
2. **Task 2: Run the 200-request pilot, enforce the 70% gate, write report** — `dfbb313` (feat)

_Plan metadata commit follows this SUMMARY._

## Files Created/Modified

- `scripts/pilot_court_trace.py` (new, 254 lines) — throwaway Phase-1 pilot trace collector against CourtListener v4. Reads `COURTLISTENER_API_KEY` from env, round-robins across 4 endpoint families for 50 requests each, exits 0 iff every endpoint clears the 70% gate. Executable (`chmod +x`).
- `traces/court_pilot.csv` (new, 177 rows + header) — `timestamp,key,size` pilot trace. Force-added (traces/ is gitignored).
- `results/court/pilot_report.txt` (new, 9 lines) — per-endpoint tally + `Verdict: ALL PASS`. Force-added (results/ is gitignored).

## Per-Endpoint Pilot Tally (verbatim from `results/court/pilot_report.txt`)

```
=== CourtListener Pilot Summary ===
  docket  : 200= 45 404=  5 403=  0 429=  0 other=  0 total= 50 success= 90.0% [PASS]
  opinion : 200= 37 404= 13 403=  0 429=  0 other=  0 total= 50 success= 74.0% [PASS]
  cluster : 200= 45 404=  5 403=  0 429=  0 other=  0 total= 50 success= 90.0% [PASS]
  court   : 200= 50 404=  0 403=  0 429=  0 other=  0 total= 50 success=100.0% [PASS]

Gate: each endpoint must hit success >= 70%
Verdict: ALL PASS
```

**Observations:**
- **docket** (90.0%) and **cluster** (90.0%) — the loose id_range upper bounds (80M / 12M) produce a modest 10% 404 rate. Still comfortably above the gate; Phase 3 may narrow these slightly if it wants to squeeze out wasted quota, but it is not required.
- **opinion** (74.0%) — tightest margin. The 15M opinion id_range has a ~26% 404 rate, making this the endpoint most likely to benefit from range narrowing in Phase 3. Still clears the 70% gate today.
- **court** (100.0%) — unsurprising; the 20-court hand-curated list is all known-valid IDs. This is the hot-cache anchor analogous to the 119th Congress weighting in the existing Congress collector.
- **Zero 429s across the full run** confirms the 0.8s + 0-0.4s cadence is appropriate.
- **Zero 403s across the full run** confirms all 4 planned endpoints are publicly accessible with a standard free-tier token — PITFALLS C3 / D-09 is moot for this endpoint selection.

## Wall-Clock Timing

- **Target:** ~5 min budget (200 req × ~1.1s mean = ~220s + overhead)
- **Observed:** 264s (4.4 min) — within budget. Includes one jittered sleep per request plus HTTP round-trip latency.

## Token Leakage Verification

Explicit grep for the full 40-char token value in both committed output files:

```
grep -c -- "$TOKEN" traces/court_pilot.csv        -> 0
grep -c -- "$TOKEN" results/court/pilot_report.txt -> 0
grep -rl "$TOKEN" scripts/ traces/ results/        -> (no matches)
```

Only the masked form (first 4 + last 4 chars) appears in the startup log, which is routed to stdout only and never persisted to a committed file. The raw token lives in `.env` (gitignored) and in `os.environ` for the lifetime of the process.

## Decisions Made

- **Used `/Users/mirayu/civicache/.venv/bin/python3`** (venv3.14 with `requests` 2.33.1) to run the pilot, since the system Python and `.venv13` did not have `requests` installed in the executor's environment. The script itself uses `#!/usr/bin/env python3` and is interpreter-agnostic — any Python 3 with `requests` works. Documenting here so the next runner knows which interpreter proved out the pilot.
- **Force-committed `traces/` and `results/court/` outputs** despite `.gitignore` excluding those dirs. Rationale: the pilot is a one-shot audit artifact for Phase 3 to cite; regenerating it from scratch in a future phase would waste quota and could yield slightly different IDs due to the random draws. The commit preserves the exact 200-request sample that cleared the gate.
- **No remediation needed** — all endpoints cleared on the first attempt, so neither D-07 (ID range narrowing) nor D-09 (endpoint dropping) was exercised. Phase 3 can trust the endpoint list as-is.

## Deviations from Plan

None — plan executed exactly as written. All acceptance criteria passed.

The only subtlety worth noting (not a deviation, but a property inherited from the `scripts/collect_trace.py` template): Python's `csv.writer` uses CRLF line endings by default. The plan's acceptance-criteria grep `head -1 traces/court_pilot.csv | grep -q '^timestamp,key,size$'` treats `$` as end-of-line without matching the trailing `\r` on BSD grep (macOS). The file content is correct (`timestamp,key,size\r\n` — identical to what the existing Congress collector produces), and `csv.reader` in the downstream pipeline handles CRLF transparently. Verified by parsing with `csv.reader` and confirming `header == ['timestamp','key','size']` and `len(rows) == 177`.

## Issues Encountered

None during planned work. One environmental note: system `python3` lacks `requests`, so all pilot invocations use `/Users/mirayu/civicache/.venv/bin/python3` explicitly. The script's `#!/usr/bin/env python3` shebang means `chmod +x scripts/pilot_court_trace.py && ./scripts/pilot_court_trace.py` would also need a venv-activated shell — direct `python3` invocation by path is the simplest reproduction recipe.

## User Setup Required

None — the `COURTLISTENER_API_KEY` was already configured in `.env` by Plan 01-05. The pilot loads it via `os.environ.get` after the user sources `.env` into their shell.

## Next Phase Readiness

- **Phase 3 (CourtListener production collector) is unblocked.** All 4 planned endpoints cleared the 70% gate; no endpoints need to be dropped; ID ranges are loose but serviceable. The production collector (`scripts/collect_court_trace.py`) can be built on the same template with the larger `--requests 20000` budget and `--duration` cap.
- **Rate cadence confirmed safe.** The 0.8s + 0-0.4s jitter produced zero 429s at 200 requests; extrapolated to 20K, the production collector will take ~6.3 hours of wall clock — within the ~7-hour budget implied by the 5,000 req/hour quota and the 20K target.
- **Schema compatibility confirmed.** `traces/court_pilot.csv` uses the same `timestamp,key,size` schema as the Congress trace, so `include/trace_gen.h::load_trace` (and the replay-Zipf pipeline) will ingest the Phase 3 court trace with no code changes.
- **Pilot artifact preserved.** If Phase 3 ever needs to debug a regression ("did the endpoints change?"), `traces/court_pilot.csv` + `results/court/pilot_report.txt` from this run are the reference baseline.
- **Note to Phase 3 authors:** keep `scripts/pilot_court_trace.py` around for now; do NOT generalize it into a shared module. CONTEXT.md line 93 and STACK.md line 32 explicitly call for two separate files (pilot + production). The pilot file may be archived or deleted at the end of Milestone 2 if desired.

## Self-Check: PASSED

- `scripts/pilot_court_trace.py` exists and is executable — verified with `test -x`.
- `traces/court_pilot.csv` exists with valid `timestamp,key,size` header + 177 rows — verified by parsing with `csv.reader`.
- `results/court/pilot_report.txt` exists with 4 per-endpoint lines + `Verdict: ALL PASS` line — verified by grep.
- Commit `b58489a` (Task 1) exists in git history — verified with `git log --oneline`.
- Commit `dfbb313` (Task 2) exists in git history — verified with `git log --oneline`.
- No token value appears in any committed file — verified with `grep -r` against the 40-char token.

---
*Phase: 01-enabling-refactors-courtlistener-pilot*
*Completed: 2026-04-18*
