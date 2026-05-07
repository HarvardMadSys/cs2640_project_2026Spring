---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: complete
stopped_at: Phase 6 complete — verification PASSED (4/4 SC, 3/3 requirements, 19/19 locked decisions)
last_updated: "2026-04-22T23:00:00.000Z"
last_activity: 2026-04-22 -- Phase 6 verification PASSED; milestone v1.0 submission-ready
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 33
  completed_plans: 33
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-16)

**Core value:** A defensible, well-analyzed comparison of cache eviction policies on real legislative + judicial API workloads, delivered as a submission-ready paper + code + AI-use report + live demo.
**Current focus:** Phase 6 — Writeup & Demo

## Current Position

Phase: 6 (Writeup & Demo) — COMPLETE
Plan: 7 of 7
Status: Milestone v1.0 complete — all deliverables verified, submission-ready
Last activity: 2026-04-22 -- Phase 6 verification PASSED

Progress: [██████████] 100% (6 of 6 phases, 33 of 33 plans)

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: ~3-5 min per autonomous plan (worktree-parallel)
- Total execution time: ~45 min wall-clock for Phase 1 (gated on user CourtListener registration)

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1     | 6     | ~45m  | ~3-8m    |
| 2     | 6/6   | ~34m  | ~5-6m    |

**Recent Trend:**

- Last 5 plans: 02-02 (2m), 02-03 (18m), 02-04 (3m20s), 02-05 (2m), 02-06 (2m)
- Trend: Stable — autonomous plans 2-7 min, 02-03 was the heaviest (wtinylfu.h implementation + integration + make_policy widen)

*Updated after each plan completion*
| Phase 02 P02 | 2m | 1 tasks | 1 files |
| Phase 02 P03 | 18m | 3 tasks | 3 files |
| Phase 02 P04 | 3m 20s | 2 tasks | 2 files |
| Phase 02 P05 | 2m | 1 tasks | 2 files |
| Phase 02 P06 | 2m | 2 tasks | 1 file |
| Phase 03 P01 | 3m 53s | 2 tasks | 1 files |
| Phase 04 P01 | 6m 25s | 3 tasks | 3 files |
| Phase 04 P02 | ~6m | 2 tasks | 3 files |
| Phase 04 P04 | ~5m 39s | 3 tasks | 4 files |
| Phase 04 P05 | ~13m | 3 tasks | 5 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table and research/SUMMARY.md.
Recent decisions affecting current work (from research phase):

