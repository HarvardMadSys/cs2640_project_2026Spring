# Testing Patterns

**Analysis Date:** 2026-04-16

## Summary: No Unit Test Suite

**The project has no unit tests, no integration tests, and no test framework configured.** This section documents the actual verification strategy in use and the testing gap that should be addressed.

- No `tests/` or `test/` directory exists at the repo root (verified: the only match for `test` in the tree is `traces/test_trace.csv`, a small input fixture).
- No test framework is pulled into the build: no GoogleTest, no Catch2, no doctest, no Boost.Test in `include/`, no dependency listed in the Makefile.
- No Python test framework: `pytest` / `unittest` is not imported anywhere in `scripts/`, and neither appears in `requirements.txt`.
- The Makefile has no `test` target. Targets are only `all`, `clean`, `run`, `run-sweep`, `plots` (`Makefile:13`).
- Neither `pytest.ini`, `tox.ini`, `pyproject.toml`, `conftest.py`, `.coveragerc`, nor CI config files (`.github/workflows/`, `.circleci/`, `.travis.yml`) exist.

This is an academic CS 2640 project. Verification has been done through **end-to-end correctness checks against known ground truth** rather than isolated unit tests.

---

## Actual Verification Strategy (Current Practice)

### Approach 1: MLE Recovery of Known Zipf Alpha

**What is validated:** The `estimate_zipf_alpha` function in `src/workload_stats.cpp:46-95`.

**Method:** Generate a synthetic trace with a chosen true alpha (e.g., `alpha = 0.8`), run the MLE estimator on it, and compare the estimate to the true value.

**Recorded result (from `PROCESS.md` Phase 3, and from running `./cache_sim --alpha 0.8`):**
- True `alpha = 0.8`
- MLE estimate: `alpha_hat = 0.797`
- The earlier naive log-log regression gave `~0.72` on the same input, which is why MLE replaced it

**How to run it:**
```bash
make run
# Watch the "Estimated Zipf alpha:" line in the "Workload summary" block
```
The value is printed by `src/main.cpp:158` and is the de facto regression test.

**Weakness:** This is a single-point recovery check, not a sweep. The estimator is not validated across the full `alpha ∈ [0.6, 1.2]` sweep range, nor against edge cases (very small traces, heavily skewed traces, uniform traces).

### Approach 2: SHARDS Error Validation Against Exact Stack Distances

**What is validated:** The `SHARDS` class in `src/shards.cpp:77-144` — specifically, its miss-ratio-curve output.

**Method:** Run SHARDS with sampling rates `{0.1%, 1%, 10%}` on a trace, then compute the exact per-size miss ratios by brute-force stack distance enumeration (`exact_stack_distances` in `src/shards.cpp:11-34`, which is O(n²) but precise), then compare point-by-point.

**Metrics computed (in `src/main.cpp:306-336`):**
- **Mean Absolute Error (MAE)** between SHARDS MRC and exact MRC across all 100 cache-size points
- **Max absolute error** — worst-case deviation

**Recorded results (from `PROCESS.md` Phase 5):**

| Rate  | Sampled | MAE   | Max Error |
|-------|---------|-------|-----------|
| 0.1%  | 17      | 0.274 | 0.335     |
| 1%    | 325     | 0.065 | 0.142     |
| 10%   | 3,843   | **0.020** | **0.052** |

At 10% sampling, MAE is below 2%, matching the quality claim from Waldspurger et al. (FAST 2015).

**How to run it:**
```bash
./cache_sim --shards-exact
```
This flag both runs SHARDS and computes the exact baseline. Results are written to:
- `results/shards_mrc.csv` (SHARDS output)
- `results/exact_mrc.csv` (ground truth)
- `results/shards_error.csv` (per-rate MAE and max error)

The exact computation is auto-skipped when trace size exceeds 50 000 (`src/main.cpp:289-291`), because the algorithm is O(n·|unique_keys|).

**Weakness:** Only validated on one trace size (≤50 000 events). The 0.1% sampling rate is statistically useless at that size (only 17 samples in the example run) and cannot be validated at larger scales without a bigger synthetic fixture.

### Approach 3: Policy Comparison Against Published Results

**What is validated:** Qualitative ordering of cache policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE).

