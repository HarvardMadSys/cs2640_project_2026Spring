# Codebase Concerns

**Analysis Date:** 2026-04-16

## Tech Debt

**No unit test suite:**
- Issue: There are no unit tests for any policy or algorithm. Correctness relies on end-to-end validation (MLE recovering the known Zipf alpha from synthetic traces, SHARDS curves matching exact MRC). No `*.test.*`, `*.spec.*`, or `tests/` directory exists.
- Files: All of `src/*.cpp`, `include/*.h`
- Impact: Regressions in cache policy code (`include/cache.h`) are only caught if they move the aggregate miss ratio. Per-policy invariants (size bookkeeping, ghost trimming, hand/frequency state) are not checked. The history of already-fixed bugs (SHARDS denominator, size-not-updated-on-hit, S3-FIFO freq tracking, ZipfGenerator OOB) suggests unit tests would have caught them earlier.
- Fix approach: Add a small `tests/` directory with GoogleTest or Catch2 that exercises each policy on tiny handcrafted traces with known hit/miss sequences; assert `stats.hits`, `stats.misses`, and internal size accounting. Hook into the `Makefile` with a `test` target.

**Alpha sweep regenerates trace per alpha value:**
- Issue: `src/main.cpp:219-222` calls `replay_zipf(raw_trace, num_requests, a)` (or `generate_zipf_trace`) inside the alpha loop for 7 alpha values. Each call does a full unique-object extraction, shuffle, CDF construction, and sampling pass, roughly 7x the trace-generation overhead.
- Files: `src/main.cpp:203-238`, `src/trace_gen.cpp:77-106`
- Impact: Alpha sweep dominates wall-clock on large traces. The unique-object extraction in `replay_zipf` (`src/trace_gen.cpp:81-88`) is the same every call; only the `ZipfGenerator` CDF depends on `alpha`. The simulator burns O(|raw_trace|) work per alpha unnecessarily.
- Fix approach: Extract and shuffle the unique-object list once outside the alpha loop, then construct a fresh `ZipfGenerator` per alpha and resample. Change `replay_zipf` signature to accept a pre-built `objects` vector, or add a `replay_zipf_prepared(...)` overload.

**`ZipfGenerator` CDF is O(n) memory and O(n) per sample lookup:**
- Issue: `src/trace_gen.cpp:9-27` stores the full CDF array of length `num_objects` and uses `std::lower_bound` for each `next()` call. For `num_objects = 50000` and `num_requests = 500000`, this is fine (log2(50000) ~ 16 comparisons), but the CDF is rebuilt from scratch for every alpha sweep iteration and every synthetic trace.
- Files: `src/trace_gen.cpp:9-27`, `include/trace_gen.h:11-19`
- Impact: Minor today, but couples CDF construction cost to trace-gen calls. Using `std::discrete_distribution` or inverse-CDF with precomputed table would be cleaner.
- Fix approach: Cache the CDF keyed on `(n, alpha, seed)`, or switch to rejection sampling.

**CLOCK policy has a dead-code branch for finding an empty slot:**
- Issue: `include/cache.h:157-169` has a `if (count_ < buffer_.size())` branch that searches for a slot with empty key, but `evict_one()` (`cache.h:176-194`) advances `hand_` past the erased slot immediately. The first iteration of the `for (size_t i = 0; i < buffer_.size(); i++)` loop will almost always pick `hand_` itself on the very first iteration since that's where the freshly erased slot typically sits — but the loop searches all slots, which is O(n) worst-case.
- Files: `include/cache.h:157-169`
- Impact: Cache inserts after eviction are O(buffer_size) instead of O(1) in the worst case.
- Fix approach: Track a free list of empty indices produced by `evict_one()` and pop from it on insert.

**Ghost set cleanup in S3-FIFO uses `ghost_set_.erase(key)` then relies on lazy queue cleanup:**
- Issue: `include/cache.h:262-264` erases `key` from `ghost_set_` when promoting a ghost hit but leaves the entry in `ghost_queue_`. The comment says "cleaned on trim" but the trim loop (`cache.h:305-309`) only pops the oldest and doesn't re-check set membership, so `ghost_queue_` can grow to contain stale entries beyond `ghost_max` before enough FIFO evictions flush them.
- Files: `include/cache.h:262-264`, `include/cache.h:300-310`
- Impact: Slightly bloated ghost memory; the FIFO ordering is still correct because a stale entry doesn't affect set lookups, and the queue bound is loose.
- Fix approach: Either use an ordered linked-list + iterator map (O(1) remove), or accept the bound as `2 * ghost_max` and document.