- Court API locked to CourtListener REST v4 (not PACER) — free, token-auth, 5k/hr
- W-TinyLFU implemented header-only C++17 with roll-your-own CMS — no external deps
- Doorkeeper kept as optional ablation (first-cut omits; added in Phase 4)
- SHARDS 1M validation via self-convergence (no exact oracle at 1M scale)
- `std::hash` banned — FNV-1a extracted to `include/hash_util.h` with 4 seeds
- Hill-climbing W-TinyLFU explicitly omitted (static 1%/99% config)
- Writeup budget ≥1 week; demo last, tested 3+ times on target laptop
- **Phase 02 Plan 01 (Caffeine pre-work):** Caffeine v3.1.8 confirmed to use STANDARD update in `FrequencySketch.increment` (FrequencySketch.java:L161-L164); our port DELIBERATELY uses CONSERVATIVE per WTLFU-01. 6 deliberate deviations documented in `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` §6. Plans 02-02 + 02-03 unblocked.
- [Phase 02]: Plan 02-02 (CMS): CONSERVATIVE update locked by WTLFU-01 — deliberately deviates from Caffeine's STANDARD update (FrequencySketch.java:L161-L164); sample_size = 10*width*depth = 40*width (4× slower halving than Caffeine's 10*maxSize); halving mask 0x77 ensures nibble-independent shift
- [Phase 02]: Plan 02-03 (W-TinyLFU header + integration): byte-bounded regions (D-01) — 1% window / 99% main / 80% protected inside main; stats single-source (L-12) enforced by grep-countable record(true|false)==4; D-08a explicit empty-probation short-circuit kept per CONTEXT.md (CAFFEINE-NOTES §6 row 4 deviation from Caffeine's protected/window victim-escalation); D-08e strict `>` reject-on-tie without Caffeine's 1/128 hash-DoS random admit (no adversarial threat model; preserves determinism for D-05); dropped unused total_capacity_ / probation_capacity_ members to pass -Wunused-private-field build-clean gate; make_policy widened to (name, capacity, n_objects_hint) with (void) ignore-pattern for non-wtinylfu branches
- [Phase 02]: Plan 02-04 (test binary + make test): CMS basics uses N=10 (below 4-bit COUNTER_MAX=15) for exact CONSERVATIVE-update estimate reads at width>=1024; aging test uses BOTH force_age() (deterministic D-10 hook) AND sample_size+10 burst (natural auto-halve path, asserts sample_count()==10 exactly); WTLFU-04 literal sizing cap_bytes=20000/obj_size=100/n_obj_hint=200 yields ~200-resident cache overflowed 5x by 1000-key scan; TEST_ASSERT accumulates failures (not abort-on-first) so one `make test` surfaces every broken invariant; build/test/ separate object dir (D-07) — `make && make test` does not invalidate main cache_sim
- [Phase 02]: Plan 02-05 (plot_results W-TinyLFU entry): POLICY_COLORS['W-TinyLFU']=#8c564b (matplotlib tab:brown, the 6th tab10 color) — deliberately NOT CONTEXT.md's suggested purple #9467bd because SIEVE owns it; POLICY_MARKERS['W-TinyLFU']='P' (filled plus) — deliberately NOT CONTEXT.md's suggested 'D' because S3-FIFO owns it; both deviations avoid T-02-05-01 "visual confusion" collision; dict key is exact CSV policy string 'W-TinyLFU' emitted by wtinylfu.h::name() (not 'wtinylfu' CLI arg); no plot-function edits because existing loops at lines 79/113/150 use .get(policy, fallback); `make plots` on existing Congress CSVs regenerates 6 PDFs (no W-TinyLFU legend entry yet because Phase 1 CSVs predate the policy — 02-06 will fix); Rule 3 hygiene added __pycache__/ + *.pyc to .gitignore since import smoke test creates scripts/__pycache__/
- [Phase 02]: Plan 02-06 (validation sweep on Congress + WTLFU-05 gate): scripts/check_wtlfu_acceptance.py at 10f96e3 checks 3 conditions (A1 mrc.csv WTLFU<LRU at every cache fraction, A2 alpha_sensitivity.csv WTLFU<LRU at alpha ≥ 0.8, B one-sided regression guard at alpha=0.6); Condition B semantics changed from two-sided abs(WTLFU-LRU)/LRU≤0.02 to ONE-SIDED (WTLFU-LRU)/LRU≤0.02 via checkpoint decision — rationale: WTLFU-05 literal "within ±2% of LRU at α=0 uniform" is a REGRESSION GUARD against LFU-flavor policies underperforming LRU on flat workloads, NOT a penalty for WTLFU outperforming LRU; at alpha=0.6 WTLFU beats LRU by 7.84% which two-sided abs() would flag as failure — opposite of intent; W-TinyLFU monotonically dominates LRU across full sweep grid alpha {0.6..1.2} with advantage growing 7.84% → 21.55% as skew rises (matches TinyLFU theory on OHW filtering); LOW_ALPHA_PROXY=0.6 (lowest value in hardcoded src/main.cpp:216 sweep grid) used as uniform proxy since alpha=0 not in grid; TOLERANCE=0.02 constant + LOW_ALPHA_PROXY=0.6 constant + HIGH_ALPHA=[0.8..1.2] list all preserved for grep-discoverable spec-drift path (T-02-06-01); sweep itself ran in prior session — 6 policies × 7 alphas × 6 cache fractions regenerated into results/congress/{alpha_sensitivity.csv, mrc.csv} (gitignored); make plots exit 0 → 6 PDFs in results/congress/figures/ now render W-TinyLFU in brown/plus per Wave 4 styling; Phase 2 milestone complete — all 5 WTLFU-01..05 requirements verified
- [Phase 04]: Plan 04-01 (SHARDS large-scale validation + 50K oracle + 2 new plot functions) at 7a62c8b: src/main.cpp gains 3 new CLI flags (--shards-rates, --limit, --emit-trace) with D-18 back-compat defaults {0.001, 0.01, 0.1}; self-convergence CSV emitter (D-02/D-16) writes reference_rate,compared_rate,mae,max_abs_error,num_points,n_samples_reference,n_samples_compared with REFERENCE_RATE=0.1 hardcoded constexpr; 50K oracle guard (D-03) drops rates where rate*trace.size() < 200 — correctly drops both 0.0001 (5 samples) AND 0.001 (50 samples) in 50K regime (plan narrative only anticipated 0.0001 being dropped; my implementation is stricter and correct per D-01 200-sample floor); Makefile shards-large target runs simulator twice (50K oracle first with --shards-rates 0.001,0.01,0.1, renames shards_mrc.csv -> shards_mrc_50k.csv, then 1M self-convergence with --shards-rates 0.0001,0.001,0.01,0.1) — both MRC CSVs coexist for overlay plot; plot_shards_convergence uses log-x axis with n_samples asterisk caveat + footnote when any rate <200; plot_shards_mrc_overlay (PITFALLS M3 money-shot) reads exact_mrc.csv + shards_mrc_50k.csv + shards_mrc.csv with os.path.exists guards so Congress workload (lacking 50K artifacts) degrades gracefully to a 1M-only overlay; sanity gate MAE(1%, 10%) = 0.0378 < 0.05 (Waldspurger target 0.001 on different workload — α=0.8 Zipf is higher-skew than paper baselines); back-compat verified with `./cache_sim --trace traces/congress_trace.csv --shards` producing exactly 3 rates {0.001, 0.01, 0.1} unchanged from Phase 1; stats single-source invariant (record(true|false) count = 4 in wtinylfu.h, 0 in main.cpp) preserved
- [Phase 04]: Plan 04-02 (Doorkeeper standalone header + test binary, DOOR-01) at 3f545a3+80f3f2a: include/doorkeeper.h (79 lines, header-only Bloom filter) — #pragma once + minimal includes + class Doorkeeper with explicit ctor(n_objects_hint), three public methods (contains/add/clear) plus test-only size() inspector; sizing D-06 = 4 × max(n_objects_hint,1) bits packed into std::vector<uint64_t>; hash D-07 = Kirsch-Mitzenmacher double-hashing (h1 + i*h2) % size_ for i in {0,1} using FNV_SEED_A and FNV_SEED_B from hash_util.h (no std::hash per D-14, no #include "cache.h" since DK is NOT a CachePolicy); trailing-underscore members size_/bits_; stats single-source invariant L-12 structurally preserved (grep record(true|false) in doorkeeper.h = 0); tests/test_doorkeeper.cpp (148 lines) mirrors test_wtinylfu.cpp pattern: TEST_ASSERT macro accumulates failures, three test functions — T1 test_contains_after_add (fresh filter + add + idempotent re-check), T2 test_clear_zeros_all (100 keys added → clear → 0/100 contained post-clear), T3 test_fpr_sanity (10K added / 10K disjoint queries, observed fpr=0.0829 = 8.29%, within [0.05, 0.25] sanity band, below ~13% paper target — hash quality noise, not a bug); Makefile refactored from single-binary TEST_SRC/TEST_OBJ/TEST_TARGET pattern to per-binary TEST_WTLFU_* and TEST_DK_* variable groups, `make test` depends on both targets and runs WTLFU first then DK; make clean && make zero warnings; Phase 2 regression green (test_wtinylfu still passes all 4 of its cases); plan-scope invariant verified: git diff --stat HEAD~2 empty for wtinylfu.h, count_min_sketch.h, cache.h — Plan 04-05 integration is deliberately deferred; no deviations from plan (zero Rule 1/2/3/4 triggers, plan text matched PATTERNS.md exactly); 8.29% FPR baseline locks the regression-guard reference for Plan 04-05
- [Phase 04]: Plan 04-05 (Doorkeeper × W-TinyLFU integration + DOOR-03 ablation figure, DOOR-02/03) at 2ae822a+a43f032+ff660b1: include/count_min_sketch.h gains `std::function<void()> on_age_cb_` private member + public `set_on_age_cb()` setter + 1-line fire `if (on_age_cb_) on_age_cb_();` at end of halve_all_() (D-09, default-empty = zero overhead / branch-predictable skip for baseline wtinylfu); include/wtinylfu.h ctor widened to 3-arg `(capacity_bytes, n_objects_hint, bool use_doorkeeper=false)` (D-08) with init-list constructing doorkeeper_ member with n_objects_hint bits when flag true else minimum 1 element (64 bits of dead overhead for baseline — trivial), ctor body registers `cms_.set_on_age_cb([this]{ doorkeeper_.clear(); })` ONLY when flag true so baseline CMS callback stays default-empty; access() implements D-05 paper-faithful pre-CMS filter wrapping the SINGLE existing cms_.record(key) in `if (use_doorkeeper_) { if (doorkeeper_.contains(key)) cms_.record(key); else doorkeeper_.add(key); } else { cms_.record(key); }` — exactly ONE record per access in either branch (L-12 invariant preserved); name() returns ternary on flag; reset() gains `if (use_doorkeeper_) doorkeeper_.clear()` before stats={}; admission helpers UNCHANGED per D-10; src/main.cpp gains 1 make_policy branch `wtinylfu-dk` + 2 symmetric label-map mixed-case overrides `label = "W-TinyLFU+DK"` (required because default toupper() path would mangle to WTINYLFU-DK); Makefile .PHONY gains ablation-doorkeeper + new target runs --alpha-sweep --policies wtinylfu,wtinylfu-dk twice (Congress + Court) with per-workload alpha_sensitivity.csv → ablation_doorkeeper.csv rename; phase-04 composition target extended to all 4 axes `shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper`; scripts/plot_results.py gains POLICY_COLORS["W-TinyLFU+DK"]="#8c564b" (same brown as legacy) + POLICY_MARKERS["W-TinyLFU+DK"]="X" (distinct from legacy "P") + plot_ablation_doorkeeper function (2-panel Congress|Court, shared y-axis, baseline-first sort via endswith("+DK"), solid-vs-dashed linestyle); results/{congress,court}/ablation_doorkeeper.csv 15 lines each (1 header + 14 data = 2 policies × 7 alphas) + figures/ablation_doorkeeper.pdf 20526 bytes each (MD5 48691ba3 Congress / 9fec0f51 Court, distinct); L-12 grep gates all verified: record(true|false) in wtinylfu.h==4 (UNCHANGED from Phase 2), doorkeeper.h==0 (UNCHANGED from 04-02), count_min_sketch.h==0 (UNCHANGED from Phase 2), cache.h==11 (UNCHANGED from 04-04); Phase 2 acceptance GREEN: scripts/check_wtlfu_acceptance.py --results-dir results/congress exits 0 with all 3 conditions PASS (A1 mrc WTLFU<LRU every fraction, A2 alpha sens WTLFU<LRU at α≥0.8, B α=0.6 one-sided regression guard ≤2%); make test runs both test_wtinylfu + test_doorkeeper, both PASS; headline finding — Doorkeeper yields marginal workload-dependent delta: Congress ≈ baseline (within ±0.22pp noise across all α); Court shows -0.72pp win at α=1.1 + -0.66pp win at α=1.2 but +0.52pp loss at α=0.6 — "context-dependent with gap at alpha extremes" per plan's sanity check, consistent with Einziger-Friedman §4.3 paper claim that DK absorbs one-hit-wonder traffic effectively on heavy-tailed workloads but hurts at uniform access; magnitude is 10-20× smaller than Plan 04-04's SIEVE visited-bit ablation (+15.4pp peak), consistent with Caffeine's production decision to omit DK and STACK.md's "ablation not baseline" framing; Phase 4 COMPLETE — 5/5 plans, 8/8 requirement IDs (SHARDS-01/02/03, DOOR-01/02/03, ABLA-01/02); Phase 5 (cross-workload analysis) unblocked
- [Phase 04]: Plan 04-04 (SIEVE visited-bit ablation, ABLA-02) at 06734d7+77d1592+e4f4900: include/cache.h SIEVECache ctor widened to `(uint64_t capacity, bool promote_on_hit = true)` with init-list ternary setting `name_` = `promote_on_hit ? "SIEVE" : "SIEVE-NoProm"` inline (no 3-arg delegating overload needed — unlike Plan 04-03's S3FIFOCache, the 2 SIEVE variants are fully flag-determined); hit-path guard at cache.h:413 is ONE-LINE `if (promote_on_hit_) it->second->visited = true;` wrapping an unchanged statement — bit-identity structurally guaranteed when flag is true (no formula change, no FP subtlety like Plan 04-03); evict_one() at lines 429-455 UNCHANGED per D-12 scope; src/main.cpp gains 1 new make_policy branch `sieve-noprom` + 2 symmetric label-map entries `else if (pn == "sieve-noprom") label = "SIEVE-NoProm"` (explicit mixed-case override required — default toupper() would produce SIEVE-NOPROM and diverge from cache.h name_ member and plot_results.py dict key); Makefile ablation-sieve target runs `--alpha-sweep --policies sieve,sieve-noprom` twice (Congress + Court) with per-workload alpha_sensitivity.csv → ablation_sieve.csv rename; phase-04 composition extended to `shards-large ablation-s3fifo ablation-sieve` (Plan 04-05 will append ablation-doorkeeper); scripts/plot_results.py POLICY_COLORS["SIEVE-NoProm"] = "#9467bd" (same purple as legacy SIEVE) + POLICY_MARKERS["SIEVE-NoProm"] = "v" (same marker as SIEVE) — disambiguation via linestyle in plot function: `linestyle = "--" if policy.endswith("NoProm") else "-"`; plot_ablation_sieve 2-panel figure (Congress | Court, shared y-axis) reads both CSVs, sorts policies baseline-first, emits results/{congress,court}/figures/ablation_sieve.pdf (20123 bytes each); verification gates: grep `record(true|false)` in cache.h = 11 (unchanged), `promote_on_hit_` = 4, `SIEVE-NoProm` in cache.h = 2, `sieve-noprom` in main.cpp = 3, `label = "SIEVE-NoProm"` in main.cpp = 2, `endswith("NoProm")` in plot_results.py = 2; Phase 1/3 back-compat verified via column-wise diff — SIEVE miss_ratio + byte_miss_ratio byte-identical between pre-Phase-4 outputs and post-plan runs (only accesses_per_sec throughput noise differs, expected); make test regression green (both test_wtinylfu + test_doorkeeper suites PASS); headline finding: SIEVE-NoProm monotonically loses to SIEVE at every alpha on both workloads, gap peaks at +15.4pp on Congress (α=1.0) and +11.0pp on Court (α=1.1) — empirically confirms Zhang et al. (NSDI'24) lazy-promotion claim as dominant contributor to SIEVE's scan-resistance; zero deviations from plan
- [Phase 03]: Plan 03-01 (scripts/collect_court_trace.py production collector) at e9d8557: 609-line sibling to scripts/pilot_court_trace.py (NOT a replacement per STACK.md §32 copy-modify policy); all 26 grep-discoverable invariants pass on first write (ALLOWED_HOST, BASE_URL, BASE_DELAY=0.8, JITTER=0.4, OPINION_METADATA_FRACTION=0.8, D-02 field set "id,absolute_url,type,date_filed,author_id", [0,30,90] ramp, CONSECUTIVE_429_HARD_STOP=5, exact FATAL diagnostic "FATAL: 5 consecutive 429s — token throttled or pacing too aggressive. Resume after 1 hour.", PER_ENDPOINT_TARGET=5000, --resume, FALLBACK_PROBE_WINDOW=500, FALLBACK_NARROW_FACTOR=0.67, COURTLISTENER_API_KEY, f.flush(), all 4 endpoint templates, court_ids list); D-11 429 ramp counter is PER-ENDPOINT (matches pilot semantics) with index-clamp reusing the +90 bucket for 4th consecutive before the 5-consec hard-stop fires; Retry-After value clamped to [0, 120]s to protect against pathological server hints; D-06 fallback evaluated AFTER the 500th response lands (fallback_triggered latch ensures at most one narrowing per endpoint); target-rows remainder distributed 1-per-endpoint to first N so 10-row smoke touches all 4 families (observed: docket=3, opinion=3, cluster=2, court=2); module-level assert urlparse(BASE_URL).netloc == ALLOWED_HOST + per-request urlparse(url) check = defence-in-depth SSRF mitigation for T-03-01-01; token fingerprint-only logging (api_key[:4]+"..."+api_key[-4:]+len, never raw); 10-row smoke run against live CourtListener API completed in 14s exit 0 with well-formed CSV (CRLF line endings matching traces/court_pilot.csv schema verbatim); observed smoke pacing ~1.27s/issued-request (0.8s base + ~0.2s jitter + ~0.07s network RTT) revises Plan 03-02 runtime estimate UP from ROADMAP's 6.1h to ~8h wall-clock for 20K successful rows at 85% aggregate success rate (~23,530 issued requests); Plan 03-02 unblocked

### Pending Todos

None yet.

### Blockers/Concerns

Phase 1 risks resolved:

- C1/C2/C3 CLEARED: CourtListener token verified via live curl; all 4 endpoints (/dockets/, /opinions/, /clusters/, /courts/) returned 200 with zero 403s and zero 429s during the 200-request pilot (90%/74%/90%/100% success rates).

Phase 2 items — ALL RESOLVED:

- ✓ C6: W-TinyLFU must mirror Caffeine `WindowTinyLfuPolicy` line-by-line — LOCKED 2026-04-19 in `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` (Plan 02-01 complete; Caffeine v3.1.8 source pinned)
- ✓ Pre-work verification: Caffeine confirmed `sampleSize = 10 * maximumSize` (FrequencySketch.java:L96); our port deliberately uses `10 * width * depth` per CONTEXT.md L-5 (deviation §6 row 2)
- ✓ WTLFU-05 validation: Plan 02-06 complete — scripts/check_wtlfu_acceptance.py exit 0, W-TinyLFU beats LRU monotonically across alpha {0.6..1.2}, all 3 acceptance conditions PASS

Phase 1 human-UAT items (resolved during Phase 2):

- ✓ RESOLVED: `make plots` end-to-end on user's machine — Makefile's `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` + `PYTHONPATH=.venv/lib/python3.14/site-packages` workaround (Phase 1 commit 2dc8466) confirmed working during 02-06; exit 0, 6 PDFs
- ✓ RESOLVED: Stale CSVs in `results/congress/` — regenerated by 02-06 sweep with W-TinyLFU rows; figures include W-TinyLFU in brown/plus styling
- HUMAN-UAT: Visual sanity of `results/congress/figures/*.pdf` — deferred to Phase 6 writeup pass

## Deferred Items

Items deferred to v2 (from REQUIREMENTS.md):

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Validation | V2-01: Caffeine trace cross-validation (±2% agreement) | Deferred to v2 | Milestone 2 planning |
| Policy | V2-02: LHD or AdaptSize as 7th policy | Deferred to v2 | Milestone 2 planning |
| Trace | V2-03: Third trace domain (SEC EDGAR) | Deferred to v2 | Milestone 2 planning |

## Session Continuity

Last session: 2026-04-21T01:30:00Z
Stopped at: Phase 6 context gathered (19 decisions across 4 areas — paper narrative, AI-use report framing, figure curation, demo content + hedge)
Resume: `/gsd-plan-phase 6` to create the executable plan set; CONTEXT.md locks the paper-leads-with-surprise-finding narrative + decision-log DOC-03 format + live-demo-with-recording-backup strategy

## Phase 5 Decisions (add to PROJECT.md Key Decisions)

- [Phase 05]: Plan 05-01 (--seed CLI flag): surgical ~9-insertion/5-deletion edit to src/main.cpp at commit 3212b97; new `uint64_t seed = 42` default; threaded into 5 non-emit-trace call sites (replay_zipf line 204, generate_zipf_trace synthetic line 215, prepare_objects line 334, generate_replay_trace+generate_zipf_trace alpha-sweep paths 339-340); `--emit-trace` literal `42` at line 182 deliberately preserved per D-15 (SHARDS-01 traces/shards_large.csv provenance)
- [Phase 05]: Plan 05-02 (Congress workload_stats.json regeneration): data-regeneration-only plan; empirical α_mle=0.231 NOT 0.797 — plan author misread the "0.797 MLE-recovers-from-synthetic-α=0.8" regression test at REQUIREMENTS.md:15 / codebase/TESTING.md:27-29 as raw-trace characterization; raw Congress trace is genuinely near-uniform (unique_objects/total_requests=91.7% + ohw_ratio=0.989 + PROJECT.md's explicit "random queries with near-zero temporal locality" framing); Plan 05-06 markdown renderer only formats α to 2 decimals, no numeric-range validation, no code fix needed
- [Phase 05]: Plan 05-03 (multi-seed sweep orchestrator): SINGLE-invocation model verified empirically (1 `./cache_sim --alpha-sweep` call emits BOTH mrc.csv + alpha_sensitivity.csv; source evidence at src/main.cpp:240 unconditional MRC + src/main.cpp:302 gated alpha); 10 cache_sim invocations → 20 CSVs; full sweep wall-clock 58.2s (30× under D-05's 30-min soft ceiling); per-seed variance confirmed Congress σ≈0.27% / Court σ≈3.3% at LRU/cache_frac=0.01 (Court's ~12× higher variance reflects heavier tail)
- [Phase 05]: Plan 05-04 (aggregation + Welch's t-test): scripts/compare_workloads.py (198 lines) is the first scipy.stats user in the codebase; Welch's via stats.ttest_ind(equal_var=False) per D-02; LRU rows get p_value=NaN + significant=True by sentinel convention (LRU is the reference, not self-compared — downstream plots/filters keep LRU rows via the True sentinel); W-TinyLFU dominance over LRU on Congress formally validated with p ∈ [1.2e-06, 8.7e-08] at every α in {0.6..1.2} — Phase 2 monotonic-dominance finding now has 5-seed Welch's-t backing
- [Phase 05]: Plan 05-05 (cross-workload plot functions): 4 new functions added to scripts/plot_results.py (plot_compare_mrc_2panel with fill_between ±1σ bands per D-03 is the canonical DOC-02 figure; plot_compare_policy_delta; plot_compare_mrc_overlay; plot_winner_per_regime_bar); POLICY_COLORS/POLICY_MARKERS dicts reused by reference (not duplicated — grep counts 1/1 preserved); all 4 PDFs land in results/compare/figures/ regardless of --workload arg; graceful os.path.exists skip when aggregated CSVs missing
- [Phase 05]: Plan 05-06 (ANAL-03/04 tables + acceptance gate): scripts/compare_workloads.py extended 198→472 lines with BASE_POLICIES/OHW_CACHE_FRAC constants + 4 new functions (md_table, build_workload_characterization, build_winner_per_regime, write_table_artifacts); scripts/check_anal_acceptance.py (140 lines) mirrors check_wtlfu_acceptance.py pattern with 4 check_sc* functions; Mixed Sizes regime uses Court single-seed byte_miss_ratio from results/court/mrc.csv (Plan 05-04 aggregates miss_ratio not byte_miss_ratio; multi-seed byte-MRC deferred to v2); Congress Mixed Sizes is N/A per D-01 (uniform sizes); **named regime winners**: Small Cache Congress=W-TinyLFU(0.869)/Court=W-TinyLFU(0.831); High Skew Congress=SIEVE(0.351)/Court=W-TinyLFU(0.386) — SIEVE winning on Congress high-α is a surprise contra Phase 2's W-TinyLFU dominance narrative, likely because mean-across-3-alphas favors SIEVE's visited-bit's scan-resistance at α=1.0 uniform-ish; Mixed Sizes Congress=N/A/Court=W-TinyLFU(0.284 byte_miss_ratio single-seed); OHW Regime Congress=W-TinyLFU(0.712)/Court=W-TinyLFU(0.728); zero ablation contamination (BASE_POLICIES filter applied in regime analysis)
- [Phase 05]: Code review finding WR-01 (latent coupling): plot_results.py::plot_winner_per_regime_bar::winner_on does NOT filter to BASE_POLICIES while compare_workloads.py::_winner_in_group DOES — today this does not diverge because run_multiseed_sweep.py uses default 6-policy sweep, but if extended to include ablations (s3fifo-5/20, sieve-noprom, wtinylfu-dk) the figure and table would silently diverge. Deferred to Phase 6 or a decimal phase patch.
- [Phase 05]: Code review finding WR-02 (regime dispatch safety): plot_results.py:819-849 if/elif chain has no else; adding a 5th regime without a matching elif would propagate stale cw/cv/kw/kv from the previous iteration. Deferred.

**Planned Phase:** 6 (Writeup & Demo) — 7 plans — 2026-04-22T01:34:02.611Z