**Method:** Check that experimental orderings reproduce published claims from the source papers (Yang et al. SOSP '23, Zhang et al. NSDI '24):
- SIEVE should beat LRU on Zipf workloads (claim from NSDI '24)
- S3-FIFO should place between SIEVE and CLOCK (claim from SOSP '23)
- Ordering should hold across cache sizes and alpha values

**Recorded results (from `PROCESS.md` Phase 5 and Phase 4 "S3-FIFO impact" note):** All expected orderings hold on replay-Zipf traces after the Phase 4 bug fixes. In particular, the S3-FIFO bug where ordering flipped below CLOCK at small caches was caught this way — the qualitative orderings were the test.

**Weakness:** This is a smoke test against prior art, not a precise numeric check. It detects gross algorithmic errors but not subtle bugs that preserve ordering.

### Approach 4: Sanity Output and Eyeballing

**What is validated:** Ad-hoc correctness of simulation output.

**Method:**
- Console output prints the workload summary (`src/main.cpp:154-161`) and the MRC table (`src/main.cpp:172-197`), which are scanned by the developer for obvious anomalies.
- Plotting pipeline in `scripts/plot_results.py` produces PDFs that are visually checked.
- The round-1 and round-2 bug lists in `PROCESS.md` Phase 4 show that several critical bugs (MRC unit mismatch, per-access random sizes, 404s in trace, S3-FIFO algorithm error, SHARDS denominator bug, size-update-on-hit bug) were caught this way rather than by automated tests.

**Weakness:** Human-in-the-loop review does not scale and is not reproducible. Bugs in cold code paths (rare CLI flags, edge cases) will not be caught.

---

## Sample Data Used for Verification

- `traces/test_trace.csv` — a small trace checked into the working tree (the `traces/` directory is gitignored overall, but this specific file exists locally for ad-hoc runs)
- `traces/congress_trace.csv` — a real 40 K-request Congress.gov trace (also gitignored; regenerated via `scripts/collect_trace.py`)
- Synthetic Zipf traces generated in-process via `generate_zipf_trace()` (`src/trace_gen.cpp:52-73`) — parameterized by `num_requests`, `num_objects`, `alpha`, `seed`; deterministic given a seed

No permanent, checked-in test fixtures exist. The synthetic-trace path with a fixed `seed = 42` is the closest thing to a reproducible test vector.

---

## Gaps (What a Proper Test Suite Should Cover)

The project explicitly acknowledges the testing gap in `PROCESS.md`:

> "S3-FIFO implementation earlier. The algorithm was wrong from the start. **Should have unit-tested each policy against known traces with hand-computed expected outputs before running experiments.**"

### High-priority gaps

1. **No per-policy unit tests.** Each of `LRUCache`, `FIFOCache`, `CLOCKCache`, `S3FIFOCache`, `SIEVECache` in `include/cache.h` could be validated against hand-computed hit/miss sequences on small hard-coded traces (e.g., "trace `[A,B,C,A,D,B]` with capacity 3 bytes of 1-byte objects should produce hits=`X`, misses=`Y` under LRU"). Without this, internal state bugs (like the Phase 4 Round 2 `size-not-updated-on-hit` bug that affected all five policies) can sit undetected.

2. **No bounds/edge-case tests** for:
   - Empty trace (`load_trace` on nonexistent file returns empty — not exercised under test)
   - Trace with a single repeated key
   - Cache capacity 0 or 1
   - Object larger than the cache (should evict everything and still miss)
   - `ZipfGenerator` with `n = 0` or `n = 1`
   - `SHARDS` with `rate = 0` (currently clamped to threshold=1, not tested)
   - `estimate_zipf_alpha` on traces with <2 unique keys (returns 0.0 — not tested)

3. **No I/O format tests** for `load_trace` (`src/trace_gen.cpp:31-50`). Malformed rows, missing header, extra whitespace, and embedded commas in keys are all untested failure modes.

4. **No tests for Python scripts.** `scripts/collect_trace.py` has complex retry/backoff logic (`collect_trace` function, `scripts/collect_trace.py:103-174`) that is entirely unverified. Mock-based testing of the `requests.Session` interactions would catch rate-limit regressions without burning real API quota.

5. **No regression tests on CSV output schemas.** If a column name changes, downstream `scripts/plot_results.py` silently falls back (see the `cache_size_objects` vs `cache_size` detection in `scripts/plot_results.py:186, 200`), masking the break.

### Lower-priority gaps

6. **No performance regression tests.** The SHARDS algorithm uses `std::distance(set::iterator, set::iterator)` which is O(n) in the worst case; a performance regression would not be detected.

7. **No fuzzing.** CLI parsing in `src/main.cpp:92-125` is hand-rolled and could be fuzzed with malformed argv.

---

## Recommended Framework Choices (If Tests Are Added)

**C++:** Catch2 v3 or doctest — single-header, no external deps, matches the project's "C++17 and nothing else" constraint. GoogleTest would require pulling in an entire external build system and is overkill for a 4-file project.

**Python:** `pytest` — standard, and `requests-mock` for network tests of `scripts/collect_trace.py`.

**Coverage:** Not currently measured. `lcov` / `gcovr` for C++ and `coverage.py` for Python would integrate cleanly.

**Suggested layout if added:**
```
tests/
├── cpp/
│   ├── test_cache_policies.cpp   # LRU/FIFO/CLOCK/S3-FIFO/SIEVE against hand-traced expectations
│   ├── test_zipf.cpp             # ZipfGenerator and estimate_zipf_alpha
│   ├── test_shards.cpp           # SHARDS vs exact MRC on small fixtures
│   └── test_trace_io.cpp         # load_trace edge cases
├── py/
│   ├── test_collect_trace.py     # requests-mock for retry/backoff logic
│   └── test_plot_results.py      # golden-CSV -> golden-PDF checks (or schema-only)
└── fixtures/
    ├── tiny_trace.csv
    └── zipf_alpha_0.8.csv
```

A Makefile target `test:` would run both suites. None of this exists today.

---

## How to Verify Correctness Today (Practical Guide)

Until a real test suite is added, use these commands as regression checks:

```bash
# Build cleanly
make clean && make

# Regression 1: MLE should recover alpha=0.797 from true alpha=0.8
./cache_sim --alpha 0.8
# Expected: "Estimated Zipf alpha: 0.797" (±0.01)

# Regression 2: SHARDS MAE at 10% sampling should be ≤ 0.03
./cache_sim --shards-exact --num-requests 40000
# Expected: results/shards_error.csv shows MAE ~0.02 at rate=0.1

# Regression 3: Policy ordering on Zipf alpha=0.8, 1% cache
# Expected ordering (best to worst miss ratio): SIEVE < S3-FIFO < CLOCK < LRU < FIFO
./cache_sim --cache-sizes 0.01
# Scan the printed MRC table

# Regression 4: Plot pipeline produces all 7 PDFs
make plots
ls results/figures/
# Expected: mrc.pdf, byte_mrc.pdf, alpha_sensitivity.pdf, ohw.pdf,
#           shards_mrc.pdf, shards_error.pdf, workload.pdf
```

Any deviation from these expected outputs is a regression.

---

*Testing analysis: 2026-04-16*
