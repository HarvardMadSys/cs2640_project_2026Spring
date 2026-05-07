---
phase: 05-cross-workload-analysis-infrastructure
plan: 01
subsystem: infra
tags:
  - cli-flag
  - seed
  - reproducibility
  - back-compat
  - zipf
  - replay-zipf

# Dependency graph
requires:
  - phase: 02-w-tinylfu-core
    provides: "WTinyLFUCache ctor(capacity, n_objects_hint) consumed via make_policy; stats single-source invariant L-12 (record(true|false) == 0 in main.cpp)"
  - phase: 04-shards-large-scale-validation-ablations
    provides: "--emit-trace literal seed=42 at src/main.cpp:182 (traces/shards_large.csv provenance anchor per D-06); make_policy branches for s3fifo-5/10/20, sieve-noprom, wtinylfu-dk"
provides:
  - "`cache_sim --seed N` CLI flag that threads into all five non-emit-trace Zipf/replay call sites"
  - "Byte-identical back-compat on semantic CSV columns when --seed is absent (default 42)"
  - "Unblocks Plan 05-03 multi-seed sweep orchestrator (scripts/run_multiseed_sweep.py) — same (workload, alpha, policy) cell with different seeds now produces different miss_ratio values"
affects:
  - 05-03-multi-seed-sweep-orchestrator
  - 05-04-aggregation-statistics
  - 05-05-figures-tables
  - 05-06-final-paper-artifacts

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CLI flag threading (D-06): single new uint64_t default-42 variable + argparse branch (copy-from-sibling pattern) + N explicit call-site additions; preserve select literals for provenance anchors"

key-files:
  created: []
  modified:
    - src/main.cpp

key-decisions:
  - "SHARDS NOT threaded (content-addressed FNV-1a is inherently deterministic — no RNG to seed); D-06's original 'SHARDS sampler' scope correctly dropped per pattern-mapper"
  - "--emit-trace literal `42` at src/main.cpp:182 preserved verbatim (T-05-01-04 mitigation; traces/shards_large.csv provenance)"
  - "uint64_t default 42 matches existing header defaults in include/trace_gen.h (generate_zipf_trace, replay_zipf, prepare_objects, generate_replay_trace all already default seed=42)"
  - "Semantic byte-compat interpretation: accesses_per_sec is wall-clock timing noise pre-existing in all Phase 1-4 CSVs (two consecutive default runs of the unmodified binary differ there too); the plan's 'byte-identical' applies to the 5 semantic columns (cache_frac, cache_size_bytes, policy, miss_ratio, byte_miss_ratio), which DO match byte-for-byte between default and --seed 42"

patterns-established:
  - "Reproducibility-seed CLI flag: uint64_t default + argparse + explicit call-site threading + literal preservation for provenance — future reproducibility flags (temperature, noise-scale, etc.) follow the same 6-edit pattern"

requirements-completed:
  - ANAL-02

# Metrics
duration: 3m 35s
completed: 2026-04-21
---

# Phase 05 Plan 01: --seed CLI Flag Summary

**New `--seed N` CLI flag on cache_sim that threads into 5 Zipf/replay call sites (synthetic path, replay_zipf path, and all 3 alpha-sweep call sites) while preserving the --emit-trace literal `42` for traces/shards_large.csv provenance.**

## Performance

- **Duration:** 3m 35s
- **Started:** 2026-04-21T03:09:55Z
- **Completed:** 2026-04-21T03:13:30Z
- **Tasks:** 1 (TDD cycle: RED/GREEN integrated since this is a CLI argparse change, not a pure-function unit; RED = verify unmodified binary rejects --seed, GREEN = apply 6 edits + re-verify, no REFACTOR needed)
- **Files modified:** 1

## Accomplishments

- New `uint64_t seed = 42;` variable declared at src/main.cpp:116, alongside num_requests / num_objects, matching sibling pattern
- New argparse branch at src/main.cpp:140-141 parses `--seed <n>` using the canonical `std::stoull` idiom (copied verbatim from --num-requests branch at line 136)
- New `--help` line at src/main.cpp:28 documents the flag with correct 23-char alignment
- Five Zipf/replay call sites now thread the seed variable:
  - src/main.cpp:204 — `replay_zipf(raw_trace, num_requests, alpha, seed)` (replay_zipf load path)
  - src/main.cpp:215 — `generate_zipf_trace(num_requests, num_objects, alpha, seed)` (synthetic-trace path)
  - src/main.cpp:334 — `prepare_objects(raw_trace, seed)` (alpha-sweep dedupe+shuffle)
  - src/main.cpp:339 — `generate_replay_trace(prepared_objects, num_requests, a, seed)` (alpha-sweep replay path)
  - src/main.cpp:340 — `generate_zipf_trace(num_requests, num_objects, a, seed)` (alpha-sweep synthetic fallback)