**Silent CSV parse errors in `load_trace`:**
- Issue: `src/trace_gen.cpp:31-50` does not check whether `iss >> e.timestamp` succeeded, whether `e.size` parsed, or whether the line had three fields. Malformed rows are silently pushed with zeroed/garbage fields.
- Files: `src/trace_gen.cpp:31-50`
- Impact: Corrupted trace files produce garbage results without warning. The raw `traces/congress_trace.csv` once logged 404s as entries — a parse-error check would not have caught that, but would catch truncated lines, non-numeric sizes, etc.
- Fix approach: Validate stream state after each field extraction; skip or warn on malformed rows.

**Header-only policies in `cache.h` mix interface and implementation (~400 lines):**
- Issue: `include/cache.h` defines five full policy classes inline. Every translation unit that includes it recompiles all five.
- Files: `include/cache.h`
- Impact: Build times scale with every `#include "cache.h"`. Currently only two TUs (`main.cpp`, `shards.cpp` transitively) include it, so impact is small, but the pattern doesn't scale.
- Fix approach: Split into `lru.h/.cpp`, `fifo.h/.cpp`, etc. with only declarations in headers.

## Known Bugs

No outstanding bugs known. See "Fixed (Historical Context)" below for the repaired issues from prior code-review rounds.

## Fixed (Historical Context)

These were fixed during three prior code-review rounds and are noted for provenance, not as outstanding work:

- **MRC unit mismatch (bytes vs. objects)** in `src/shards.cpp`. SHARDS now correctly uses object-count stack distances (see `src/main.cpp:263` passing `ws.unique_objects` as `max_cache`).
- **Per-access random sizes in `generate_zipf_trace`**. Sizes are now pre-generated per object (`src/trace_gen.cpp:60-64`) so repeated accesses see a consistent size.
- **404s logged as trace rows in `collect_trace.py`**. Non-200 responses are now skipped (`scripts/collect_trace.py:146-147`).
- **S3-FIFO algorithm wrong (no freq tracking, random ghost eviction)**. S3-FIFO now matches Yang et al. SOSP '23: small queue tracks per-entry `freq`, ghosts are FIFO-ordered via `std::deque<std::string> ghost_queue_` (`include/cache.h:205-334`).
- **`ZipfGenerator` OOB when `lower_bound` returns `end()`**. Guarded at `src/trace_gen.cpp:25`.
- **Size not updated on hit in all five policies**. LRU, FIFO, CLOCK, S3-FIFO, and SIEVE now update `current_size_`/`small_size_`/`main_size_` on hit (`include/cache.h:62-64, 99-101, 145-147, 244-246, 254-256, 359-361`).
- **SHARDS denominator bug**. `build_mrc` now divides by `total_sampled_` (`src/shards.cpp:138-144`), which is the correct normalizer given distances are already scaled by `1/rate`.

## Security Considerations

**Congress.gov API key in `.env`:**
- Risk: If `.env` is ever committed or leaked, the key grants quota-bounded access to Congress.gov.
- Files: `.env` (present in repo root, not tracked), `.gitignore:45` (`.env` correctly listed), `scripts/collect_trace.py:50-55` (reads `CONGRESS_API_KEY` from environment only — never from file)
- Current mitigation: `.env` is in `.gitignore`. `collect_trace.py` truncates the key to 8 chars when logging (`collect_trace.py:196`).
- Recommendations: Rotate the key periodically; consider adding a `.env.example` template so collaborators don't accidentally commit a real key while trying to create their own.

**No other secrets or credentials detected.** The codebase is a research simulator — no user-facing auth, no network endpoints served, no database access.

## Performance Bottlenecks

