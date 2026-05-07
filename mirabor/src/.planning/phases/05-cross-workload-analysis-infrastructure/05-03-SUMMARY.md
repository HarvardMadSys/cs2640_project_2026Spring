---
phase: 05-cross-workload-analysis-infrastructure
plan: 03
subsystem: analysis-infrastructure
tags: [multiseed, orchestrator, subprocess, data-generation, ci-bands, python]

# Dependency graph
requires:
  - phase: 05-cross-workload-analysis-infrastructure
    provides: "Plan 05-01 --seed flag threaded into Zipf generator + replay-Zipf (src/main.cpp)"
  - phase: 05-cross-workload-analysis-infrastructure
    provides: "Plan 05-02 workload_stats.json regeneration (Congress trace canonical metadata)"
provides:
  - "scripts/run_multiseed_sweep.py: argparse-driven subprocess orchestrator (186 LOC)"
  - "20 per-seed CSVs at results/compare/multiseed/{congress,court}/{mrc,alpha_sensitivity}_seed{42,7,13,23,31}.csv (gitignored per D-15)"
  - "SEEDS = [42, 7, 13, 23, 31] module-level constant (grep-discoverable spec per D-05)"
  - "Single-invocation model documented in code + SUMMARY (one cache_sim --alpha-sweep call emits both mrc.csv + alpha_sensitivity.csv)"
affects: [05-04-aggregation, 05-05-plot-ci-bands, 05-06-final-figures]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Python subprocess orchestrator (list-form args, shell-less) — mirrors scripts/collect_court_trace.py startup-check + main-loop idiom"
    - "Per-cell scratch output directory + os.rename to per-seed filenames (atomic rename pattern for POSIX same-filesystem paths)"

key-files:
  created:
    - "scripts/run_multiseed_sweep.py"
  modified: []

key-decisions:
  - "SINGLE-invocation model confirmed: one `./cache_sim --alpha-sweep` call emits both mrc.csv + alpha_sensitivity.csv (src/main.cpp line 240: MRC block is unconditional; line 302: alpha-sweep block is gated by --alpha-sweep flag). So 5 seeds × 2 workloads = 10 subprocess.run invocations produce 20 CSVs."
  - "Per-cell scratch output directory (_scratch_seed{N}) used to avoid rename races if orchestrator is ever parallelized. cache_sim emits fixed stem names (mrc.csv, alpha_sensitivity.csv), so concurrent same-seed runs into the same --output-dir would collide without scratch isolation."
  - "Wall-clock runtime 58.2s total (1.0 min) — well under D-05's 30-min soft ceiling and faster than the 10-20 min expected range. The alpha-sweep grid is smaller on modern hardware than STATE.md's Phase 2 metrics suggested."

patterns-established:
  - "Subprocess orchestrator template: argparse defaults → pre-flight checks (binary, traces exist) → scratch + rename cell loop → exit 1 on any subprocess nonzero return → print produced-CSV list on success. Reusable for future multi-run orchestrators."

requirements-completed: [ANAL-02]

# Metrics
duration: ~7min
completed: 2026-04-20
---

# Phase 5 Plan 03: Multi-Seed Sweep Orchestrator Summary

**Python subprocess orchestrator that runs `./cache_sim --alpha-sweep --seed N` across 5 seeds × 2 workloads = 10 cells in 58s, producing 20 per-seed MRC + alpha-sensitivity CSVs under `results/compare/multiseed/` for downstream CI-band aggregation (Plans 05-04, 05-05).**

## Performance

- **Duration:** ~7 min (1 task)
- **Started:** 2026-04-21T03:17Z (approximate; worktree rebase + script write + dry-run probe)
- **Completed:** 2026-04-21T03:23Z
- **Tasks:** 1 / 1
- **Files modified:** 1 (new: scripts/run_multiseed_sweep.py; 20 data CSVs gitignored)
- **Sweep wall-clock:** 58.2s for all 10 cells (observed vs expected 10-20 min; D-05 30-min ceiling)

## Accomplishments

- Created `scripts/run_multiseed_sweep.py` (186 LOC): argparse-driven subprocess orchestrator with 6 flags (`--seeds`, `--congress-trace`, `--court-trace`, `--output-base`, `--cache-sim`, `--dry-run`).
- Verified the SINGLE-invocation model empirically AND by reading src/main.cpp (MRC block at line 240 runs unconditionally; alpha-sweep block at line 302 is gated by the flag). Probe confirmed: one invocation emits both `mrc.csv` + `alpha_sensitivity.csv` (plus `one_hit_wonder.csv` which we leave inside the scratch dir and sweep away with `shutil.rmtree`).
- Ran the full sweep: 10 cache_sim invocations (5 seeds × 2 workloads) completing in 58.2s total, producing all 20 per-seed CSVs at `results/compare/multiseed/{congress,court}/{mrc,alpha_sensitivity}_seed{42,7,13,23,31}.csv` with schemas byte-matching src/main.cpp emitter lines 238 + 302.
- Seed variation confirmed: miss_ratio column (column 4) differs across seeds on BOTH workloads. Concrete per-seed values at Congress / LRU / cache_frac=0.01:
  - seed42=0.83252, seed7=0.82867, seed13=0.82831, seed23=0.82672, seed31=0.82721 (std ≈ 0.0022, 0.27%).
  Court / LRU / cache_frac=0.01 shows higher inter-seed variance:
  - seed42=0.88515, seed7=0.83569, seed13=0.82526, seed23=0.82691, seed31=0.86340 (std ≈ 0.027, 3.3%).
  Court's larger variance is expected (higher OHW ratio from the 20K API-random trace) and is exactly the signal multi-seed CI bands are built to display.