- The `--emit-trace` literal `generate_zipf_trace(num_requests, num_objects, alpha, 42)` at src/main.cpp:182 is UNCHANGED (T-05-01-04 mitigation)
- Build clean: zero `-Wunused-variable` warnings (the absence of Wunused proves seed is actually threaded — if it were declared-but-unused, -Wunused would fire)
- Regression green: both `test_wtinylfu` (4 cases) and `test_doorkeeper` (3 cases) still PASS

## Task Commits

Each task was committed atomically:

1. **Task 1: Add --seed N CLI flag + thread into Zipf/replay call sites** — `3212b97` (feat)

## Files Created/Modified

- `src/main.cpp` — Added 1 variable declaration, 1 argparse branch, 1 help line, and 5 explicit call-site threadings (9 insertions, 5 deletions per git diff --stat)

## Grep-Discoverable Invariants Confirmed

All 12 invariants from the plan's acceptance criteria passed:

| Check | Grep pattern | Expected | Actual |
|---|---|---|---|
| A. Help text | `./cache_sim --help \| grep -c -- "--seed"` | ≥ 1 | 1 |
| B. Variable decl | `grep -c "^ *uint64_t seed = 42;" src/main.cpp` | ≥ 1 | 1 |
| C. Parser branch | `grep -c 'std::strcmp(argv\[i\], "--seed")' src/main.cpp` | 1 | 1 |
| D. --emit-trace literal 42 preserved | `grep -c "generate_zipf_trace(num_requests, num_objects, alpha, 42)" src/main.cpp` | 1 | 1 |
| E. replay_zipf threaded | `grep -c "replay_zipf(raw_trace, num_requests, alpha, seed)" src/main.cpp` | 1 | 1 |
| F. synthetic generate_zipf_trace threaded | `grep -c "generate_zipf_trace(num_requests, num_objects, alpha, seed)" src/main.cpp` | 1 | 1 |
| G. prepare_objects threaded | `grep -c "prepare_objects(raw_trace, seed)" src/main.cpp` | 1 | 1 |
| H. generate_replay_trace threaded | `grep -c "generate_replay_trace(prepared_objects, num_requests, a, seed)" src/main.cpp` | 1 | 1 |
| I. alpha-sweep generate_zipf_trace threaded | `grep -c "generate_zipf_trace(num_requests, num_objects, a, seed)" src/main.cpp` | 1 | 1 |
| J. L-12 stats single-source invariant | `grep -cE "record\(\s*(true\|false)" src/main.cpp` | 0 | 0 |
| K. mrc.csv header unchanged | `grep -c "cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec" src/main.cpp` | 1 | 1 |
| L. alpha_sensitivity.csv header unchanged | `grep -c "alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec" src/main.cpp` | 1 | 1 |

## Seed Variation Smoke-Test Evidence

All three execution paths confirmed seed-threading produces different output CSVs:

**Synthetic path (`--num-objects 500 --num-requests 5000 --alpha 0.8 --cache-sizes 0.01 --policies lru`):**
- `--seed 42` → `cache_frac=0.01, cache_size_bytes=67182, policy=LRU, miss_ratio=0.9264, byte_miss_ratio=0.974242`
- `--seed 7`  → `cache_frac=0.01, cache_size_bytes=53408, policy=LRU, miss_ratio=0.9212, byte_miss_ratio=0.951948`
- All 3 semantic columns (cache_size_bytes, miss_ratio, byte_miss_ratio) differ → seed threads into generate_zipf_trace.

**Replay path (`--trace traces/court_trace.csv --replay-zipf --num-requests 5000 --cache-sizes 0.01 --policies lru`):**
- `--seed 42` → `cache_size_bytes=121122, miss_ratio=0.9574, byte_miss_ratio=0.965794`
- `--seed 7`  → `cache_size_bytes=109881, miss_ratio=0.9340, byte_miss_ratio=0.970856`
- All 3 semantic columns differ → seed threads into replay_zipf.