**Exact stack distance is O(n * unique_keys):**
- Problem: `exact_stack_distances` in `src/shards.cpp:11-34` uses a nested scan between last-seen positions with a fresh `std::unordered_set` each iteration — roughly O(n) per access in the worst case, O(n^2) overall.
- Files: `src/shards.cpp:11-34`
- Cause: Naive algorithm for a validation oracle.
- Improvement path: Use an order-statistic tree (e.g., Mattson's stack algorithm with a balanced BST) for O(n log n) exact stack distances. Current implementation is gated behind `--shards-exact` with a hard cap of 50000 trace rows (`src/main.cpp:289-290`), so this is a validation-only bottleneck.

**`workload_stats.cpp` harmonic number recomputed per Newton iteration:**
- Problem: `generalized_harmonic`, `..._deriv`, `..._deriv2` in `src/workload_stats.cpp:23-44` each loop `k=1..n` per call; called three times per Newton iteration (up to 50 iterations), capped at `n_fit=2000`. About 300K multiplications per `characterize()` call.
- Files: `src/workload_stats.cpp:23-95`
- Cause: No caching of `k^{-alpha}` across the three harmonic calls.
- Improvement path: Compute `k^{-alpha}` once per iteration and reuse across H, H', H''. Roughly 3x speedup for alpha estimation. Not critical — characterization runs once per trace.

**Simulator performance is already good:**
- 500K-request replay runs in <30s on modern hardware.
- SHARDS at 10% sampling is O(n log n) in the number of sampled accesses, dominated by the `std::set<uint64_t> access_times_` operations (`src/shards.cpp:109-126`).
- SHARDS at 0.1% on a 40K trace samples only ~17 accesses — the histogram is too sparse to produce a useful MRC curve (see "Fragile Areas").

## Fragile Areas

**Alpha sweep loop regenerates full trace per alpha:**
- Files: `src/main.cpp:219-235`
- Why fragile: Changing `replay_zipf`'s signature or internals (especially the unique-object extraction) silently blows up alpha-sweep runtime. The 7x overhead is not visible in the sweep's own logging.
- Safe modification: If you touch `replay_zipf` or the alpha-sweep loop, benchmark with `time ./cache_sim --trace ... --replay-zipf --alpha-sweep`. Consider factoring the object-extraction step out as suggested in Tech Debt.
- Test coverage: None — no tests exist for either `replay_zipf` or the sweep driver.

**SHARDS at 0.1% sampling on small traces:**
- Files: `src/main.cpp:261` (hardcoded rate list `{0.001, 0.01, 0.1}`), `src/shards.cpp:77-93`
- Why fragile: With 40K trace rows, 0.1% sampling produces only ~17 sampled accesses, which is not enough to build a meaningful MRC. The result plot looks broken (very coarse step function), but the code emits it without warning.
- Safe modification: If adding smaller trace sources, filter out sampling rates whose expected sample count is below some threshold (e.g., 200 samples).
- Test coverage: None. Validation of SHARDS vs. exact MRC only covered larger traces (<50K but typically 10K–30K).

**Trace representativeness is fundamentally limited:**
- Files: `scripts/collect_trace.py`, `traces/congress_trace.csv`
- Why fragile: Congress.gov does not publish server-side request logs, so `collect_trace.py` issues weighted-random queries against the API and logs its own requests. Raw trace Zipf alpha is ~0.23 and one-hit-wonder ratio is 92–99%, reflecting the generator, not real user behavior. The raw trace is therefore not a representative cache workload on its own.
- Safe modification: Use `--replay-zipf` mode (`src/main.cpp:132-139`, `src/trace_gen.cpp:77-106`) which reuses only the real keys and sizes and overlays synthetic Zipf popularity. Any change that removes or bypasses `--replay-zipf` without providing a real production trace invalidates the cache-comparison results.
- Test coverage: None. This is a methodology caveat rather than a code bug; document it in any downstream analysis.

**Raw trace contains legacy 404 rows:**
- Files: `traces/congress_trace.csv` (857 KB, pre-fix)
- Why fragile: About 73% of rows in the committed raw trace were 404 responses logged before `scripts/collect_trace.py` started filtering non-200s. These rows have real URLs but size = length of the 404 JSON payload, not a real resource. Any analysis that consumes the raw trace directly (not via `--replay-zipf`) is biased.
- Safe modification: Re-collect the trace with the current `collect_trace.py`, or preprocess `congress_trace.csv` to drop rows matching the 404 size signature before raw-trace analysis. `--replay-zipf` is unaffected because it overlays synthetic popularity; it only uses the (key, size) pairs for distinct objects.
- Test coverage: None.

**macOS Homebrew Python has broken `libexpat`:**
- Files: `scripts/plot_results.py`, `scripts/collect_trace.py`
- Why fragile: On macOS + Homebrew Python, `matplotlib`/`pandas` imports can fail with a `libexpat` dylib mismatch. Users typically need `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` or an alternate Python (see `.venv13/` suggesting a second Python was needed).
- Safe modification: Document the workaround in `README.md`. If you hit ImportError on Darwin, try the alternate Python before assuming a code bug.
- Test coverage: Environment-level; not testable in CI without macOS runners.

**SIEVE hand-pointer state is subtle:**
- Files: `include/cache.h:378-404`
- Why fragile: `evict_one()` moves `hand_` before erasing the victim node, with a special case for when the queue becomes size 1. The interaction with `hand_valid_` and the `if (hand_ == victim) hand_valid_ = false` check has two nested conditionals that are easy to break during refactoring.
- Safe modification: Any change here should run the full MRC against LRU/FIFO as a sanity check: SIEVE should sit between them, and `stats.hits + stats.misses` should equal the trace length.
- Test coverage: None — only end-to-end validation via MRC shape.

## Scaling Limits

**Exact stack-distance validation:**
- Current capacity: `<= 50,000` trace accesses (hardcoded at `src/main.cpp:289`).
- Limit: Quadratic memory and time beyond that.
- Scaling path: Replace `exact_stack_distances` with an order-statistic-tree implementation (Mattson et al.) — unlocks ~O(n log n) exact MRC for >1M accesses.

**Synthetic trace size:**
- Current capacity: Default 500K requests, 50K objects — runs in <30s.
- Limit: Memory for the full `std::vector<TraceEntry>` (each entry ~50 bytes including string). 10M-entry trace is ~500 MB.
- Scaling path: Stream the trace through the simulator instead of materializing in memory. Would require restructuring `run_simulation` to accept an iterator/generator.

**`ZipfGenerator` CDF memory:**
- Current capacity: Comfortable up to ~1M objects.
- Limit: CDF array is `sizeof(double) * num_objects`. 100M objects = 800 MB.
- Scaling path: Use an approximate rejection-sampling Zipf (Jörnsten trick) for very large object spaces.

## Dependencies at Risk

**Python `requests`, `matplotlib`, `numpy`, `pandas`, `scipy`:**
- Risk: Pinned only in `requirements.txt` without versions. A future incompatible major release could break `scripts/plot_results.py` or `scripts/collect_trace.py`.
- Impact: Plotting and trace collection would fail after a fresh install.
- Migration plan: Pin to known-good versions (e.g., `matplotlib>=3.7,<4.0`). Low priority given the project is a one-off CS 2640 artifact.

**No C++ external dependencies** — the simulator uses only the C++17 standard library. No risk.

## Missing Critical Features

**No CI / automated reproducibility:**
- Problem: No GitHub Actions, no build verification, no regression baseline for miss ratios.
- Blocks: A reviewer pulling the repo cannot verify that the committed results CSVs match a fresh run.

**No seed control for `collect_trace.py` timing:**
- Problem: `scripts/collect_trace.py:191` sets `random.seed(args.seed)` for the URL generator, but network timing jitter (`collect_trace.py:169-170`) and backoff cadence (`148-165`) are not seeded. Two runs with the same seed produce different traces.
- Blocks: Full trace-collection reproducibility.

**Byte miss ratio is recorded but not the stated cache capacity currency for SHARDS:**
- Problem: `SHARDS` produces object-count MRCs while the main comparison uses byte-capacity caches (`src/main.cpp:182`). This is an intentional design choice but there is no byte-based SHARDS path if a reviewer wants to cross-check.
- Blocks: Direct SHARDS-vs-simulation overlay in byte space.

## Test Coverage Gaps

**All of `include/cache.h` (five policies):**
- What's not tested: Per-policy invariants. No assertion that `current_size_ <= capacity_` after each access, no assertion that `stats.hits + stats.misses == trace.size()`, no handcrafted trace where the expected hit/miss sequence is known.
- Files: `include/cache.h:49-408`
- Risk: The history of size-not-updated-on-hit and S3-FIFO freq-tracking bugs shows how silently a policy can degrade while still producing "plausible" miss ratios.
- Priority: **High** — this is the project's core contribution.

**`src/shards.cpp` — SHARDS and exact stack distances:**
- What's not tested: No unit test for the FNV-1a hash, the threshold math (`threshold_ = rate * modulus_`), or the set-distance calculation (`access_times_.upper_bound / lower_bound`).
- Files: `src/shards.cpp:77-132`
- Risk: The prior denominator bug was caught only by MRC inspection. A test with a tiny, known-distance trace would have flagged it in milliseconds.
- Priority: **High**.

**`src/trace_gen.cpp` — generators and I/O:**
- What's not tested: CSV parser (`load_trace`), Zipf CDF construction, `replay_zipf` unique-object extraction.
- Files: `src/trace_gen.cpp:31-106`
- Risk: Silent CSV parse failures, off-by-one in CDF indexing.
- Priority: **Medium**.

**`src/workload_stats.cpp` — MLE alpha estimator:**
- What's not tested: Newton's method convergence, behavior with `n < 2`, harmonic number numerical stability at large `n`.
- Files: `src/workload_stats.cpp:46-95`
- Risk: Could silently return `alpha = 0.01` or `5.0` (clipped) if the gradient diverges.
- Priority: **Medium** — characterization is output, not a correctness input for cache sims.

**`scripts/collect_trace.py` and `scripts/plot_results.py`:**
- What's not tested: No Python tests. The trace collector's endpoint generators, weighted-congress pick, and 404-skip logic are unchecked.
- Files: `scripts/collect_trace.py`, `scripts/plot_results.py`
- Risk: Low — scripts are run interactively, and output is visually inspected.
- Priority: **Low**.

---

*Concerns audit: 2026-04-16*
