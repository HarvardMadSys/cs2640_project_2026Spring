# Architecture

**Analysis Date:** 2026-04-16

## Pattern Overview

**Overall:** Monolithic C++17 CLI simulator driven by a single `main()` entry point, paired with auxiliary Python data-collection and plotting scripts. The C++ core follows a **strategy pattern** around an abstract `CachePolicy` base class, with a **pipeline / data-flow** style on top: trace is produced (generated or loaded), characterized, fed through every selected policy, and the results are serialized to CSV for downstream Python plotting.

**Key Characteristics:**
- Header-heavy C++ layout: all cache policy implementations live inline in `include/cache.h` (no corresponding `.cpp`); other headers in `include/` only declare interfaces and are paired with implementation files in `src/`.
- Clean separation between simulation (C++) and presentation (Python + matplotlib). The only contract between them is CSV schemas.
- No external C++ dependencies — only the standard library. Python side uses `pandas`, `numpy`, `matplotlib`, and `requests`.
- Single-process, single-threaded; each policy is run sequentially across the full trace.
- Reproducibility is built in via fixed RNG seeds (`seed = 42` default) in `ZipfGenerator` and `replay_zipf`.

## Layers

**CLI / Orchestration Layer:**
- Purpose: Parse command-line flags, load or synthesize a trace, run every selected policy, dispatch to SHARDS, write CSV outputs and a human-readable text table.
- Location: `src/main.cpp`
- Contains: argument parsing (`argc/argv` scan), `make_policy()` factory, `run_simulation()` helper, inline CSV writers for each analysis section.
- Depends on: every `include/*.h` (all other layers).
- Used by: the `cache_sim` binary is invoked directly by the shell or by Makefile targets `run` and `run-sweep`.

**Trace Layer:**
- Purpose: Represent a sequence of cache accesses and manufacture traces from real data or Zipf distributions.
- Location: `include/trace_gen.h`, `src/trace_gen.cpp`
- Contains:
  - `TraceEntry` POD (`timestamp`, `key`, `size`) — defined in `include/cache.h` alongside the cache policies.
  - `ZipfGenerator` — inverse-CDF sampler backed by `std::mt19937_64`.
  - `load_trace()` — CSV reader for `timestamp,key,size` format.
  - `generate_zipf_trace()` — synthetic Zipf trace with log-normal object sizes (median ~4 KB, capped at 10 MB).
  - `replay_zipf()` — takes unique (key, size) pairs from a real trace, shuffles them, and re-samples accesses with Zipf popularity.
- Depends on: `include/cache.h` (for `TraceEntry`).
- Used by: `src/main.cpp` for trace ingest; `src/shards.cpp` and `src/workload_stats.cpp` consume `std::vector<TraceEntry>`.

