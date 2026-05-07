---
status: resolved
phase: 01-enabling-refactors-courtlistener-pilot
source: [01-VERIFICATION.md]
started: 2026-04-19T01:49:00Z
updated: 2026-04-19T02:15:00Z
---

## Current Test

[all 3 tests complete]

## Tests

### 1. Confirm the CourtListener pilot report reflects a real live-API run
expected: User confirms `results/court/pilot_report.txt` was generated against live `www.courtlistener.com` (not replayed from a local mock), with 200 actual HTTP requests issued using the .env token. All 4 endpoints show ≥70% success with no 403s.
result: pass (2026-04-19 — user confirmed live run; timestamps in traces/court_pilot.csv span 4:23 consistent with rate-limited 200-req pilot; real CourtListener resource keys visible)

### 2. Visually inspect results/congress/figures/*.pdf for sensible shapes
expected: `mrc.pdf` shows miss-ratio curves monotonically decreasing with cache size for all 5 policies; `alpha_sensitivity.pdf` shows LRU/FIFO/CLOCK degrading at low alpha; `workload.pdf` shows a log-normal-ish size distribution.
result: pass (2026-04-19 — user confirmed mrc.pdf, alpha_sensitivity.pdf, workload.pdf visually sensible)

### 3. Confirm `make plots` works end-to-end on the user's machine
expected: `make plots` exits 0 and regenerates `results/congress/figures/*.pdf`; or, if libexpat workaround is needed, user confirms `.venv` activation + `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` produces the PDFs.
result: pass-with-fix (2026-04-19 — user initially hit ModuleNotFoundError without venv, then libexpat symbol mismatch with DYLD_LIBRARY_PATH + venv activation. Root cause: .venv/bin/python is a hardened-runtime binary so macOS strips DYLD vars; also `make plots` didn't activate the venv. Inline fix: patched Makefile `plots` target to call Homebrew python directly with DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib and PYTHONPATH=.venv/lib/python3.14/site-packages. Verified: `make plots` alone now produces all 6 PDFs.)

## Summary

total: 3
passed: 3
issues: 1 (resolved inline — Makefile plots target patched)
pending: 0
skipped: 0
blocked: 0

## Gaps

### G-01: Makefile `plots` target did not work out-of-the-box on macOS with Python 3.14
status: resolved
found_at: 2026-04-19 during UAT test 3
debug_session: null (fixed inline, no separate session needed)

**Root cause:** Two compounding issues:
1. `make plots` did not activate .venv, so system python3 (no matplotlib) was used.
2. Even with .venv activated + DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib, Python 3.14's pyexpat dlopen resolved against /usr/lib/libexpat.1.dylib (missing symbol `_XML_SetAllocTrackerActivationThreshold`). Cause: `.venv/bin/python` is a hardened-runtime binary so macOS strips DYLD_* env vars on exec. Invoking the real Homebrew Python (`/opt/homebrew/opt/python@3.14/bin/python3.14`) preserves DYLD vars.

**Fix:** Patched `Makefile` plots target to:
- Call Homebrew python directly: `/opt/homebrew/opt/python@3.14/bin/python3.14`
- Set `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`
- Set `PYTHONPATH=.venv/lib/python3.14/site-packages` to pick up venv-installed matplotlib
- Expose `PLOT_PYTHON` / `PLOT_PYTHONPATH` overrides for other setups

**Verification:** `make plots` (no env prefix, no venv activation) now exits 0 and produces all 6 PDFs.

**Note:** The CONTEXT.md note flagging the libexpat issue as "out of scope" was stale — a clean Makefile target is achievable and the workaround documentation in CONTEXT.md should be updated for Phase 2+ context loads.