**Alpha-sweep path (`--trace traces/court_trace.csv --replay-zipf --alpha-sweep --num-requests 5000 --cache-sizes 0.01 --policies lru`):**
- All 7 alpha rows (0.6..1.2) differ in miss_ratio and byte_miss_ratio between seed=42 and seed=7:

| alpha | seed=42 miss_ratio | seed=7 miss_ratio | Δ miss_ratio |
|---|---|---|---|
| 0.6 | 0.9864 | 0.9888 | +0.0024 |
| 0.7 | 0.9760 | 0.9706 | −0.0054 |
| 0.8 | 0.9574 | 0.9340 | −0.0234 |
| 0.9 | 0.9200 | 0.8834 | −0.0366 |
| 1.0 | 0.8936 | 0.8084 | −0.0852 |
| 1.1 | 0.8354 | 0.7248 | −0.1106 |
| 1.2 | 0.7812 | 0.6224 | −0.1588 |

The gap widens monotonically with alpha, consistent with tighter-skew distributions amplifying per-seed permutation noise — expected behavior for the ZipfGenerator stream. Seed threading through both `prepare_objects(raw_trace, seed)` AND `generate_replay_trace(..., seed)` confirmed.

## Default Back-Compat Evidence

**Semantic-column byte-identity (synthetic path, default vs --seed 42):**

```
cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio
default run: 0.01,67182,LRU,0.9264,0.974242
--seed 42:   0.01,67182,LRU,0.9264,0.974242
```

A strict `diff` of the full CSV shows only `accesses_per_sec` differs (1.26e+07 vs 1.11e+07), but a fresh second run of the same default invocation shows the SAME `accesses_per_sec` variation (1.22e+07 on run 2) — confirming that `accesses_per_sec` is pre-existing wall-clock timing noise unrelated to this plan. The 5 semantic columns are byte-identical between default and --seed 42, satisfying the plan's back-compat contract.

## --emit-trace Provenance Preservation Evidence

Ran `--emit-trace` twice with and without `--seed 999`:
- `./cache_sim --emit-trace /tmp/a.csv --num-requests 1000 --num-objects 100 --alpha 0.8` → MD5 `a6b6a38a9de7d8e4e23516813bca4317`
- `./cache_sim --emit-trace /tmp/b.csv --num-requests 1000 --num-objects 100 --alpha 0.8 --seed 999` → MD5 `a6b6a38a9de7d8e4e23516813bca4317`

Byte-identical. The hardcoded literal `42` at src/main.cpp:182 is untouched by the `--seed` flag, preventing T-05-01-04 regression of traces/shards_large.csv provenance.

## Regression Evidence

`make test` runs both test binaries post-edit; all 7 cases PASS:

```
=== W-TinyLFU + CountMinSketch test suite ===
PASS: cms_basics
PASS: cms_aging
PASS: hot_survives_scan
PASS: determinism
All tests PASSED.

=== Doorkeeper test suite ===
PASS: contains_after_add
PASS: clear_zeros_all (post-clear contained=0/100)
PASS: fpr_sanity (fp=829/10000, fpr=0.0829, target ~0.13)
All tests PASSED.
```

Confirms the seed threading did not touch WTLFU, CMS, or Doorkeeper code paths — surgical change scoped to main.cpp only, as the plan required.

## Decisions Made

- **Seed variable comment placement:** kept on same physical line as `uint64_t seed = 42;` declaration (matching sibling style of `trace_limit = 0; // D-03: 0 means "no limit"` at the same block). Enhances grep-discoverability of the D-06 back-reference.
- **Argparse branch placement:** inserted AFTER `--num-objects` (line 138-139) and BEFORE `--alpha` (line 142-143), grouping with other uint64_t-valued flags per plan's `<action>` (b).
- **Help-text placement:** inserted AFTER `--num-objects` help (line 27) and BEFORE `--alpha` help (line 29), matching argparse order for discoverability.
- **Comment update on prepare_objects:** changed inline comment from `// default seed=42` to `// Plan 05-01: thread --seed (default 42)` per plan's action (f) so future readers don't think seed=42 is still hardcoded.

## Deviations from Plan

None — plan executed exactly as written. All six surgical edits applied verbatim from `<action>` (a)-(f), all five call-site line numbers from the plan's `<interfaces>` matched the actual post-read file contents (the PATTERNS.md sketch line numbers were accurate), no Rule 1/2/3/4 triggers, no auth gates, no architectural questions.

