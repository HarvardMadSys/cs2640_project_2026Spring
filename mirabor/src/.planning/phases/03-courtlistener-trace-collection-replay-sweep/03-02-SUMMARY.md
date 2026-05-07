---
phase: 03-courtlistener-trace-collection-replay-sweep
plan: 02
completed: 2026-04-19T16:07:00Z
requirements: [TRACE-06]
commits:
  - feat(03-02): collect 20K CourtListener trace + per-endpoint report (TRACE-06) — f9b60de
status: complete
---

# Plan 03-02: 20K CourtListener Trace Collection — Summary

## What was built

`traces/court_trace.csv` — 20,001 lines (header + 20,000 data rows) — and `results/court/collection_report.txt` — per-endpoint success tally — both committed to git via a targeted `.gitignore` exemption pattern (D-15).

## How the checkpoint ran

User launched `scripts/collect_court_trace.py` overnight on their local machine using the project's `.venv`:

```bash
.venv/bin/python3 scripts/collect_court_trace.py \
    --output traces/court_trace.csv \
    --report results/court/collection_report.txt \
    --target-rows 20000 \
    2>&1 | tee results/court/collection_progress.log
```

Returned the next morning with `done`. Orchestrator ran verification + commit step inline (no executor agent needed — the remaining work was scripted mechanical steps).

## Results

### Per-endpoint tally (from `results/court/collection_report.txt`)

| Endpoint | 200 | 404 | 403 | 429 | other | total | success | gate |
|----------|-----|-----|-----|-----|-------|-------|---------|------|
| docket   | 5000 | 609  | 0 | 0 | 3 | 5612 | 89.1%  | PASS |
| opinion  | 5000 | 1993 | 0 | 0 | 2 | 6995 | 71.5%  | PASS |
| cluster  | 5000 | 836  | 0 | 0 | 3 | 5839 | 85.6%  | PASS |
| court    | 5000 | 0    | 0 | 0 | 1 | 5001 | 100.0% | PASS |

- **Target rows:** 20,000; **Achieved:** 20,000 (exactly 5,000 per endpoint per D-05)
- **Runtime:** 8h 56m wall-clock (~0.85 req/s effective rate, well under CourtListener's 5000/hr = 1.39 req/s ceiling — no 429s observed)
- **Every endpoint cleared the ≥70% pilot gate.** Opinion at 71.5% is closest (expected — 1–15M ID range is the broadest; 1993 404s reflect sparse ID population in the upper range)
- **Zero 403s** (no accidental gated-endpoint contact, confirming D-11 host allowlist + 4-endpoint scope held)
- **Zero 429s** — pacing at 0.8s+0.4s jitter was right-sized; D-11 5-consecutive hard-stop was never triggered

### Trace characteristics (spot-checked, not a full workload_stats — that's Plan 03-03)

- Opinion size distribution shows clear 80/20 bimodality: most opinion rows ~115 bytes (the `?fields=id,absolute_url,type,date_filed,author_id` metadata strip response), ~20% fall between 1KB and 500KB (full plain_text). D-01 through D-04 implementation verified end-to-end.
- Dockets ~1.5–3KB (matches STACK.md §Court records estimate)
- Clusters ~3–9KB (matches STACK.md estimate)
- Courts ~0.6–1KB (matches STACK.md estimate of ~1KB; only ~3000 unique court IDs drawn from the static list)

## Commit details

**Commit:** `f9b60de` — `feat(03-02): collect 20K CourtListener trace + per-endpoint report (TRACE-06)`

**Files changed:** 3 files, +20,019 / −2

- `.gitignore` — rewrote `traces/` → `traces/*` and `results/` → `results/**` so the `!` exemptions below can take effect (git's "parent directory exclusion prevents re-include" rule meant the original `traces/` pattern blocked the `!traces/court_trace.csv` exemption). Also added exemptions for `traces/court_pilot.csv`, `results/court/`, `results/court/pilot_report.txt` to normalize the state for already-tracked Phase 1 files.
- `traces/court_trace.csv` — 20,001 lines, 708KB; committed per D-15
- `results/court/collection_report.txt` — 7 lines (1 header + 4 endpoint rows + gate + verdict); committed per D-15

## Deviation: .gitignore pattern rewrite

**What changed vs the plan:** Plan 03-02 Step 4a was written to append `!traces/court_trace.csv` and `!results/court/collection_report.txt` to the existing `.gitignore` (directly below the `traces/` and `results/` lines). That plan was authored assuming git's `!` exemption would override the parent-directory exclude — which is NOT how gitignore works. Per `gitignore(5)`: **"It is not possible to re-include a file if a parent directory of that file is excluded."**

The fix: rewrite the parent-directory patterns from `traces/` to `traces/*` and `results/` to `results/**`, which match directory *contents* (not the directory itself as a whole) — then `!path/to/file` correctly re-includes specific files.

**Verified fix works:**
- `git check-ignore -v traces/court_trace.csv` → matched rule `!traces/court_trace.csv` (the negation wins)
- `git status` shows both files as tracked, not ignored
- Already-tracked Phase 1 files (`traces/court_pilot.csv`, `results/court/pilot_report.txt`) were re-listed as `!` entries to normalize — they remain tracked exactly as before; no behavior change for them
- `git ls-files traces/ results/court/` now returns the 4 committed files (2 from Phase 1 + 2 from Phase 3)

**Impact:** None for D-15's intent (trace + report are committed); minor `.gitignore` pattern refinement that's net-cleaner than the original rules. Future phases that want to commit specific `traces/*` or `results/**` files can add their own `!` entries without further pattern changes.

## Self-Check: PASSED

- [x] `wc -l traces/court_trace.csv` returns 20001
- [x] `cat results/court/collection_report.txt` shows all 4 endpoints PASS
- [x] `grep -qE "^!traces/court_trace\.csv$" .gitignore` — exemption line present
- [x] `grep -qE "^!results/court/collection_report\.txt$" .gitignore` — exemption line present
- [x] `git ls-files traces/court_trace.csv | grep -q court_trace` — file tracked in git
- [x] `git ls-files results/court/collection_report.txt | grep -q collection_report` — file tracked in git
- [x] `git ls-files .gitignore | grep -q .gitignore` — .gitignore change committed
- [x] TRACE-06 marked complete in REQUIREMENTS.md with traceability to f9b60de
- [x] ROADMAP Phase 3 row updated to 2/3 In progress
- [x] Plan 03-02 checkbox `[x]` with completion details

## Next

Plan 03-03 (Wave 3) — workload pre-characterization + 6-policy replay-Zipf sweep. Uses the trace file committed here + the `--workload court` Makefile parameterization. Expected ~10 minutes wall-clock.