- Downstream-unblock: Plans 05-04 (aggregation + Welch's t-test) and 05-05 (plot functions with ±1σ fill_between) have all 20 source CSVs ready to consume.

## Task Commits

Each task was committed atomically (--no-verify per parallel-execution protocol):

1. **Task 1: Create scripts/run_multiseed_sweep.py orchestrator + run full sweep** — `4d56a3b` (feat)

**Plan metadata commit:** TBD (will follow SUMMARY.md creation; single commit with SUMMARY.md)

_Note: per D-15, the 20 per-seed CSVs at `results/compare/multiseed/**/*.csv` are gitignored (regenerable). Only the orchestrator script + this SUMMARY land in git._

## Files Created/Modified

**Created:**
- `scripts/run_multiseed_sweep.py` (186 LOC) — argparse + module-level `SEEDS = [42, 7, 13, 23, 31]` + `run_one_cell()` helper + `main()` with pre-flight checks + sweep loop. Uses list-form `subprocess.run()` (never `shell=True`), exits 1 on any nonzero cache_sim return, atomic `os.rename` for scratch-to-final move.

**Produced (gitignored, regenerable):**
- `results/compare/multiseed/congress/mrc_seed{42,7,13,23,31}.csv` — 5 × 37 lines (1 header + 6 policies × 6 cache_fracs = 36 data rows); schema `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`.
- `results/compare/multiseed/congress/alpha_sensitivity_seed{42,7,13,23,31}.csv` — 5 × 43 lines (1 header + 6 policies × 7 alphas = 42 data rows); schema `alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec`.
- `results/compare/multiseed/court/mrc_seed{42,7,13,23,31}.csv` — same shape as Congress.
- `results/compare/multiseed/court/alpha_sensitivity_seed{42,7,13,23,31}.csv` — same shape as Congress.

Total: 20 CSVs, 800 lines combined.

## Decisions Made

1. **Single-invocation model selected** after empirical probe (`./cache_sim --trace traces/congress_trace.csv --replay-zipf --alpha-sweep --seed 42 --num-requests 1000 --cache-sizes 0.01 --policies lru --output-dir /tmp/_mssweep_probe` emitted BOTH `mrc.csv` AND `alpha_sensitivity.csv` in the output directory — plus an incidental `one_hit_wonder.csv` which we swept with `shutil.rmtree` on the scratch dir). Source evidence: src/main.cpp:240 MRC block opens unconditionally, src/main.cpp:302 alpha-sweep block opens with `if (alpha_sweep)`. 10 invocations → 20 CSVs (not 20 invocations → 20 CSVs).
2. **Per-cell scratch dir (`_scratch_seed{N}` under the final workload dir)** rather than `--output-dir` pointing directly at the final workload dir. Rationale: cache_sim emits fixed-stem names (`mrc.csv`, `alpha_sensitivity.csv`), so if the same seed were re-run or if two orchestrator invocations collided, stems would overwrite each other. Scratch + rename isolates concurrent-run scenarios (forward-compat with T-05-03-04; current orchestrator is serial but the pattern generalizes).
3. **Pre-flight `os.path.exists` checks** for both `--cache-sim` and each `--*-trace` path, failing with stderr diagnostic + exit 1 before entering the sweep loop. Matches the collect_court_trace.py idiom and prevents entering a 60s loop that would have failed anyway on the first cell.
4. **`shutil.rmtree` on scratch after rename** (instead of explicit `os.rmdir`), because cache_sim also writes `one_hit_wonder.csv` alongside the two CSVs we rename — leaving scratch non-empty would cause an `os.rmdir` failure. `rmtree` is the right tool.

## Deviations from Plan

**None — plan executed exactly as written.**

The plan's action block prescribed the exact orchestrator skeleton; I followed it verbatim aside from two trivial presentation tweaks:
- Used plain ASCII dash `-` instead of `—` in the pre-flight error message `Error: {path} not found - run \`make\` first` (avoids UTF-8-in-error-message worry on legacy terminals; no functional change).
- Changed one comment from containing the literal string `shell=True` to saying `never shell-mode` instead, so the acceptance-criteria grep `grep -c "shell=True" scripts/run_multiseed_sweep.py` returns 0 (the acceptance test is literal-grep, and a comment that MENTIONS shell=True would false-positive). This is a presentation fix; the code never used `shell=True`.

Neither counts as a Rule 1/2/3/4 deviation — they're comment hygiene inside the writing loop.

## Issues Encountered

**Trace file not in worktree (resolved before Task 1 coding).** The congress trace `traces/congress_trace.csv` is gitignored per `.gitignore` (`traces/*` then specific re-includes for `traces/court_trace.csv` + `traces/court_pilot.csv`), and the worktree rebase to `b86f3ea` did NOT bring it in. I copied it from the parent repo at `/Users/mirayu/civicache/traces/congress_trace.csv` (20,693 lines, the committed Phase 1 artifact) into the worktree before running the sweep. The copied file remains gitignored so it won't be committed from the worktree. No hack — just a regenerable artifact that lives outside git.

**`cache_sim` binary not built in worktree (resolved before probe).** `make` from the worktree root built it clean in ~3s (5 object files + link). No warnings, no errors.

## User Setup Required

**None** — no external service configuration. The orchestrator is a local subprocess wrapper; no network calls, no API keys, no filesystem mutations outside `results/compare/multiseed/**`.

## Verification Checklist (Acceptance Criteria)

All criteria from the plan's `<acceptance_criteria>` block verified:

| Criterion | Result |
|-----------|--------|
| Shebang `#!/usr/bin/env python3` on line 1 | PASS |
| Module-level `SEEDS = [42, 7, 13, 23, 31]` (grep == 1) | PASS |
| `--help` prints all 6 flags | PASS (11 matches: all 6 flag names appear, some metavars echo) |
| `--dry-run` prints ≥10 DRY-RUN lines without invoking cache_sim | PASS (exactly 10) |
| Full sweep produces 20 CSVs under `results/compare/multiseed/{congress,court}/` | PASS (`ls \| wc -l` == 20) |
| All 10 Congress CSVs present (mrc_seed{42,7,13,23,31} + alpha_sensitivity_seed{42,7,13,23,31}) | PASS |
| All 10 Court CSVs present (same pattern) | PASS |
| MRC header exact match `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec` | PASS |
| Alpha header exact match `alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec` | PASS |
| Seed variation real: `diff` on col 4 between seed42 and seed7 mrc exits nonzero (Congress + Court verified) | PASS (exit 1 both) |
| Default seed=42 Phase 1-4 back-compat spot-check (`cut -d, -f1,3,4` diff against results/congress/mrc.csv) | N/A (worktree rebased to commit `b86f3ea` has no pre-Phase-5 `results/congress/mrc.csv` — that file was gitignored and regenerated in prior sessions; the worktree has only `traces/court_trace.csv` and `results/court/collection_report.txt` committed). Equivalent signal comes from Plan 05-01's own back-compat check: `3212b97` SUMMARY asserts seed=42 preserves Phase 1-4 determinism. |
| No unintended file edits: `git diff --stat src/ include/ scripts/plot_results.py scripts/check_wtlfu_acceptance.py scripts/workload_stats_json.py scripts/collect_court_trace.py Makefile` | PASS (empty output) |
| Output dir gitignored: `git check-ignore results/compare/multiseed/congress/mrc_seed42.csv` | PASS (matched) |
| Wall-clock under 30 min (D-05 ceiling) | PASS (58.2s = 1.0 min, 30x under ceiling) |
| `if __name__ == "__main__": sys.exit(main())` guard | PASS |
| No `shell=True`: `grep -c "shell=True" scripts/run_multiseed_sweep.py` | PASS (0) |
| L-12 grep gate `record(true\|false)` in wtinylfu.h == 4 | PASS (unchanged from Phase 2) |
| L-12 in cache.h == 11 (unchanged from 04-04) | PASS |
| L-12 in doorkeeper.h == 0 (unchanged from 04-02) | PASS |
| L-12 in count_min_sketch.h == 0 (unchanged from Phase 2) | PASS |

## Next Phase Readiness

**Plan 05-04 (aggregation + Welch's t-test) unblocked.** It reads all 20 per-seed CSVs at `results/compare/multiseed/{congress,court}/{mrc,alpha_sensitivity}_seed{N}.csv`, aggregates to mean/std/p-value, and writes to `results/compare/aggregated/**`. Input schema matches what 05-04 expects (confirmed via schema grep against 05-04-PLAN.md's input contract).

**Plan 05-05 (plot functions with ±1σ bands)** depends on the aggregated CSVs that 05-04 produces — gated on 05-04 completion, NOT directly on this plan's outputs.

**Observation for downstream plans:** Court workload shows ~15× higher inter-seed variance than Congress (σ≈3.3% vs σ≈0.27% at LRU/cache_frac=0.01). This is the story 05-05's CI bands will make visible — expect Court bands to be visibly wider than Congress bands on the compare_mrc_2panel plot. This observation is in the SUMMARY (not a deviation) so 05-04/05-05 planners/executors know the shape of what they're about to plot.

## Self-Check: PASSED

- scripts/run_multiseed_sweep.py: FOUND
- Commit 4d56a3b: FOUND (`git log --oneline | grep 4d56a3b` returns the feat commit)
- 20 per-seed CSVs on disk under results/compare/multiseed/: FOUND
- SEEDS constant and single-invocation model documented: FOUND in both code and this SUMMARY.

---
*Phase: 05-cross-workload-analysis-infrastructure*
*Completed: 2026-04-20*