**Total deviations:** 0
**Impact on plan:** Zero scope change. The ~6-line diff target was met exactly (9 insertions, 5 deletions, 1 file).

## Issues Encountered

**None** during the plan execution. Minor setup observation:

- The worktree initial HEAD (44f13ea, Phase 2 completion) was behind the expected base (34d82e4, Phase 5 planning completion). Corrected via `git merge --ff-only 34d82e4` (fast-forward, non-destructive — `git reset --hard` was denied by sandbox but not needed since HEAD was strictly an ancestor of the expected base). No user action required; the plan executed cleanly against the corrected HEAD.

## TDD Gate Compliance

Plan frontmatter has `tdd="true"` on Task 1. This is a CLI argparse integration test, not a pure-function unit test — so the RED/GREEN gate expresses at the `make && ./cache_sim --help | grep --seed` integration level rather than as a unit test file:

- **RED:** before the edits, `./cache_sim --seed 42 ...` printed `Unknown option: --seed` and exited non-zero; `./cache_sim --help | grep -c -- "--seed"` returned 0. Confirmed before any edits were applied.
- **GREEN:** after the 6 edits, the same commands exit 0 and the grep returns 1. The seed-variation semantic tests across synthetic/replay/alpha-sweep paths all show the expected differences between `--seed 42` and `--seed 7`. All 12 grep-invariants PASS.
- **REFACTOR:** not needed — the 6 surgical edits were already minimal (single variable, single argparse branch, single help line, 5 explicit call-site threadings). No dead code introduced.

No separate unit-test commit was produced because the RED baseline is captured by the pre-existing `else { std::cerr << "Unknown option: " << argv[i] << "\n"; ... }` path in main.cpp — i.e., the CLI's built-in unknown-option handler served as the failing-test assertion for RED. A single `feat(05-01): ...` commit captures the GREEN transition.

## Threat Flags

None — the six surgical edits introduce no new network, auth, file-access, or trust-boundary surface. `--seed <n>` is a local-process CLI argument, parsed via the same `std::stoull` idiom as existing flags (`--num-requests`, `--limit`, etc.), with identical threat profile. All four threats (T-05-01-01..04) in the plan's `<threat_model>` remain LOW and mitigated as designed.

## Known Stubs

None — the plan introduces no hardcoded empty values, placeholder text, or unwired components. Every edit produces functioning runtime behavior.

## Next Phase Readiness

**Plan 05-03 (multi-seed sweep orchestrator) unblocked.** The orchestrator script `scripts/run_multiseed_sweep.py` can now invoke:

```bash
./cache_sim --seed 42 --trace traces/congress_trace.csv --replay-zipf --alpha-sweep ...
./cache_sim --seed 7  --trace traces/congress_trace.csv --replay-zipf --alpha-sweep ...
./cache_sim --seed 13 --trace traces/congress_trace.csv --replay-zipf --alpha-sweep ...
./cache_sim --seed 23 --trace traces/congress_trace.csv --replay-zipf --alpha-sweep ...
./cache_sim --seed 31 --trace traces/congress_trace.csv --replay-zipf --alpha-sweep ...
```

Each invocation will produce a distinct `alpha_sensitivity.csv` with per-seed miss_ratio variability (empirically shown above on the court trace at alpha=0.8: miss_ratio 0.9574 vs 0.9340 for seeds 42 vs 7). This supplies the "±1σ across 5 seeds" confidence-interval data that ANAL-02 claims rest on.

Downstream plans 05-04 (aggregation + statistics) and 05-05 (figures) consume the multi-seed CSVs from 05-03 — this plan's output (a single CLI flag) is the atomic primitive they all depend on.

## Self-Check: PASSED

- FOUND: src/main.cpp (edited)
- FOUND: .planning/phases/05-cross-workload-analysis-infrastructure/05-01-SUMMARY.md (this file)
- FOUND: commit `3212b97` (task 1 feat)
- Verified: `uint64_t seed = 42;` declaration count = 1
- Verified: `generate_zipf_trace(num_requests, num_objects, alpha, 42)` literal count = 1 (--emit-trace provenance preserved)
- Verified: `record\(\s*(true|false)` count in src/main.cpp = 0 (L-12 stats single-source invariant intact)
- Verified: `cache_sim` binary rebuilds cleanly with zero -Wunused warnings

---
*Phase: 05-cross-workload-analysis-infrastructure*
*Plan: 01*
*Completed: 2026-04-21*
