---
phase: 02-w-tinylfu-core
plan: 06
subsystem: validation
tags: [wtinylfu, congress, acceptance, sweep, pandas, wtlfu-05]

# Dependency graph
requires:
  - phase: 02-w-tinylfu-core
    provides: W-TinyLFU policy + CMS + make_policy wiring + plot_results W-TinyLFU entry
  - phase: 01-enabling-refactors-courtlistener-pilot
    provides: Congress trace, results/congress/ layout, throughput column
provides:
  - "scripts/check_wtlfu_acceptance.py — standalone WTLFU-05 acceptance checker (one-sided Condition B)"
  - "results/congress/alpha_sensitivity.csv regenerated with W-TinyLFU rows at alpha in {0.6..1.2}"
  - "results/congress/mrc.csv regenerated with W-TinyLFU rows at 6 cache fractions"
  - "results/congress/figures/*.pdf regenerated (6 PDFs) with W-TinyLFU rendered in brown/plus (POLICY_COLORS/POLICY_MARKERS entries from 02-05)"
  - "Machine-verifiable WTLFU-05 gate: `python3 scripts/check_wtlfu_acceptance.py --results-dir results/congress` exits 0"
affects: [Phase 3 CourtListener sweep, Phase 4 Doorkeeper ablation, Phase 5 cross-workload analysis, Phase 6 writeup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Standalone Python acceptance-gate scripts at scripts/check_*.py (pandas-based, exit-code contract)"
    - "One-sided tolerance checks for regression guards (signed delta, not absolute value)"

key-files:
  created:
    - "scripts/check_wtlfu_acceptance.py (147 lines — WTLFU-05 gate)"
    - ".planning/phases/02-w-tinylfu-core/02-06-SUMMARY.md"
  modified:
    - "results/congress/alpha_sensitivity.csv (regenerated — adds W-TinyLFU rows; gitignored, not committed)"
    - "results/congress/mrc.csv (regenerated — adds W-TinyLFU rows; gitignored, not committed)"
    - "results/congress/figures/*.pdf (6 figures regenerated with W-TinyLFU styling; gitignored)"

key-decisions:
  - "One-sided Condition B: check flags only WTLFU REGRESSION vs LRU at alpha=0.6, not outperformance. Requirement intent (regression guard on uniform-like workloads) preserved; W-TinyLFU beating LRU at low alpha is not a failure."
  - "LOW_ALPHA_PROXY = 0.6 (lowest value in src/main.cpp:216 hardcoded sweep). WTLFU-05 literal says alpha=0 uniform but sweep grid doesn't include alpha=0."
  - "Checker self-identifies the requirement (WTLFU-05 string in docstring + HIGH_ALPHA + TOLERANCE constants matching ROADMAP literals) so a grep-discoverable edit path exists if the sweep grid changes (T-02-06-01)."

patterns-established:
  - "Acceptance-gate script pattern: scripts/check_<req>_acceptance.py reads results CSVs, prints per-condition verdicts, exits 0/1 on pass/fail"
  - "Regression-guard tolerance pattern: use signed delta `(actual - baseline) / baseline`, not `abs(...)`, when the requirement is 'don't be worse by more than X%'"

requirements-completed: [WTLFU-05]

# Metrics
duration: 2m (checkpoint-resume; sweep itself was run in prior session)
completed: 2026-04-19
---

# Phase 2 Plan 06: Congress Validation Sweep + WTLFU-05 Gate Summary

**W-TinyLFU validated on Congress trace: beats LRU at every alpha >= 0.8 across all 6 cache fractions, and does not regress vs LRU at alpha=0.6 — in fact wins by 7.84% there too. WTLFU-05 acceptance gate passes via `scripts/check_wtlfu_acceptance.py` (exit 0).**

## Performance

- **Duration:** ~2 minutes (checkpoint-resume phase only; full sweep + prior checker write happened in the paused session)
- **Started:** 2026-04-19T05:01:05Z (resume)
- **Completed:** 2026-04-19T05:02:23Z
- **Tasks:** 2 planned (sweep + checker) — both fulfilled; checkpoint decision applied to Condition B
- **Files created:** 1 (scripts/check_wtlfu_acceptance.py)
- **Files regenerated (gitignored):** 2 CSVs + 6 PDFs under results/congress/

## Accomplishments

- W-TinyLFU integrated into the 6-policy sweep on the real Congress trace (replay-Zipf overlay) — no more stale Phase 1 CSVs
- Acceptance checker `scripts/check_wtlfu_acceptance.py` exits 0 on the produced CSVs; all 3 conditions (A1, A2, B) PASS
- Figures regenerated via `make plots` — 6 PDFs under `results/congress/figures/` now include W-TinyLFU in brown/filled-plus per Wave 4's styling entries
- WTLFU-05 is the final requirement in Phase 2 — this plan closes the phase's required-claims list

## Task Commits

1. **Task 2: WTLFU-05 acceptance checker** — `10f96e3` (feat)

**Task 1 (sweep) artifacts:** CSV outputs regenerated in the prior session; `results/` is gitignored so no commit, but the files are on disk at `results/congress/alpha_sensitivity.csv` and `results/congress/mrc.csv` with fresh mtimes.

**Plan metadata:** _pending final `docs(02-06)` commit (next step)_

## Alpha Sensitivity Results (7 α × 6 policies, miss_ratio)

| α   | LRU     | FIFO    | CLOCK   | S3-FIFO | SIEVE   | W-TinyLFU | WTLFU vs LRU |
|-----|---------|---------|---------|---------|---------|-----------|--------------|
| 0.6 | 0.9552  | 0.9592  | 0.9499  | 0.9136  | 0.8825  | 0.8803    | WTLFU better by 7.84% |
| 0.7 | 0.9102  | 0.9216  | 0.8983  | 0.8513  | 0.8163  | 0.8124    | WTLFU better by 10.75% |
| 0.8 | 0.8325  | 0.8554  | 0.8150  | 0.7616  | 0.7192  | 0.7223    | WTLFU better by 13.24% |
| 0.9 | 0.7203  | 0.7555  | 0.6971  | 0.6447  | 0.6058  | 0.6053    | WTLFU better by 15.97% |
| 1.0 | 0.5837  | 0.6281  | 0.5588  | 0.5122  | 0.4738  | 0.4757    | WTLFU better by 18.51% |
| 1.1 | 0.4450  | 0.4939  | 0.4231  | 0.3843  | 0.3528  | 0.3572    | WTLFU better by 19.72% |
| 1.2 | 0.3287  | 0.3758  | 0.3082  | 0.2808  | 0.2554  | 0.2578    | WTLFU better by 21.55% |

Source: `results/congress/alpha_sensitivity.csv` (42 rows, 6 policies × 7 alphas).

**W-TinyLFU monotonically dominates LRU across every α in the sweep**, with advantage growing as skew increases — matches TinyLFU theory (admission test filters long-tail one-hit-wonders that LRU admits and immediately evicts).

## MRC Results (6 cache fractions × 6 policies, miss_ratio at Congress workload alpha≈0.797)

| cache_frac | cache_bytes | LRU     | W-TinyLFU | WTLFU vs LRU |
|-----------:|------------:|---------|-----------|--------------|
| 0.001      | 14,484      | 0.9655  | 0.8891    | WTLFU better by 7.91% |
| 0.005      | 72,420      | 0.8857  | 0.7854    | WTLFU better by 11.33% |
| 0.01       | 144,841     | 0.8325  | 0.7223    | WTLFU better by 13.24% |
| 0.02       | 289,683     | 0.7678  | 0.6528    | WTLFU better by 14.98% |
| 0.05       | 724,207     | 0.6593  | 0.5444    | WTLFU better by 17.42% |
| 0.1        | 1,448,415   | 0.5548  | 0.4543    | WTLFU better by 18.12% |

Source: `results/congress/mrc.csv` (36 rows, 6 policies × 6 cache fractions).

**A1 PASS** — W-TinyLFU strictly less than LRU at every cache fraction.

## Acceptance Check Output (verbatim)

```
=== WTLFU-05 Acceptance Check ===
A1 (mrc.csv: WTLFU < LRU at every cache fraction): PASS
A2 (alpha_sensitivity.csv: WTLFU < LRU at alpha in [0.8, 0.9, 1.0, 1.1, 1.2]): PASS
B (alpha=0.6: WTLFU regression vs LRU <= 2% — one-sided): PASS

PASS: all WTLFU-05 conditions satisfied.
```

Exit code: **0**. All 3 conditions pass.

## Decisions Made

### D-02-06-01: One-sided Condition B (checkpoint decision)

**Original plan:** Condition B used `|WTLFU - LRU| / LRU <= 0.02` (two-sided absolute tolerance).

**Problem discovered during first run:** At alpha=0.6, W-TinyLFU's miss_ratio (0.8803) is 7.84% BELOW LRU's (0.9552) — i.e., W-TinyLFU is substantially BETTER than LRU. The two-sided check flagged this as a Condition B failure because `abs(0.0784) > 0.02`, penalizing W-TinyLFU for outperforming LRU.

**User decision (checkpoint):** Edit Condition B to one-sided. The check now flags only when W-TinyLFU regresses vs LRU by more than 2%, not when it beats LRU.

**Rationale:**

1. **Requirement intent:** WTLFU-05's "within ±2% of LRU at α=0 uniform" clause (from ROADMAP.md §Phase 2 success criterion 4) is a regression guard against the theoretical concern that LFU-flavor policies can underperform LRU on flat workloads. The concern is NOT that W-TinyLFU might win by too large a margin.

2. **Workload regime:** alpha=0.6 is not actually uniform. Phase 1's MLE estimate for the natural Congress trace is α≈0.797. The sweep grid (src/main.cpp:216) hardcodes `{0.6..1.2}` and doesn't include α=0. LOW_ALPHA_PROXY=0.6 is the best available proxy for the low-skew regime, but it still has meaningful skew — WTLFU's admission filter helps there too.

3. **Monotonicity:** The sweep shows WTLFU's advantage over LRU grows monotonically with α (from +7.84% at α=0.6 to +21.55% at α=1.2). The advantage is smallest at low α, as theory predicts — but it's still positive. This is exactly the expected shape.

4. **Theoretical soundness:** TinyLFU's admission test filters long-tail one-hit-wonders. The Congress trace's OHW ratio is substantial (Phase 1 data), so even at lower α the admission filter still adds value by rejecting cache-polluting one-hit-wonders. WTLFU beating LRU here is a feature, not a bug.

**Alternative considered:** Regenerate the sweep including α=0 explicitly. Rejected because `--alpha-sweep` is hardcoded in C++ (src/main.cpp:216) and expanding it would be scope creep. The one-sided check is the correct-intent fix; the sweep grid is a separate concern.

**Concrete change:** In `scripts/check_wtlfu_acceptance.py` `check_b_low_alpha()`:
```python
# before (two-sided)
rel = abs(w - l) / l
if rel > TOLERANCE:
    fail(...)

# after (one-sided — regression guard only)
signed_rel = (w - l) / l
if signed_rel > TOLERANCE:
    fail(f"WTLFU regresses vs LRU by {signed_rel*100:.2f}% (>2%)")
```

Docstring + Condition B label in `main()` both updated to reflect the one-sided semantics. TOLERANCE constant (0.02) and LOW_ALPHA_PROXY constant (0.6) preserved so the requirement literal is still grep-discoverable.

## Deviations from Plan

### Auto-fixed Issues

None during this resumed session — the deviation was the checkpoint-escalated Condition B semantics question, which the user resolved via the "one-sided tolerance" selection.

### Checkpoint-resolved

**1. [Checkpoint - Decision] Condition B two-sided → one-sided**
- **Found during:** Task 2 first run (sweep had just produced CSVs showing WTLFU beating LRU at α=0.6 by 7.84%)
- **Issue:** Plan-as-written had `abs()` tolerance which would fail whenever WTLFU substantially outperformed LRU — opposite of requirement intent
- **Resolution:** Escalated as `checkpoint:decision`; user selected "one-sided tolerance"
- **Fix:** Edited `check_b_low_alpha()` to use signed delta; updated docstring + main() label
- **Verification:** Checker exit 0 with all 3 PASS verdicts
- **Committed in:** `10f96e3`

---

**Total deviations:** 0 auto-fixed, 1 user-resolved via checkpoint
**Impact on plan:** Condition B semantics refined to match requirement intent. All 3 acceptance conditions now PASS on the real Congress sweep data. WTLFU-05 closed.

## Issues Encountered

- **`make plots` macOS libexpat quirk:** The Makefile already handles this via `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` + a pinned PYTHONPATH to the project's `.venv` (resolved in Phase 1's `fix(01)` commit `2dc8466`). `make plots` exited 0 with all 6 PDFs written cleanly — no HUMAN-UAT items deferred.

