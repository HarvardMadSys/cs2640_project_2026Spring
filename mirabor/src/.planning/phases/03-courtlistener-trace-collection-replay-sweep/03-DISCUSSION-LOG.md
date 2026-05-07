# Phase 3: CourtListener Trace Collection & Replay Sweep — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-19
**Phase:** 03-courtlistener-trace-collection-replay-sweep
**Areas discussed:** Plain_text handling, Endpoint distribution, Resumability + failed-request handling, Sweep parameters + pre-characterization

---

## Plain_text handling

### Q1: How should the 80/20 mix be implemented in code?

| Option | Description | Selected |
|--------|-------------|----------|
| ?fields= on opinion requests (Recommended) | Per-request: 80% of opinion calls use ?fields=... stripping plain_text; 20% fetch full object | ✓ |
| 80% non-opinion, 20% opinion mix | Endpoint-level: 80% of 20K hit dockets+clusters+courts; 20% hit /opinions/ full | |
| ?omit=plain_text for 80% | Inverse syntax of ?fields=; portability uncertain across v4 | |

**User's choice:** `?fields=` on opinion requests
**Rationale:** Per-request control keeps the opinion endpoint centrally interesting; dockets/clusters/courts stay default.

### Q2: Which field set counts as 'metadata only' (the 80%)?

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal: id, absolute_url, type, date_filed, author_id (Recommended) | Tight strip ~1–5KB; max contrast with 20% | ✓ |
| Everything except plain_text + html fields | Keep all relational metadata (cluster, author, joined_by) minus 3 body fields | |
| Let Claude pick during implementation | Deferred to plan | |

**User's choice:** Minimal field set
**Rationale:** Maximizes size-distribution contrast for byte-MRC analysis.

### Q3: Does 80/20 apply only to /opinions/, or to other endpoints too?

| Option | Description | Selected |
|--------|-------------|----------|
| Opinions only (Recommended) | Matches ROADMAP literal; dockets/clusters/courts always default | ✓ |
| Apply to all four endpoints | Uniform policy but diverges from spec | |
| Opinions + clusters | Clusters include sub_opinions arrays with bodies | |

**User's choice:** Opinions only
**Rationale:** Matches ROADMAP spec exactly; narrow control surface.

### Q4: When plain_text is empty on a 20%-full request, what goes in CSV?

| Option | Description | Selected |
|--------|-------------|----------|
| Actual response byte length regardless (Recommended) | size = len(response.content); no retry | ✓ |
| Retry with html field if plain_text null | Fallback to html_with_citations | |
| Skip opinions with empty text, re-roll ID | Cleaner size story, more requests | |

**User's choice:** Actual response byte length regardless
**Rationale:** No retry complexity; size distribution is what the API returns.

---

## Endpoint distribution

### Q1: Endpoint distribution for the 20K production trace?

| Option | Description | Selected |
|--------|-------------|----------|
| Equal 25% per endpoint (Recommended) | 5K each for dockets/opinions/clusters/courts | ✓ |
| Opinion-weighted (40/30/20/10) | 8K opinions + 6K dockets + 4K clusters + 2K courts | |
| Realistic client mix (50/30/15/5) | 10K dockets + 6K opinions + 3K clusters + 1K courts | |
| You decide during implementation | Deferred | |

**User's choice:** Equal 25% per endpoint
**Rationale:** Clean symmetry with pilot; predictable workload characterization.

### Q2: Opinion's 74% pilot success — narrow ID range for production?

| Option | Description | Selected |
|--------|-------------|----------|
| Keep pilot ranges, accept 26% 404s (Recommended) | 50-request pilot is statistically shaky; tune only if first 500 show <60% | ✓ |
| Narrow opinion ID range to 1–10M | Drop top 5M of current 1–15M range | |
| Sample IDs from successful pilot opinions | Seed-based neighborhood draw; loses randomness | |

**User's choice:** Keep pilot ranges
**Rationale:** 404s are skipped per D-10; natural variance settles at 5K sample size.

---

## Resumability + failed-request handling

### Q1: Does '≥20K-request trace' mean 20K successful rows or 20K issued requests?

| Option | Description | Selected |
|--------|-------------|----------|
| 20K successful trace rows (Recommended) | Keep issuing until CSV has ≥20K rows (~24K issued) | ✓ |
| 20K issued requests; ~17K successful | Fixed runtime bound | |
| Configurable via --target-rows flag | Both; flag-driven | |

**User's choice:** 20K successful trace rows
**Rationale:** Matches Congress's 20,692-row committed CSV precedent and TRACE-06 literal intent.

### Q2: CSV write strategy during the ~6hr run?

