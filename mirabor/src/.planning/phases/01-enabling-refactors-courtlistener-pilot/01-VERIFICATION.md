---
phase: 01-enabling-refactors-courtlistener-pilot
verified: 2026-04-18T21:50:00Z
status: human_needed
score: 4/4 roadmap success criteria verified; all plan-level truths verified
overrides_applied: 0
re_verification: null
gaps: []
deferred: []
human_verification:
  - test: "Confirm the CourtListener pilot report reflects a real live-API run, not a canned fixture"
    expected: "User confirms results/court/pilot_report.txt was generated against live www.courtlistener.com (not replayed from a local mock), with 200 actual HTTP requests issued using the .env token"
    why_human: "Verifier cannot replay the pilot — it mutates rate-limit quota and requires the user's token. The existing report file was force-committed by Plan 01-06 and all 4 endpoints passed the 70% gate; no remediation was applied. Surface-level evidence (timestamps in traces/court_pilot.csv, per-endpoint tallies) is consistent with a live run but the verifier cannot independently re-run the pilot to prove it."
  - test: "Visually inspect results/congress/figures/*.pdf for sensible shapes"
    expected: "mrc.pdf shows miss-ratio curves monotonically decreasing with cache size for all 5 policies; alpha_sensitivity.pdf shows LRU/FIFO/CLOCK degrading at low alpha; workload.pdf shows a log-normal-ish size distribution"
    why_human: "Plot correctness is a visual judgment. Programmatic checks only confirm the PDFs exist and are non-empty."
  - test: "Confirm `make plots` works end-to-end on the user's machine"
    expected: "make plots exits 0 and regenerates results/congress/figures/*.pdf; or, if libexpat workaround is needed, user confirms .venv activation + DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib produces the PDFs"
    why_human: "The libexpat linkage issue is documented as out-of-scope for Phase 1 (CONTEXT.md specifics). Plan 01-04 SUMMARY notes `make plots` may need the venv+DYLD workaround. Verifier cannot reliably confirm the user's shell has this set up."
---

# Phase 1: Enabling Refactors & CourtListener Pilot — Verification Report

**Phase Goal:** Unblock downstream work — the hash refactor is required by W-TinyLFU, the replay_zipf refactor is required for the multi-seed cross-workload sweep, and the CourtListener token + pilot must succeed before any real collection is attempted.