**Cache-Policy Layer:**
- Purpose: Implement five eviction strategies behind a common interface and expose per-run hit/miss statistics.
- Location: `include/cache.h` (all five policies defined inline in the header).
- Contains:
  - `CachePolicy` abstract base — pure virtuals `access()`, `name()`, `reset()`; an embedded `CacheStats` member with `record(hit, size)`.
  - `LRUCache` — `std::list` + `std::unordered_map` iterator index; splice-to-tail on hit.
  - `FIFOCache` — same layout but no promotion on hit.
  - `CLOCKCache` — `std::vector<Entry>` ring buffer with reference bits and hand pointer.
  - `S3FIFOCache` — small FIFO (10%) + main FIFO (90%) + ghost FIFO filter with 2-bit frequency counters (Yang et al., SOSP '23).
  - `SIEVECache` — single FIFO list with a roving hand pointer; visited bits reset as hand passes (Zhang et al., NSDI '24).
- Depends on: `<unordered_map>`, `<list>`, `<deque>`, `<vector>`, `<unordered_set>` only.
- Used by: `src/main.cpp` via `make_policy()` factory (`std::unique_ptr<CachePolicy>`).

**Workload-Statistics Layer:**
- Purpose: Characterize a trace — Zipf alpha (MLE), one-hit-wonder ratio, size summary stats.
- Location: `include/workload_stats.h`, `src/workload_stats.cpp`
- Contains: `WorkloadStats` struct; `estimate_zipf_alpha()` (Newton's method on discrete power-law log-likelihood per Clauset et al. 2009, fitting top 2000 ranks); `one_hit_wonder_ratio()`; `characterize()`.
- Depends on: `include/cache.h` (for `TraceEntry`).
- Used by: `src/main.cpp` — runs once per trace load and prints a workload summary block.

**SHARDS / MRC Layer:**
- Purpose: Build miss-ratio curves using Spatially Hashed Approximate Reuse Distance Sampling (Waldspurger et al., FAST '15), plus an exact `O(n * unique)` reference implementation for validation.
- Location: `include/shards.h`, `src/shards.cpp`
- Contains:
  - `MRCPoint` (cache_size, miss_ratio).
  - `exact_stack_distances()` — brute-force histogram (used only with `--shards-exact`, guarded to traces ≤ 50K).
  - `mrc_from_stack_distances()` — converts a stack-distance histogram into an MRC at N points.
  - `SHARDS` class — FNV-1a key hash with `modulus = 10000`, `threshold = rate * modulus`; maintains `access_times_` (`std::set`) and `last_access_` (`unordered_map`) for O(log n) distance queries; scales sampled distances by `1/rate`.
- Depends on: `include/cache.h` (for `TraceEntry`).
- Used by: `src/main.cpp` when `--shards` or `--shards-exact` is supplied.

**Analysis / Presentation Layer (Python):**
- Purpose: Produce publication-quality PDF figures from the CSV results.
- Location: `scripts/plot_results.py`
- Contains: per-artifact plotting functions (`plot_mrc`, `plot_byte_mrc`, `plot_alpha_sensitivity`, `plot_ohw`, `plot_shards`, `plot_shards_error`, `plot_workload`) with a shared serif/no-grid style and consistent `POLICY_COLORS` / `POLICY_MARKERS` dictionaries.
- Depends on: `pandas`, `numpy`, `matplotlib` (Agg backend).
- Used by: `make plots`.

**Data-Collection Layer (Python):**
- Purpose: Build real Congress.gov API traces by sampling bill / amendment / roll-call-vote endpoints and recording `(timestamp, key, size)` for each successful response.
- Location: `scripts/collect_trace.py`
- Contains: endpoint templates, weighted random URL generator (bills 60% / amendments 25% / votes 15%, biased 60% toward the 119th Congress), rate-limited request loop with exponential backoff on 429/5xx.
- Depends on: `requests`, `CONGRESS_API_KEY` env var.
- Used by: the operator, ahead of running `cache_sim --trace`.

## Data Flow

**Primary simulation flow (`cache_sim`):**

1. `main.cpp` parses CLI flags and chooses one of three trace sources:
   - `--trace <file>` with `--replay-zipf` → `load_trace()` then `replay_zipf(raw_trace, num_requests, alpha)` (real keys/sizes, synthetic popularity).
   - `--trace <file>` alone → `load_trace()`.
   - No `--trace` → `generate_zipf_trace(num_requests, num_objects, alpha)`.
2. `characterize(trace)` computes Zipf alpha (MLE), one-hit-wonder ratio at 10% window, and size percentiles; printed to stdout.
3. `working_set_bytes(trace)` sums first-seen sizes to anchor cache capacity as a fraction of the working set.
4. For each `cache_frac × policy` cell: `make_policy(name, cache_bytes)` constructs a policy, `run_simulation()` walks the trace calling `policy.access(key, size)` which records a hit/miss on the embedded `CacheStats`; then `miss_ratio()` and `byte_miss_ratio()` are printed and appended to `results/mrc.csv`.
5. If `--alpha-sweep`: for each α in {0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2}, regenerate the trace (via `replay_zipf` when a real trace was loaded, otherwise `generate_zipf_trace`), re-anchor capacity at 1% working set, re-run every policy; write `results/alpha_sensitivity.csv`.
6. One-hit-wonder sweep over windows {1%, 5%, 10%, 20%, 50%, 100%} → `results/one_hit_wonder.csv`.
7. If `--shards`: for each sampling rate in {0.001, 0.01, 0.1}, `SHARDS.process(trace)` accumulates a scaled stack-distance histogram, then `build_mrc(max_cache=unique_objects, num_points=100)` emits `results/shards_mrc.csv`.
8. If `--shards-exact` (and trace ≤ 50K): `exact_stack_distances()` + `mrc_from_stack_distances()` produce `results/exact_mrc.csv`; SHARDS MAE and max error vs. exact are written to `results/shards_error.csv`.

**Presentation flow (`make plots`):**

1. `scripts/plot_results.py` reads each CSV under `results/` (skips gracefully if missing).
2. One figure per analysis is rendered to `results/figures/*.pdf`.
3. `plot_workload()` additionally reads `traces/congress_trace.csv` to chart response-size distribution and endpoint-type breakdown.

**Trace-collection flow (`scripts/collect_trace.py`):**

1. Read `CONGRESS_API_KEY` from environment.
2. Loop `--requests` times (or until `--duration` elapses): generate an endpoint URL via weighted random selection, GET it, on HTTP 200 append `timestamp,key,size` to the output CSV and flush; on 429/5xx back off exponentially; on other failures skip.
3. Jitter sleeps 1.2–2.0 s between requests to stay under the ~1 req/s API quota.

**State Management:**
- Each `CachePolicy` owns all its state (queue/list/map); `reset()` clears it between cache sizes. There is no shared mutable state between policies — `main.cpp` reconstructs a fresh policy per `(cache_frac, policy)` cell via `std::unique_ptr`.
- `SHARDS` instance is reused within a single sampling rate but discarded between rates.
- Traces are immutable `std::vector<TraceEntry>` once built.

## Key Abstractions

**`TraceEntry` (record struct):**
- Purpose: Single cache access — `timestamp`, `key` (string), `size` (bytes).
- Examples: `include/cache.h:11-15`
- Pattern: Plain-old-data value type; passed by `const&` through all consumers.

**`CachePolicy` (strategy interface):**
- Purpose: Uniform eviction-policy contract so the driver can iterate across every algorithm without special cases.
- Examples: `include/cache.h:34-47` — abstract base; concrete classes at `cache.h:50` (LRU), `87` (FIFO), `122` (CLOCK), `205` (S3-FIFO), `338` (SIEVE).
- Pattern: Classic strategy pattern + polymorphism via `std::unique_ptr<CachePolicy>`; stats collection is baked into the base class rather than injected.

**`CacheStats` (embedded counter):**
- Purpose: Hit / miss / byte-hit / byte-miss counters and derived ratios for the current run.
- Examples: `include/cache.h:17-31`
- Pattern: Composed as a public data member of `CachePolicy`, mutated by `record()` inside each `access()` call.

**`ZipfGenerator` (distribution sampler):**
- Purpose: Deterministic Zipf sampling via precomputed CDF and `std::lower_bound`.
- Examples: `include/trace_gen.h:11-19`, implementation in `src/trace_gen.cpp:9-27`.
- Pattern: RAII construction precomputes the CDF; `next()` is a single binary search + RNG draw.

**`SHARDS` (online estimator):**
- Purpose: Approximate MRC from a hash-sampled subset of the trace.
- Examples: `include/shards.h:30-66`, implementation in `src/shards.cpp:77-144`.
- Pattern: Stateful streaming estimator — `process()` walks a trace once; `build_mrc()` renders results without re-walking.

**`WorkloadStats` (summary record):**
- Purpose: Bundle of trace characterization metrics for stdout and downstream reporting.
- Examples: `include/workload_stats.h:7-15`
- Pattern: POD struct produced by the top-level `characterize()` function.

**`MRCPoint` (plot point):**
- Purpose: One `(cache_size, miss_ratio)` pair on a miss-ratio curve.
- Examples: `include/shards.h:10-14`
- Pattern: POD struct, returned as `std::vector<MRCPoint>`.

## Entry Points

**`main()` in `src/main.cpp`:**
- Location: `src/main.cpp:77`
- Triggers: `./cache_sim` invocation (either direct or via `make run` / `make run-sweep`).
- Responsibilities: CLI parsing, trace preparation, workload characterization, per-policy MRC computation, optional α-sweep, one-hit-wonder sweep, optional SHARDS construction, CSV output to `--output-dir` (default `results/`).

**`main()` in `scripts/plot_results.py`:**
- Location: `scripts/plot_results.py:296`
- Triggers: `make plots` or `python3 scripts/plot_results.py`.
- Responsibilities: Iterate each plotting function; skip missing CSVs; write PDFs to `results/figures/`.

**`main()` in `scripts/collect_trace.py`:**
- Location: `scripts/collect_trace.py:177`
- Triggers: `python3 scripts/collect_trace.py` with `CONGRESS_API_KEY` set.
- Responsibilities: Randomly walk the Congress.gov API, record each successful response as a trace row, respect rate limits with exponential backoff.

## Error Handling

**Strategy:** Minimal — this is a batch simulator, not a service. Errors are either printed to stderr and continue (Python trace collector backoff, unknown policy names) or cause early termination with non-zero exit (unreadable trace, unknown CLI flag).

**Patterns:**
- CLI parsing errors → `std::cerr << "Unknown option: ..."` then `return 1` (`src/main.cpp:121-124`).
- `load_trace()` returns an empty vector on open failure; `main()` checks and exits non-zero (`src/main.cpp:135-138`, `143-146`).
- `make_policy()` returns `nullptr` for unknown names; the driver prints `"Unknown policy: ..."` and skips (`src/main.cpp:186-189`).
- SHARDS exact-stack-distance guard: if `trace.size() > 50000`, print a skip notice instead of running the O(n²) path (`src/main.cpp:289-290`).
- Python trace collector: HTTP 429/5xx or `RequestException` → exponential backoff capped at 300 s, other non-200 responses silently skipped (`scripts/collect_trace.py:137-166`).
- `plot_results.py`: each plot function checks for its input CSV and prints `"Skipping ..."` instead of raising (`scripts/plot_results.py:51, 82, 122, 152, 178, 220, 272`).

## Cross-Cutting Concerns

**Logging:** `std::cout` for the user-facing progress table and workload summary; `std::cerr` for errors and usage. Python side uses `print()` for progress and skip notices. No structured logging framework.

**Validation:** Minimal input validation — CLI flags are parsed positionally, numeric fields use `std::stod` / `std::stoull` without try/catch. The SHARDS trace-size guard (`trace.size() > 50000`) is the only explicit domain check.

**Authentication:** Only relevant to the Python collector — `CONGRESS_API_KEY` is read from the environment and attached as a query param on a `requests.Session` (`scripts/collect_trace.py:117`). `.env` exists at repo root and is git-ignored (`.gitignore:45`).

**Reproducibility:** All RNG is seeded deterministically with default `seed = 42` (`include/trace_gen.h:17, 26, 33`; `scripts/collect_trace.py:185`). The Python collector also accepts `--seed`.

**Determinism of sampling:** SHARDS uses FNV-1a over the key string with modulus 10000, so the same key is always either sampled or not at a given rate — this is essential for correct stack-distance estimates (`src/shards.cpp:85-98`).

**Build reproducibility:** Only `-std=c++17 -O2 -Wall -Wextra`, no optional features or external libs. `-MMD -MP` emits `.d` dependency files under `build/` so incremental builds track header edits.

---

*Architecture analysis: 2026-04-16*