| Option | Description | Selected |
|--------|-------------|----------|
| Append per row + flush (Recommended) | Match collect_trace.py pattern; per-row fsync for crash recovery | ✓ |
| Batch every 500 rows to temp file, rename | Fewer fsyncs; risk losing 500 rows | |
| In-memory until end, one atomic write | Simplest code; risk losing everything | |

**User's choice:** Append per row + flush

### Q3: Crash recovery behavior?

| Option | Description | Selected |
|--------|-------------|----------|
| --resume flag that reads existing CSV and continues (Recommended) | Opt-in resume; count existing rows, continue with fresh IDs | ✓ |
| No resume; re-run from scratch overwrites | 6hr restart cost | |
| Resume by default (auto-detect existing CSV) | Risks silent extension of stale CSV | |

**User's choice:** --resume flag
**Rationale:** Explicit opt-in prevents accidental continuation of a stale run.

### Q4: Failed requests (404/403/429) — what goes in the trace?

| Option | Description | Selected |
|--------|-------------|----------|
| Skipped entirely (Recommended) | Match Phase 1 D-08; success = 200 + size > 0 | ✓ |
| Included with size=0 | Simulator sees failed fetches as zero-byte misses | |
| 404s only (not 403/429) with size=0 | Middle ground | |

**User's choice:** Skipped entirely
**Rationale:** Phase 1 precedent; Congress trace semantics.

### Q5: 429 handling beyond Retry-After?

| Option | Description | Selected |
|--------|-------------|----------|
| Honor Retry-After then exponential backoff on repeat (Recommended) | First 429: Retry-After; second: +30s; third: +90s; after 5 consecutive: hard-stop | ✓ |
| Fixed 60s sleep on any 429 | Simpler | |
| Abort on first 429 | Strict | |

**User's choice:** Exponential ramp with 5-consecutive hard-stop

---

## Sweep parameters + pre-characterization

### Q1: Sweep grid — match Congress or adapt?

| Option | Description | Selected |
|--------|-------------|----------|
| Identical to Congress (Recommended) | Same α {0.6..1.2} and cache fractions; zero simulator changes | ✓ |
| Adapt cache fractions to court's byte budget | Cleaner MRCs; harder cross-compare | |
| Wider α grid {0.4..1.4} on court only | More coverage; asymmetric | |

**User's choice:** Identical to Congress
**Rationale:** Apples-to-apples for Phase 5's cross-workload compare.

### Q2: Workload pre-characterization — Phase 3 or Phase 5?

| Option | Description | Selected |
|--------|-------------|----------|
| In Phase 3, lightweight (Recommended) | workload_stats.json via src/workload_stats.cpp; catches M5 pre-sweep | ✓ |
| Defer to Phase 5 compare_workloads.py | Leaner Phase 3 scope | |
| Phase 3 emits stats; Phase 5 builds compare table | Split | |

**User's choice:** In Phase 3, lightweight

### Q3: Output layout under results/court/ — mirror Congress?

| Option | Description | Selected |
|--------|-------------|----------|
| Mirror Congress exactly (Recommended) | mrc.csv, alpha_sensitivity.csv, workload_stats.json, figures/, collection_report.txt | ✓ |
| Same files + trace-provenance manifest | Add collection_manifest.json with git SHA, version | |

**User's choice:** Mirror Congress exactly
**Rationale:** Zero special-casing in downstream scripts.

---

## Trace commit policy

### Q: Commit traces/court_trace.csv to git?

| Option | Description | Selected |
|--------|-------------|----------|
| Commit traces/court_trace.csv (Recommended) | ~1.2MB; locks dataset against API drift; matches Congress precedent | ✓ |
| Don't commit; regenerate via collect_court_trace.py | Grader runs collector; 6hr + token | |
| Commit 1K sample + full in .gitignore | Middle ground | |

**User's choice:** Commit traces/court_trace.csv

---

## Claude's Discretion

- Exact v4 field-name strings for the D-02 minimal-fields set (verify during planning)
- Per-endpoint random-seed protocol inside the collector (any deterministic seed)
- Logging verbosity during the 6-hour collection (mirror collect_trace.py cadence)
- Makefile exposure for `run-sweep --workload court` (minimal change, whatever is cleanest)
- `collection_report.txt` exact format (pilot_court_trace.py's pattern is fine)

## Deferred Ideas

- Cross-workload compare table (Phase 5 ANAL-03)
- Multi-seed confidence intervals (Phase 5 ANAL-02)
- Doorkeeper ablation on court trace (Phase 4 DOOR-03)
- SHARDS on court trace (NOT Phase 4; Phase 4 uses 1M synthetic)
- v3 endpoint fallback (v4 locked)
- Dynamic endpoint mix (D-06 first-500 fallback is sufficient)
- Trace provenance manifest (reconstruct from git log if needed)