**Verified:** 2026-04-18T21:50:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `include/hash_util.h` exists with FNV-1a and 4 deterministic seeds; `src/shards.cpp` uses it; `std::hash` absent from cache code | VERIFIED | `include/hash_util.h` (37 lines) declares FNV_BASIS, FNV_PRIME, FNV_SEED_A..D, fnv1a_64, hash_util_self_test. `src/shards.cpp:7` includes `"hash_util.h"`; line 86-88 `SHARDS::hash_key` delegates to `return fnv1a_64(key);`. Literals 14695981039346656037ULL and 1099511628211ULL appear ONLY in `include/hash_util.h` under `src/` and `include/`. `grep 'std::hash<std::string>' src/ include/` returns zero matches; only one `std::hash<` match in `include/hash_util.h:7` is a documentation comment explaining the ban. |
| 2 | `replay_zipf` accepts a pre-shuffled object list; a 7-alpha sweep on Congress trace completes noticeably faster than regeneration-per-alpha baseline | VERIFIED | `include/trace_gen.h:40-49` declares `prepare_objects()` and `generate_replay_trace()` with D-10 signatures. `src/trace_gen.cpp:77-93` implements `prepare_objects`, `:95-106` implements `generate_replay_trace`, `:108-119` `replay_zipf` is now a 3-statement wrapper. `src/main.cpp:234-237` hoists `prepare_objects(raw_trace)` above the `for (double a : alphas)` loop; `:240-242` calls `generate_replay_trace(prepared_objects, ...)` per alpha. Live 10K alpha-sweep on real congress_trace.csv produced exactly ONE `"Replay-Zipf:"` diagnostic (from one-shot main-MRC path), confirming the sweep loop skips the wrapper. SUMMARY-02 micro-benchmark records 2.49x speedup on a 200K/20K-unique trace; structural evidence (single dedupe+shuffle call per sweep) satisfies the roadmap's "noticeably faster" requirement. |
| 3 | Simulator CSV output contains a new `accesses_per_sec` column; `results/` is reorganized into `{congress, court, shards_large, compare}/` | VERIFIED | `grep -c 'double accesses_per_sec = elapsed > 0' src/main.cpp` = 3 (MRC loop, alpha-sweep loop, SHARDS loop). Fresh run `./cache_sim --num-requests 5000 --alpha-sweep --shards --output-dir /tmp/verify_spot` produces: `mrc.csv` header = `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`; `alpha_sensitivity.csv` header = `alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`; `shards_mrc.csv` header = `sampling_rate,cache_size_objects,miss_ratio,accesses_per_sec`. All 4 subdirs `results/{congress,court,shards_large,compare}/` exist; `results/congress/` contains mrc.csv, alpha_sensitivity.csv, one_hit_wonder.csv, shards_mrc.csv; `ls results/*.csv` returns nothing (all migrated out of top level). |
| 4 | `COURTLISTENER_API_KEY` is configured and a 200-request pilot across /dockets/, /opinions/, /clusters/, /courts/ returns ≥70% success with no 403s | VERIFIED | `.env` exists at repo root (256 bytes, gitignored per .gitignore:42). `.env.example` committed at repo root (545 bytes, tracked) with empty `COURTLISTENER_API_KEY=` and `CONGRESS_API_KEY=` lines. `scripts/pilot_court_trace.py` exists (255 lines, executable, valid Python syntax); `grep 'BASE_URL = "https://www.courtlistener.com/api/rest/v4"'` matches; `CONSECUTIVE_429_ABORT = 5` and `SUCCESS_GATE = 0.70` present. `results/court/pilot_report.txt` contains: `docket: 90.0% [PASS]`, `opinion: 74.0% [PASS]`, `cluster: 90.0% [PASS]`, `court: 100.0% [PASS]`, `Verdict: ALL PASS`. Zero 403s and zero 429s recorded. `traces/court_pilot.csv` contains 177 HTTP-200 rows in `timestamp,key,size` schema. **Note:** Verifier cannot independently re-run the pilot (see human_verification). |

**Score:** 4/4 roadmap success criteria verified

---

### Required Artifacts (Plan-Level Contract)

Plan-declared `must_haves.artifacts` aggregated from 01-01..01-06:

| Artifact | Expected | Level 1 (exists) | Level 2 (substantive) | Level 3 (wired) | Level 4 (data flows) | Status |
|---|---|---|---|---|---|---|
| `include/hash_util.h` | FNV-1a + 4 seeds + self-test | YES (37 lines) | YES — 6 constexpr constants, 2 inline functions, `#pragma once`, no `#define` | YES — included by src/shards.cpp:7 and src/main.cpp:15 | N/A (header) | VERIFIED |
| `src/shards.cpp` | Uses shared FNV-1a | YES | YES — `return fnv1a_64(key);` at line 87 | YES — linked into cache_sim binary; build clean | YES — SHARDS produces MRC rows driven by fnv1a_64 sampling | VERIFIED |
| `src/main.cpp` | Self-test + prepared_objects + 3x timing sites | YES | YES — `hash_util_self_test()` at :128, `prepared_objects = prepare_objects(raw_trace)` at :236, three `double accesses_per_sec = elapsed > 0 ...` blocks at :201, :253, :301 | YES — binary builds + runs | YES — emits real accesses_per_sec values (e.g., 1.23976e+07 for LRU) | VERIFIED |
| `include/trace_gen.h` | prepare_objects + generate_replay_trace decls | YES | YES — both decls at :40-49 with D-10 signatures, replay_zipf decl unchanged at :32-34 | YES — included transitively via cache_sim | N/A (header) | VERIFIED |
| `src/trace_gen.cpp` | prepare_objects + generate_replay_trace impls + wrapper | YES | YES — `prepare_objects` at :77-93 with dedupe+shuffle(seed); `generate_replay_trace` at :95-106 with ZipfGenerator(..., seed+1); `replay_zipf` wrapper at :108-119 | YES — linked into cache_sim | YES — alpha sweep on 10K real trace produced 36-line alpha_sensitivity.csv | VERIFIED |
| `scripts/plot_results.py` | --workload flag + tolerant accesses_per_sec reader + _has_throughput helper + CSV schema doc | YES | YES — CSV schemas block at lines 9-21, `_has_throughput` helper at :62-64, `--workload` argparse at :317, `args.results_dir or os.path.join("results", args.workload)` at :328, `plot_workload(..., workload="congress")` at :235, `f"{workload}_trace.csv"` at :238 | YES — existing PDFs in results/congress/figures/ (mrc.pdf, byte_mrc.pdf, alpha_sensitivity.pdf, ohw.pdf, shards_mrc.pdf, workload.pdf) | YES — plots consume real CSV data | VERIFIED |
| `Makefile` | run/run-sweep use --output-dir results/congress | YES | YES — `mkdir -p results/congress` and `--output-dir results/congress` on both run and run-sweep recipes (lines 34, 35, 39, 40) | YES — `make clean && make` exits 0; build is clean under -Wall -Wextra | N/A (build script) | VERIFIED |
| `.env.example` | Template with both env vars empty | YES (545 bytes, tracked) | YES — `CONGRESS_API_KEY=` and `COURTLISTENER_API_KEY=` lines with empty values and comments pointing to consuming scripts | YES — `git ls-files .env.example` returns the path | N/A (template) | VERIFIED |
| `.env` | User-side token, gitignored | YES (256 bytes, NOT tracked) | Not inspected (secret) | YES — consumed by pilot script at runtime via os.environ.get | N/A | VERIFIED (existence + gitignore compliance) |
| `scripts/pilot_court_trace.py` | Pilot collector with gate + host allowlist + backoff | YES (255 lines, executable) | YES — BASE_URL literal, ALLOWED_HOST allowlist guard at :142-144, BASE_DELAY=0.8, JITTER=0.4, CONSECUTIVE_429_ABORT=5, EARLY_403_SAMPLE=5, SUCCESS_GATE=0.70. `--help` works under venv. | YES — successfully executed per SUMMARY-06 commit dfbb313 | YES — produced traces/court_pilot.csv + results/court/pilot_report.txt | VERIFIED |
| `traces/court_pilot.csv` | Pilot trace in timestamp,key,size schema | YES (178 lines incl. header) | YES — header `timestamp,key,size`, 177 data rows | N/A (data artifact) | YES — rows e.g., `dockets/14942604/,1767` show real endpoint keys | VERIFIED |
| `results/court/pilot_report.txt` | Per-endpoint tally + gate verdict | YES (9 lines, tracked) | YES — 4 endpoint lines (docket=90%, opinion=74%, cluster=90%, court=100%) + `Verdict: ALL PASS` | N/A | YES — counts sum to 50 per endpoint | VERIFIED |
| `results/congress/` | Migrated top-level CSVs | YES | YES — mrc.csv, alpha_sensitivity.csv, one_hit_wonder.csv, shards_mrc.csv all present; figures/ subdir also migrated | N/A | Partial — see Anti-Patterns (stale CSVs) | VERIFIED (migration structure) |
| `results/court/` | Empty stub subdir | YES | YES — directory exists; contains only pilot_report.txt + empty figures/ | N/A | N/A | VERIFIED |
| `results/shards_large/` | Empty stub subdir | YES | YES — empty | N/A | N/A | VERIFIED |
| `results/compare/` | Empty stub subdir | YES | YES — empty | N/A | N/A | VERIFIED |