## User Setup Required

None — no external services, no new env vars.

## Next Phase Readiness

**Phase 2 is complete.** All 5 W-TinyLFU requirements (WTLFU-01..05) verified:

- ✓ WTLFU-01: `include/count_min_sketch.h` — CONSERVATIVE update, 10·W·D sample_size halving
- ✓ WTLFU-02: `include/wtinylfu.h` — 1% window / 99% SLRU / 80-20 split + plot-render entry
- ✓ WTLFU-03: `make_policy("wtinylfu", ...)` wired through `src/main.cpp`
- ✓ WTLFU-04: `tests/test_wtinylfu.cpp::test_hot_survives_scan` PASS under `make test`
- ✓ WTLFU-05: `scripts/check_wtlfu_acceptance.py --results-dir results/congress` exit 0

**Phase 3 prerequisites satisfied:**

- W-TinyLFU policy exists and is validated — the 6-policy sweep tooling works on any `results/<workload>/` tree, so Phase 3's CourtListener sweep can reuse it unchanged
- Acceptance-gate script pattern established; Phase 3 can add analogous gates under `scripts/check_*.py` if needed

**No blockers.** Phase 3 (CourtListener trace collection + 6-policy replay sweep) can begin.

## Self-Check: PASSED

- FOUND: scripts/check_wtlfu_acceptance.py
- FOUND: results/congress/alpha_sensitivity.csv (with W-TinyLFU rows, 7 occurrences)
- FOUND: results/congress/mrc.csv (with W-TinyLFU rows, 6 occurrences)
- FOUND: results/congress/figures/alpha_sensitivity.pdf (regenerated at 01:02)
- FOUND: results/congress/figures/mrc.pdf (regenerated at 01:02)
- FOUND: commit 10f96e3 (feat(02-06): add scripts/check_wtlfu_acceptance.py for WTLFU-05 gate)
- VERIFIED: `python3 scripts/check_wtlfu_acceptance.py --results-dir results/congress` → exit 0 with all 3 PASS verdicts
- VERIFIED: Python syntax parses clean via `ast.parse`
- VERIFIED: `make plots` → exit 0, 6 PDFs regenerated
- VERIFIED: Condition B uses signed delta `(w - l) / l` (not `abs()`); grep confirms `signed_rel = (w - l) / l`

---
*Phase: 02-w-tinylfu-core*
*Completed: 2026-04-19*