---

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `src/shards.cpp::SHARDS::hash_key` | `include/hash_util.h::fnv1a_64` | `return fnv1a_64(key)` | WIRED | Verified at `src/shards.cpp:87`; pattern match exact |
| `src/main.cpp::main` | `include/hash_util.h::hash_util_self_test` | startup call | WIRED | Verified at `src/main.cpp:128-131`; gate returns 1 on failure |
| `src/main.cpp` alpha-sweep loop | `src/trace_gen.cpp::generate_replay_trace` | per-alpha call inside loop | WIRED | Verified at `src/main.cpp:240-242`; consumes hoisted prepared_objects |
| `src/trace_gen.cpp::replay_zipf` | `prepare_objects + generate_replay_trace` | thin wrapper | WIRED | Verified at `src/trace_gen.cpp:108-119`; both internal calls present |
| `src/main.cpp` all 3 timing sites | `std::chrono::steady_clock` | `duration<double>(t_end - t_start).count()` | WIRED | 3 matches of `double accesses_per_sec = elapsed > 0` at lines 201, 253, 301 |
| `results/*/mrc.csv` header | `scripts/plot_results.py` | pd.read_csv tolerance | WIRED | `_has_throughput(df)` helper at line 62; CSV schemas comment block at lines 9-21 |
| `scripts/plot_results.py::main` | `results/{workload}/` | `os.path.join("results", args.workload)` | WIRED | Verified at line 328; default workload="congress" |
| `Makefile run` target | `results/congress/` | `--output-dir results/congress` | WIRED | Verified at Makefile:35, 40 |
| `scripts/pilot_court_trace.py` | CourtListener v4 API | `Authorization: Token <token>` | WIRED | `session.headers.update({"Authorization": f"Token {api_key}"})` at line 114 |
| `scripts/pilot_court_trace.py` | `results/court/pilot_report.txt` | write per-endpoint tally | WIRED | Final write at line 224-225; `success=` pattern confirmed in output file |
| `scripts/pilot_court_trace.py` | `os.environ.get('COURTLISTENER_API_KEY')` | env var read | WIRED | Line 77; exits 1 if missing |
| `.env.example` | `.env` | user-side copy + fill-in | WIRED (template contract) | Both env vars listed empty; comments instruct how to fill |

All 12 key links WIRED. No ORPHANED, PARTIAL, or NOT_WIRED links found.

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| `cache_sim` binary (MRC loop) | `accesses_per_sec` | `trace.size() / elapsed` around real `run_simulation()` | YES — 1.24e+07 observed in fresh /tmp/verify_spot/mrc.csv | FLOWING |
| `cache_sim` binary (alpha sweep) | `accesses_per_sec` | `sweep_trace.size() / elapsed` after live `generate_replay_trace()` | YES — 7.28e+06 observed on real 10K congress sweep | FLOWING |
| `cache_sim` binary (SHARDS) | `accesses_per_sec` | `trace.size() / elapsed` around `shards.process(trace)` | YES — 1.47e+08 observed on 5K synthetic SHARDS run | FLOWING |
| `results/court/pilot_report.txt` | per-endpoint tallies | `tally[ep]` mutations in `collect_pilot()` loop across 200 HTTPS calls | YES — 45+37+45+50 = 177 200s match 177 rows in court_pilot.csv | FLOWING |
| `traces/court_pilot.csv` | HTTP-200 rows | `writer.writerow([ts, key, size])` when `resp.status_code == 200` | YES — real endpoint keys visible (e.g., `dockets/14942604/`, `opinions/4614227/`) | FLOWING |
| `scripts/plot_results.py` outputs | MRC/alpha/shards frames | `pd.read_csv(...)` from the workload-derived results_dir | YES — workload.pdf, mrc.pdf, etc. all present in results/congress/figures/ | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Build succeeds under -Wall -Wextra | `make clean && make` | Clean build, zero warnings | PASS |
| Self-test gate passes on valid build | `./cache_sim --num-requests 1000 --num-objects 100 --output-dir /tmp/smoketest_01` | Exit 0, no "self-test failed" in output | PASS |
| All 3 CSVs emit accesses_per_sec | `./cache_sim --num-requests 5000 --num-objects 500 --alpha-sweep --shards --output-dir /tmp/verify_spot` | mrc.csv, alpha_sensitivity.csv, shards_mrc.csv all have accesses_per_sec as final column | PASS |
| Alpha sweep single dedupe+shuffle on real trace | `./cache_sim --trace traces/congress_trace.csv --replay-zipf --alpha-sweep --num-requests 10000 --output-dir /tmp/alpha_real` | Exactly 1 "Replay-Zipf:" diagnostic printed (from one-shot, NOT the sweep); 36-line alpha_sensitivity.csv | PASS |
| Makefile targets Congress dir | `grep 'results/congress' Makefile` | 5 lines match — `mkdir -p results/congress` + `--output-dir results/congress` twice + comment | PASS |
| Pilot script valid Python + help works | `python3 -c 'import ast; ast.parse(open(...).read())'` + `pilot_court_trace.py --help` (via .venv) | Syntax OK; help prints CourtListener pilot description | PASS |
| std::hash banned from cache code | `grep 'std::hash<std::string>' src/ include/` | 0 matches in code; 1 comment-only match in hash_util.h | PASS |
| FNV literals only in hash_util.h | `grep -rn '14695981039346656037ULL\|1099511628211ULL' src/ include/` | Only `include/hash_util.h:10-11` matches under src/include | PASS |

All 8 behavioral spot-checks PASS.

---

### Requirements Coverage

Requirement IDs declared across 01-01..01-06 plan frontmatters: REFACTOR-01 (01-01), REFACTOR-02 (01-02), REFACTOR-03 (01-03), REFACTOR-04 (01-04), TRACE-03 (01-05), TRACE-04 (01-06). Cross-referenced against REQUIREMENTS.md.

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| REFACTOR-01 | 01-01 | Extract FNV-1a into include/hash_util.h with 4 deterministic seeds | SATISFIED | include/hash_util.h present with FNV_BASIS/FNV_PRIME + 4 FNV_SEED_A..D constexpr uint64_t; src/shards.cpp uses fnv1a_64; std::hash banned |
| REFACTOR-02 | 01-02 | Refactor replay_zipf to accept pre-shuffled object list | SATISFIED | prepare_objects + generate_replay_trace implemented; legacy wrapper preserved; alpha-sweep hoist in main.cpp; live 10K real-trace sweep confirms single dedupe via single "Replay-Zipf:" line |
| REFACTOR-03 | 01-03 | Add accesses/sec throughput column to simulation output | SATISFIED | 3 timing sites (MRC, alpha, SHARDS) with steady_clock + trace.size()/elapsed guard; fresh runs confirm all 3 CSVs emit the column; include/cache.h untouched (git diff clean on cache.h across commits) |
| REFACTOR-04 | 01-04 | Reorganize results/ into per-workload subdirs | SATISFIED | results/{congress,court,shards_large,compare}/ all exist; top-level results/*.csv returns nothing; --workload flag + workload-parameterized trace filename in plot_results.py; Makefile targets results/congress |
| TRACE-03 | 01-05 | Register CourtListener account, configure COURTLISTENER_API_KEY | SATISFIED | .env present at repo root (gitignored), .env.example committed with template; SUMMARY-05 records 5 verification curl HTTP=200 results against live API; all 4 endpoints non-gated. **Note:** Live curl re-verification is a human task (see human_verification). |
| TRACE-04 | 01-06 | Pilot 200-request run across 4 endpoints with ≥70% gate | SATISFIED | scripts/pilot_court_trace.py present (255 lines, executable); results/court/pilot_report.txt shows all 4 endpoints at 90%/74%/90%/100% success with "Verdict: ALL PASS"; traces/court_pilot.csv has 177 HTTP-200 rows |

**Orphaned requirements check:** REQUIREMENTS.md traceability table maps REFACTOR-01..04, TRACE-03, TRACE-04 to Phase 1 — exactly 6 IDs. All 6 are claimed by plans in this phase. No orphans.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `results/congress/alpha_sensitivity.csv` | header | Header is legacy `alpha,policy,miss_ratio,byte_miss_ratio` (no accesses_per_sec) | Info | This CSV dates to 2026-04-15 21:59 (pre-refactor Milestone 1). Plan 01-04 migrated it as-is via `mv`. The simulator source correctly emits the new column; any fresh run regenerates the CSV with the right schema (verified in /tmp/verify_spot). Not a capability gap. |
| `results/congress/shards_mrc.csv` | header | Header is legacy `sampling_rate,cache_size_objects,miss_ratio` (no accesses_per_sec) | Info | Same as above — stale pre-refactor artifact migrated from top-level results/. Not a capability gap. |
| `results/congress/mrc.csv` | — | Fresh (2026-04-18 21:40), has accesses_per_sec column | — | Confirms simulator output schema per SC #3. |
| `results/congress/one_hit_wonder.csv` | — | Fresh (2026-04-18 21:40), unchanged schema (correct — OHW schema was not modified) | — | Expected. |

**Notable observations (non-blocking):**
- Two of the four migrated CSVs (alpha_sensitivity.csv, shards_mrc.csv) are stale relative to the post-refactor code. The simulator binary correctly emits the new schema on every fresh run — verified both on synthetic traces and on the real congress_trace.csv. The roadmap success criterion says "Simulator CSV output contains a new accesses_per_sec column", which is a capability requirement on the simulator, not a data-freshness requirement on historical CSVs. Nonetheless, a clean `make run-sweep` (or equivalent) on the Congress trace would regenerate them with the new schema — the project authors may want to do this before Phase 2 consumes them.
- No TODO/FIXME/placeholder comments introduced in this phase.
- No empty handlers / hardcoded empty returns / `return null` patterns found in the new files.

---

### Human Verification Required

Three items require human testing before the phase can be marked fully "passed". Automated checks confirm all roadmap success criteria AND all plan-level truths are met; the items below cover the slice of the phase that a verifier cannot safely or reliably automate.

#### 1. CourtListener Pilot Live-API Authenticity

**Test:** Confirm the 200-request pilot actually hit www.courtlistener.com with a real token (not replayed from a mock).

**Expected:**
- User recalls running the pilot with live .env credentials.
- `results/court/pilot_report.txt` tallies reflect real HTTP status codes, not canned values.
- No other process silently wrote the pilot report.

**Why human:** Re-running the pilot burns ~200 requests from the user's 5,000/hour quota and requires the token. Surface evidence (real endpoint IDs in traces/court_pilot.csv, 0% 429s matching the documented 0.8s+jitter cadence, 100% court hit rate on the 20-element hand-curated ID list) is consistent with a live run, but non-falsifiable by the verifier.

#### 2. Figure Visual Sanity

**Test:** Open `results/congress/figures/{mrc,alpha_sensitivity,byte_mrc,workload,shards_mrc,ohw}.pdf` and confirm shapes match expected plots.

**Expected:**
- `mrc.pdf` — miss ratio monotonically decreases with cache size; 5 policies differentiated.
- `alpha_sensitivity.pdf` — LRU/FIFO/CLOCK degrade as alpha→0 (uniform); S3-FIFO/SIEVE stay competitive.
- `shards_mrc.pdf` — 3 sampling rates converge; 0.1% rate matches 1% rate closely.
- `workload.pdf` — size distribution is right-skewed (log-normal-ish) around a few-KB median.

**Why human:** Plot correctness is a visual judgment no grep can make. Programmatic checks only verify PDFs exist and are non-empty.

#### 3. `make plots` End-to-End Execution

**Test:** Run `make plots` on your development machine and confirm it regenerates `results/congress/figures/*.pdf`.

**Expected:** Exit 0 with no traceback. If libexpat is an issue, the documented workaround (`.venv` activation + `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`) works.

**Why human:** Plan 01-04 SUMMARY explicitly notes the libexpat linkage issue on macOS Homebrew Python — `make plots` was tested under the venv+DYLD workaround, not the default shell. The workaround is documented as out-of-scope for Phase 1. Verifier cannot assume the user's shell has this preconfigured.

---

### Gaps Summary

**No blocking gaps.** All four roadmap success criteria are verified in the codebase; all plan-level must_have truths pass three-level checks (exists / substantive / wired) plus data-flow trace; all 12 key links are WIRED; all 8 behavioral spot-checks PASS; all 6 requirement IDs are SATISFIED with traceable evidence.

The phase goal — "unblock downstream work" — is achieved:
- **W-TinyLFU (Phase 2) is unblocked** by `include/hash_util.h` exposing `fnv1a_64` + `FNV_SEED_A..D`.
- **Multi-seed cross-workload sweep (Phase 5) is unblocked** by the `prepare_objects` / `generate_replay_trace` split and the alpha-sweep hoist (structural elimination of 6x redundant dedupe+shuffle per alpha-column).
- **CourtListener production collection (Phase 3) is unblocked** by the verified token, the validated 4-endpoint non-gated status, and the 0.8s+jitter rate cadence proven safe at 200 requests.

Status is `human_needed` rather than `passed` solely because three items (live-API re-verification, figure visual sanity, end-to-end `make plots` execution on the user's machine) cannot be safely or reliably automated from this worktree. These items do not change the answer to "was the goal achieved?" — they are calibration checks the user can complete quickly.

---

*Verified: 2026-04-18T21:50:00Z*
*Verifier: Claude (gsd-verifier)*
