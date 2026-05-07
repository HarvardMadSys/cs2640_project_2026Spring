# Phase 1: Enabling Refactors & CourtListener Pilot — Pattern Map

**Mapped:** 2026-04-18
**Files analyzed:** 9 code files (7 modified, 2 created) + 1 data migration (`git mv`)
**Analogs found:** 9 / 9 (all files have strong in-repo analogs)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `include/hash_util.h` (new) | utility header | hashing | `include/cache.h` (header-only policies) + `src/shards.cpp:85-93` (FNV impl) | exact (extraction) |
| `scripts/pilot_court_trace.py` (new) | trace-collector script | I/O + rate-limited HTTP | `scripts/collect_trace.py` | exact (copy-modify) |
| `src/shards.cpp` (modify) | simulation impl | hashing (now delegated) | self (`src/shards.cpp:85-93`) | exact (in-place replace) |
| `src/trace_gen.cpp` (modify) | trace-gen impl | trace-gen (dedupe + Zipf sample) | self (`src/trace_gen.cpp:77-106`) | exact (split + wrap) |
| `include/trace_gen.h` (modify) | trace-gen header | trace-gen API | self (`include/trace_gen.h:28-34`) | exact (add 2 decls) |
| `src/main.cpp` (modify) | CLI/orchestration | simulation + timing | self (`src/main.cpp:166-200`, `269-278`) | exact (extend MRC + SHARDS timing pattern) |
| `scripts/plot_results.py` (modify) | analysis script | I/O + plotting | self (`scripts/plot_results.py:48-75`, `296-315`) | exact (extend) |
| `include/cache.h` (possible no-op) | header | CSV schema owner | self (`include/cache.h:17-31`) | exact (CacheStats is the CSV driver) |
| `Makefile` (possible no-op) | build config | build | self (`Makefile:2, 26`) | exact (`-MMD -MP` picks up new header automatically) |
| `.env.example` (new) | config doc | config | none — new file; mirror `.env` shape from `collect_trace.py:50-55` | no-analog (trivial) |
| `results/*.csv` → `results/congress/` | data migration | file-I/O | N/A (`git mv`) | N/A |

**Key decision:** `include/cache.h` and `Makefile` likely need ZERO edits. `CacheStats` isn't the CSV writer — `src/main.cpp:169` writes the header string literally. Throughput is emitted there, not via `CacheStats`. Dependency tracking in `Makefile` auto-picks up `hash_util.h` via `-MMD -MP` on line 2. Plan should confirm no-op but not force an edit.

---

## Pattern Assignments

### `include/hash_util.h` (NEW — utility header, hashing)

**Analogs:**
- **Header-only pattern** → `include/cache.h` (entire file — pragma once, inline methods, no paired .cpp)
- **FNV-1a body** → `src/shards.cpp:85-93`
- **Header conventions** → `include/trace_gen.h:1-8` (header ordering, `#pragma once`, includes)

**Header guard + includes pattern** (mirror `include/trace_gen.h:1-8`):
```cpp
#pragma once
#include <cstdint>
#include <string>
```

**Core FNV-1a body to extract verbatim** (from `src/shards.cpp:85-93`):
```cpp
uint64_t SHARDS::hash_key(const std::string& key) const {
    // FNV-1a 64-bit hash
    uint64_t hash = 14695981039346656037ULL;
    for (char c : key) {
        hash ^= (uint64_t)(unsigned char)c;
        hash *= 1099511628211ULL;
    }
    return hash;
}
```

**Target shape in `hash_util.h`** (parameterize basis per D-12; keep the two standard FNV-1a magic constants inline as they are today — no macros, matching `CONVENTIONS.md:50` "No #define constants"):
```cpp
// ==================== FNV-1a 64-bit ====================
constexpr uint64_t FNV_BASIS = 14695981039346656037ULL;
constexpr uint64_t FNV_PRIME = 1099511628211ULL;

// Four golden-ratio-derived seeds for W-TinyLFU CMS (Phase 2 consumer).
// Values per D-12 — use distinct 64-bit primes derived from the golden ratio.
constexpr uint64_t FNV_SEED_A = 0x9e3779b97f4a7c15ULL;
constexpr uint64_t FNV_SEED_B = 0xbf58476d1ce4e5b9ULL;
constexpr uint64_t FNV_SEED_C = 0x94d049bb133111ebULL;
constexpr uint64_t FNV_SEED_D = 0xda942042e4dd58b5ULL;

inline uint64_t fnv1a_64(const std::string& s, uint64_t seed = FNV_BASIS) {
    uint64_t hash = seed;
    for (char c : s) {
        hash ^= (uint64_t)(unsigned char)c;
        hash *= FNV_PRIME;
    }
    return hash;
}
```

**Naming convention** (from `CONVENTIONS.md:49-52`): "No `#define` constants in the code — magic numbers are inline with comments. Hash seeds are literal constants." Use `constexpr` at namespace scope (not `#define`).

**Self-test pattern** (from specifics block): Add an `inline` function `hash_util_self_test()` that asserts `fnv1a_64("hello") == 0xa430d84680aabd0bULL` (the published FNV-1a-64 vector for "hello"). Caller in `main.cpp` calls once at startup; failure prints to `std::cerr` and returns 1 — error-handling style from `src/trace_gen.cpp:33-37`.

---

### `src/shards.cpp` (MODIFY — replace local FNV with include)

**Analog:** self (in-place edit).

**Current local impl** (to be deleted, `src/shards.cpp:85-93`):
```cpp
uint64_t SHARDS::hash_key(const std::string& key) const {
    // FNV-1a 64-bit hash
    uint64_t hash = 14695981039346656037ULL;
    for (char c : key) {
        hash ^= (uint64_t)(unsigned char)c;
        hash *= 1099511628211ULL;
    }
    return hash;
}
```

**Replacement**:
- Add `#include "hash_util.h"` to `src/shards.cpp:1-6` block (alphabetical-ish; project uses logical-dependency order per `CONVENTIONS.md:106-111` — put project headers after stdlib, so insert just after `#include "shards.h"` on line 1).
- Replace the method body with: `return fnv1a_64(key);`
- **Keep the method signature** (`uint64_t SHARDS::hash_key(const std::string& key) const`) so `include/shards.h:46` declaration stays unchanged and SHARDS behavior is provably identical (`SHARDS_SEED = FNV_BASIS` per D-13).

**Include ordering** (from `src/main.cpp:1-14` — mirror it):
```cpp
#include "shards.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include "hash_util.h"   // NEW — project header last
```

---

### `src/trace_gen.cpp` (MODIFY — split `replay_zipf` per D-10/D-11)

**Analog:** self (`src/trace_gen.cpp:77-106`).

**Current `replay_zipf` — the source of the split** (`src/trace_gen.cpp:77-106`):
```cpp
std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed) {
    // Extract unique objects, keeping the first-seen size for each key
    std::unordered_map<std::string, uint64_t> seen;
    std::vector<std::pair<std::string, uint64_t>> objects; // (key, size)
    for (auto& e : real_trace) {
        if (!seen.count(e.key)) {
            seen[e.key] = e.size;
            objects.push_back({e.key, e.size});
        }
    }

    std::cout << "Replay-Zipf: " << objects.size() << " unique objects from real trace, "
              << "generating " << num_requests << " accesses with alpha=" << alpha << "\n";

    // Shuffle object order so Zipf ranking isn't tied to collection order
    std::mt19937_64 shuffle_rng(seed);
    std::shuffle(objects.begin(), objects.end(), shuffle_rng);

    ZipfGenerator zipf(objects.size(), alpha, seed + 1);

    std::vector<TraceEntry> trace;
    trace.reserve(num_requests);
    for (uint64_t i = 0; i < num_requests; i++) {
        uint64_t rank = zipf.next();
        trace.push_back({i, objects[rank].first, objects[rank].second});
    }
    return trace;
}
```

**Target split** (per D-10, D-11):

```cpp
// NEW — dedupe-then-shuffle step, called ONCE per real trace.
std::vector<std::pair<std::string, uint64_t>> prepare_objects(
        const std::vector<TraceEntry>& raw_trace, uint64_t seed) {
    std::unordered_map<std::string, uint64_t> seen;
    std::vector<std::pair<std::string, uint64_t>> objects;
    for (auto& e : raw_trace) {
        if (!seen.count(e.key)) {
            seen[e.key] = e.size;
            objects.push_back({e.key, e.size});
        }
    }
    std::mt19937_64 shuffle_rng(seed);
    std::shuffle(objects.begin(), objects.end(), shuffle_rng);
    return objects;
}

// NEW — Zipf sampling over prepared objects, called N times per alpha sweep.
std::vector<TraceEntry> generate_replay_trace(
        const std::vector<std::pair<std::string, uint64_t>>& objects,
        uint64_t num_requests, double alpha, uint64_t seed) {
    ZipfGenerator zipf(objects.size(), alpha, seed + 1);
    std::vector<TraceEntry> trace;
    trace.reserve(num_requests);
    for (uint64_t i = 0; i < num_requests; i++) {
        uint64_t rank = zipf.next();
        trace.push_back({i, objects[rank].first, objects[rank].second});
    }
    return trace;
}

// EXISTING (now a thin wrapper, per D-11, preserves signature and behavior).
std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed) {
    auto objects = prepare_objects(real_trace, seed);
    std::cout << "Replay-Zipf: " << objects.size() << " unique objects from real trace, "
              << "generating " << num_requests << " accesses with alpha=" << alpha << "\n";
    return generate_replay_trace(objects, num_requests, alpha, seed);
}
```

**Seed contract to preserve:** the legacy `replay_zipf(seed)` must yield an identical trace to pre-refactor. The current code uses `seed` for the shuffle RNG and `seed + 1` for the Zipf RNG — preserve both exactly. (Validation hook: run the pre-refactor binary on `traces/test_trace.csv` and capture trace checksums before/after.)

**Existing `generate_zipf_trace` as a template** for how sizes+keys are bundled into `TraceEntry` (`src/trace_gen.cpp:52-73`) — follow the same `trace.reserve(num_requests)` + single-loop style.

---

### `include/trace_gen.h` (MODIFY — export 2 new functions)

**Analog:** self (`include/trace_gen.h:28-34`).

**Existing declaration pattern to mirror**:
```cpp
// Replay real keys/sizes from a trace with Zipf-distributed popularity.
// Extracts unique (key, size) pairs from the input trace, ranks them
// arbitrarily, and generates num_requests accesses where the probability
// of accessing rank-k object follows Zipf(alpha).
std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed = 42);
```

**Two declarations to add** (preserve 3-line docstring comment pattern above each):
```cpp
// Dedupe a real trace into unique (key, size) pairs and shuffle deterministically.
// Call ONCE per raw trace; the returned object list is reused across alpha values
// in replay_zipf-based alpha sweeps to avoid O(N) re-dedup per sweep cell.
std::vector<std::pair<std::string, uint64_t>> prepare_objects(
        const std::vector<TraceEntry>& raw_trace, uint64_t seed = 42);

// Sample num_requests accesses from a prepared object list, where the probability
// of accessing rank-k object follows Zipf(alpha). Uses seed+1 for the Zipf RNG
// to preserve the legacy replay_zipf seeding contract.
std::vector<TraceEntry> generate_replay_trace(
        const std::vector<std::pair<std::string, uint64_t>>& objects,
        uint64_t num_requests, double alpha, uint64_t seed = 42);
```

**Include ordering** (already correct on lines 1-8) — no changes to existing `#include` block; `std::pair` is transitively included via `<vector>` but explicit `<utility>` is safer; check if existing headers already pull it in (they do, via `<algorithm>`). Don't add `<utility>` unless the build breaks.

---

### `src/main.cpp` (MODIFY — throughput timing + alpha-sweep refactor + `prepare_objects` one-shot)

**Analog:** self — multiple sections.

#### Pattern A: MRC loop gets throughput column (`src/main.cpp:165-200`)

**Existing MRC block** (lines 165-200):
```cpp
{
    std::string csv_path = output_dir + "/mrc.csv";
    std::ofstream csv(csv_path);
    csv << "cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio\n";
    // ...
    for (double frac : cache_fracs) {
        uint64_t cache_bytes = (uint64_t)(total_bytes * frac);
        // ...
        for (auto& pn : policy_names) {
            auto p = make_policy(pn, cache_bytes);
            if (!p) { /* error */ continue; }
            run_simulation(trace, *p);
            std::cout << std::setw(10) << std::setprecision(4) << p->stats.miss_ratio();
            csv << frac << "," << cache_bytes << "," << p->name() << ","
                << p->stats.miss_ratio() << "," << p->stats.byte_miss_ratio() << "\n";
        }
        std::cout << "\n";
    }
}
```

**Throughput timing — lift the existing `std::chrono::steady_clock` block from SHARDS** (`src/main.cpp:271-278`):
```cpp
auto t_start = std::chrono::steady_clock::now();
shards.process(trace);
auto t_end = std::chrono::steady_clock::now();
double elapsed = std::chrono::duration<double>(t_end - t_start).count();
```

**Target wrap around `run_simulation` call** (per D-01, D-02):
```cpp
auto t_start = std::chrono::steady_clock::now();
run_simulation(trace, *p);
auto t_end = std::chrono::steady_clock::now();
double elapsed = std::chrono::duration<double>(t_end - t_start).count();
double accesses_per_sec = elapsed > 0 ? (double)trace.size() / elapsed : 0.0;

csv << frac << "," << cache_bytes << "," << p->name() << ","
    << p->stats.miss_ratio() << "," << p->stats.byte_miss_ratio() << ","
    << accesses_per_sec << "\n";
```

**CSV header update** (line 169):
```cpp
csv << "cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec\n";
```

#### Pattern B: Alpha sweep — `prepare_objects` lifted out of the loop (`src/main.cpp:202-238`)

**Existing alpha sweep** (line 220-222) — the hot spot:
```cpp
for (double a : alphas) {
    auto sweep_trace = (!raw_trace.empty())
        ? replay_zipf(raw_trace, num_requests, a)   // re-dedupes raw_trace 7x
        : generate_zipf_trace(num_requests, num_objects, a);
    // ...
}
```

**Target** — hoist `prepare_objects` above the loop when `raw_trace` is non-empty:
```cpp
std::vector<std::pair<std::string, uint64_t>> prepared_objects;
if (!raw_trace.empty()) {
    prepared_objects = prepare_objects(raw_trace);  // once, default seed=42
}

for (double a : alphas) {
    auto sweep_trace = !prepared_objects.empty()
        ? generate_replay_trace(prepared_objects, num_requests, a)
        : generate_zipf_trace(num_requests, num_objects, a);
    // ... also wrap run_simulation with steady_clock and add accesses_per_sec
}
```

**Alpha CSV header update** (line 207):
```cpp
csv << "alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec\n";
```

#### Pattern C: SHARDS throughput (D-03)

SHARDS timing already exists at `src/main.cpp:271-274`. Per D-03, also emit an `accesses_per_sec` column in `shards_mrc.csv`. The cleanest wiring: add a `throughput` column populated once per sampling rate (same value on every MRC row for that rate):
```cpp
// Line 267 — old header:
csv << "sampling_rate,cache_size_objects,miss_ratio\n";
// Target:
csv << "sampling_rate,cache_size_objects,miss_ratio,accesses_per_sec\n";
```
Compute `accesses_per_sec = trace.size() / elapsed` right after line 274, then append to each row when writing lines 280-282.

#### Pattern D: Self-test call for `fnv1a_64`

Add at the top of `main()` body (just after the arg-parsing block, `src/main.cpp:127` area), before the `=== Cache Policy Simulator ===` banner:
```cpp
if (!hash_util_self_test()) {
    std::cerr << "hash_util self-test failed — aborting\n";
    return 1;
}
```

Matches the error-style from `src/main.cpp:120-124` (print + return 1).

---

### `scripts/plot_results.py` (MODIFY — subdir layout + `--workload` flag)

**Analog:** self (`scripts/plot_results.py:296-315` for arg parsing; `48-75` for a plotting function).

**Existing argparse block** (lines 296-302):
```python
def main():
    parser = argparse.ArgumentParser(description="Plot cache simulation results")
    parser.add_argument("--results-dir", default="results",
                        help="Directory containing CSV outputs")
    parser.add_argument("--traces-dir", default="traces",
                        help="Directory containing raw trace CSVs")
    args = parser.parse_args()
```

**Target pattern** (add `--workload`, default `congress` per D-05; derive effective `results_dir` from it unless explicit):
```python
def main():
    parser = argparse.ArgumentParser(description="Plot cache simulation results")
    parser.add_argument("--workload", default="congress",
                        help="Workload subdir under results/ (default: congress)")
    parser.add_argument("--results-dir", default=None,
                        help="Directory containing CSV outputs (overrides --workload)")
    parser.add_argument("--traces-dir", default="traces",
                        help="Directory containing raw trace CSVs")
    args = parser.parse_args()

    results_dir = args.results_dir or os.path.join("results", args.workload)
    figures_dir = os.path.join(results_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    # ...existing plot_* calls unchanged — they already take results_dir
```

**Existing per-plot function pattern to preserve** (`scripts/plot_results.py:48-75`) — every `plot_*(results_dir, figures_dir)` already takes `results_dir` as its first arg, so the refactor is confined to `main()`. No body edits to `plot_mrc`, `plot_byte_mrc`, `plot_alpha_sensitivity`, etc.

**Workload trace path** (`plot_workload`, line 219) currently hardcodes `congress_trace.csv`:
```python
trace_path = os.path.join(traces_dir, "congress_trace.csv")
```
Target — parameterize on the workload arg:
```python
trace_path = os.path.join(traces_dir, f"{args.workload}_trace.csv")
```
This requires passing `args.workload` or the filename into `plot_workload(traces_dir, figures_dir, workload)`.

**Read-pattern reminder** (`CONVENTIONS.md:374`): "Python reads with pandas (`pd.read_csv(path)`) — never the `csv` module for reading." Keep this for the new `accesses_per_sec` column reads.

---

### `scripts/pilot_court_trace.py` (NEW — pilot trace collector)

**Analog:** `scripts/collect_trace.py` (copy-modify per CONTEXT.md code_context; do NOT generalize per D-04 of RESEARCH).

**Header + imports to copy verbatim** (`scripts/collect_trace.py:1-23`):
```python
#!/usr/bin/env python3
"""
CourtListener v4 API pilot trace collector (Phase 1 sanity check).

Fires a small fixed number of requests across 4 planned endpoints and
emits a per-endpoint success tally (200s / 404s / 403s / 429s / total).
Phase 1 gate: each endpoint must hit >=70% success.
"""

import argparse
import csv
import os
import random
import sys
import time

import requests
```

**API base + endpoints constants pattern** (mirror `collect_trace.py:24-47`) — per `STACK.md` suggested ID ranges:
```python
BASE_URL = "https://www.courtlistener.com/api/rest/v4"

ENDPOINTS = {
    "docket":  {"path": "/dockets/{id}/",             "id_range": (1, 80_000_000)},
    "opinion": {"path": "/opinions/{id}/",            "id_range": (1, 15_000_000)},
    "cluster": {"path": "/clusters/{id}/",            "id_range": (1, 12_000_000)},
    "court":   {"path": "/courts/{court_id}/",        "court_ids": [
        "scotus", "ca1", "ca2", "ca3", "ca9", "nysd", "cand",
        # ... short hand-curated list ~20 entries to start
    ]},
}
# Pilot: even weights across endpoints (unlike production 60/25/10/5 — we want
# per-endpoint statistical signal, not workload realism).
ENDPOINT_ORDER = ["docket", "opinion", "cluster", "court"]
```

**`get_api_key` pattern** (copy from `collect_trace.py:50-55`, substitute env var name per D-12/research):
```python
def get_api_key():
    key = os.environ.get("COURTLISTENER_API_KEY")
    if not key:
        print("Error: set COURTLISTENER_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)
    return key
```

**HTTP loop with Token auth + backoff** (mirror `collect_trace.py:116-166`). Key differences from Congress:
1. `Authorization: Token <token>` header (not query param):
   ```python
   session = requests.Session()
   session.headers.update({"Authorization": f"Token {api_key}"})
   session.params = {"format": "json"}
   ```
2. `base_delay = 0.8` (not 1.2) — per STACK.md rate limit analysis.
3. Jitter range `0 – 0.4s` (not `0 – 0.8s`).
4. **Track per-endpoint status-code tallies** — new vs Congress collector:
   ```python
   tally = {ep: {"200": 0, "404": 0, "403": 0, "429": 0, "other": 0} for ep in ENDPOINT_ORDER}
   # ... inside loop, after each request:
   code = resp.status_code
   bucket = str(code) if code in (200, 404, 403, 429) else "other"
   tally[endpoint_name][bucket] += 1
   ```

**Backoff + 429 handling** — copy verbatim from `collect_trace.py:137-143` and `160-166`; it's the approved pattern.

**D-09 hook** — 5-consecutive-429s abort (`PITFALLS.md` C2/C3): add a counter that bails out of an endpoint if 429s dominate, so the pilot surfaces gated endpoints without retry spam. Implement as a per-endpoint `skip` set.

**CSV write pattern** (copy `collect_trace.py:110-113, 151-152`): write `timestamp,key,size` rows for 200s AND flush after each write. Output path default `traces/court_pilot.csv`.

**Summary emission** (new, per specifics block): at end of `collect_trace`, print per-endpoint tally:
```python
print("\n=== Pilot Summary ===")
for ep in ENDPOINT_ORDER:
    t = tally[ep]
    total = sum(t.values())
    success_rate = t["200"] / total if total else 0
    gate = "PASS" if success_rate >= 0.70 else "FAIL"
    print(f"  {ep:10s}: 200={t['200']:3d} 404={t['404']:3d} "
          f"403={t['403']:3d} 429={t['429']:3d} other={t['other']:3d} "
          f"total={total:3d} success={success_rate:.1%} [{gate}]")
```

**argparse + main guard** — mirror `collect_trace.py:177-201`. Add `--requests` default 200, `--base-delay` default 0.8.

---

### `.env.example` (NEW — config doc)

**No in-repo analog** (file does not exist). Create as a documentation stub that mirrors the `os.environ.get` requirements from both collectors:
```bash
# Copy to .env and fill in. .env is gitignored (see .gitignore:45).
# Required by scripts/collect_trace.py
CONGRESS_API_KEY=

# Required by scripts/pilot_court_trace.py and (Phase 3) scripts/collect_court_trace.py
COURTLISTENER_API_KEY=
```

**Style** (from `CONVENTIONS.md:261-264` + `collect_trace.py:50`): no shell quoting, one var per line, comment explaining which script reads it. No export prefix — the format is pure `KEY=VALUE` that users `source` or load with a `.env` reader.

**Claim in README or PROCESS.md** optional — not in scope for Phase 1 per CONTEXT.md (README update is deferred to later milestone per `STACK.md:225`).

---

### `include/cache.h` (LIKELY NO-OP)

**Assessment:** Throughput is measured outside `CachePolicy` (in `main.cpp` around `run_simulation`), not inside it, per D-02. `CacheStats` at `include/cache.h:17-31` doesn't need a new field — the timing lives in `main.cpp` locals.

**If the planner decides to add a stats hook** (an alternative D-02 implementation where `run_simulation` returns an `accesses_per_sec`), the analog pattern is `CacheStats::miss_ratio()` at line 23:
```cpp
double miss_ratio() const {
    uint64_t total = hits + misses;
    return total == 0 ? 0.0 : (double)misses / total;
}
```
— POD aggregate with no trailing-underscore (per `CONVENTIONS.md:39-43`: "public POD fields use no underscore").

**Recommendation for the planner:** keep throughput in `main.cpp` locals; skip `cache.h` edits entirely. Lower churn, same data in the CSV.

---

### `Makefile` (LIKELY NO-OP)

**Assessment:** `Makefile:2` uses `-MMD -MP` flags which auto-generate dependency files. New header `include/hash_util.h` included by `src/shards.cpp` (and eventually `src/main.cpp` for the self-test) gets picked up automatically. `Makefile:26` `-include $(OBJECTS:.o=.d)` pulls those in.

**No explicit edit needed.** If `make clean && make` builds cleanly with the new header, done. The planner should verify but should not preemptively edit.

---

### `results/*.csv` → `results/congress/` (DATA MIGRATION, NOT CODE)

**Operation** (per D-04):
```bash
mkdir -p results/congress results/court results/shards_large results/compare
git mv results/mrc.csv results/congress/mrc.csv
git mv results/alpha_sensitivity.csv results/congress/alpha_sensitivity.csv
git mv results/one_hit_wonder.csv results/congress/one_hit_wonder.csv
git mv results/shards_mrc.csv results/congress/shards_mrc.csv
```
Empty stub dirs for `{court,shards_large,compare}/` can be created with `.gitkeep` placeholders; but since `results/` is gitignored (`.gitignore:31`), even `.gitkeep` files won't persist without an ignore-rule exception. Easiest: create empty subdirs locally, document in plan that Phase 3 writers will `mkdir -p` their own target dir when they first write.

**Callers that use `--output-dir`** (per D-06): no semantics change — callers already pass the target. Example Makefile call patterns to update:
- `Makefile:32-34` (`run` target) — `./$(TARGET)` with no args currently writes to `results/` literal (default). Update to `./$(TARGET) --output-dir results/congress`.
- `Makefile:37-39` (`run-sweep`) — same.

---

## Shared Patterns

### Header guard + includes

**Source:** `include/trace_gen.h:1-8`, `include/cache.h:1-9`
**Apply to:** `include/hash_util.h`

```cpp
#pragma once
#include <cstdint>
#include <string>
// (project headers last, per src/main.cpp:1-14 ordering)
```

### Error handling (C++)

**Source:** `src/trace_gen.cpp:33-37`
**Apply to:** `include/hash_util.h` self-test, `src/main.cpp` startup gate

```cpp
if (!file.is_open()) {
    std::cerr << "Error: cannot open " << filename << "\n";
    return trace;  // empty sentinel
}
```

Callers check sentinel + `return 1` from main (`src/main.cpp:135-138`).

### Error handling (Python HTTP)

**Source:** `scripts/collect_trace.py:133-166`
**Apply to:** `scripts/pilot_court_trace.py`

Exponential backoff formula is load-bearing:
```python
backoff = min(base_delay * (2 ** consecutive_failures), 300)
```
Reset `consecutive_failures = 0` on success; catch `requests.RequestException` not bare `except`.

### Deterministic-seed reproducibility

**Source:** `include/trace_gen.h:17,26,34` (all default `seed = 42`); `scripts/collect_trace.py:185-186`
**Apply to:** `include/trace_gen.h` new `prepare_objects` / `generate_replay_trace` signatures; `scripts/pilot_court_trace.py` argparse.

Default `seed = 42`. Honor seed end-to-end (shuffle RNG uses `seed`, Zipf RNG uses `seed + 1` — preserve this split from `trace_gen.cpp:94, 97`).

### CSV-as-contract

**Source:** `src/main.cpp:169, 207, 267` (C++ writer headers) + `scripts/plot_results.py:55, 126, 181` (`pd.read_csv` + column name usage)
**Apply to:** all 3 CSVs that get `accesses_per_sec` added

Rules (from `CONVENTIONS.md:371-376`):
- C++ writes headers literally with `<<` — no library.
- Python reads with `pd.read_csv` — never `csv` module.
- Schema changes require coordinated update: C++ writer header AND Python reader column references.
- Column names `snake_case`.

### `std::chrono::steady_clock` timing block

**Source:** `src/main.cpp:271-274`
**Apply to:** the three simulation-timing sites in `src/main.cpp` (MRC loop, alpha-sweep loop, SHARDS loop).

Always `steady_clock` (not `system_clock`); always `std::chrono::duration<double>(...).count()` for seconds as double. Pattern is locked.

### Section banners

**Source:** `include/cache.h:49, 86, 121, 200, 336`, `src/shards.cpp:8, 36, 75`
**Apply to:** `include/hash_util.h`, new blocks in `src/main.cpp`, new functions in `src/trace_gen.cpp`.

Format: `// ==================== Name ====================` (20 equals signs each side — count from `cache.h:49`).

### Python argparse + main guard

**Source:** `scripts/collect_trace.py:177-201`, `scripts/plot_results.py:296-319`
**Apply to:** `scripts/pilot_court_trace.py`, updated `scripts/plot_results.py`

`def main(): ... parser = argparse.ArgumentParser(...) ... if __name__ == "__main__": main()`. Every flag has a default; help text describes the default.

---

## Patterns to AVOID

1. **Do NOT use `std::hash<std::string>` for the new FNV machinery (D-14).** It's explicitly banned for determinism reasons. The `STACK.md:48-49` note about "Caffeine uses a single 64-bit hash split into multiple hash values" is a Phase-2 CMS technique; for Phase 1, just extract FNV-1a.

2. **Do NOT generalize `collect_trace.py` into a polymorphic `--source congress|court` collector.** `code_context` explicitly says "copy-modify pattern, don't generalize" (CONTEXT.md line 93 and STACK.md's "Separate `scripts/collect_court_trace.py`, NOT generalized"). Two files is intentional — the pilot is throwaway verification code and the production collector follows in Phase 3.

3. **Do NOT regenerate object sizes per access.** `generate_replay_trace` must read `objects[rank].second` as the size (pattern from `src/trace_gen.cpp:102-103`), never call a size distribution per request. Sizes are fixed per key at `prepare_objects` time.

4. **Do NOT drop the trailing-underscore convention.** Any private member added to a C++ class (none expected in this phase, but `hash_util.h` must not add public POD with `_` and private members without it). Rule: public POD bare names (`hits`, `timestamp`); private state trailing-underscore (`hand_`, `buffer_`).

5. **Do NOT macro-ize FNV constants.** `CONVENTIONS.md:50` — "No `#define` constants in the code." Use `constexpr uint64_t` at namespace scope in `hash_util.h`.

6. **Do NOT touch `CachePolicy` or `CacheStats` to add throughput.** Timing belongs in `main.cpp` locals (D-02). Adding a field to `CacheStats` would bleed timing concerns into every policy and break the "POD aggregate" shape.

7. **Do NOT retry gated endpoints indefinitely (D-09).** If CourtListener returns 403 across the random ID range for an endpoint, drop it from the mix and record in the pilot report; don't burn quota retrying.

8. **Do NOT change the existing `replay_zipf` signature.** D-11 mandates backwards-compat as a thin wrapper; downstream code (`src/main.cpp:139, 221`) must keep compiling without edits to those call sites (the alpha-sweep site gets rewired by choice, but the hot path at line 139 must still work).

9. **Do NOT commit `.env`** (already gitignored). `.env.example` is the committable sibling; per `CONVENTIONS.md` the secrets pattern is `.env` for real values, `.env.example` as committed template.

10. **Do NOT add dependencies to `requirements.txt` or `Makefile`.** `STACK.md:56-77` is explicit: no new Python or C++ deps for Phase 1.

---

## No Analog Found

| File | Role | Reason | Fallback |
|------|------|--------|----------|
| `.env.example` | config template | Repo has never checked in an env example | Use a pure KEY=VALUE form with comments citing which script reads each var (see section above) |

Everything else has a strong in-repo analog.

---

## Metadata

**Analog search scope:** `include/`, `src/`, `scripts/`, `Makefile`, `.gitignore`
**Files read:** 10 code files + 4 planning docs
**Pattern extraction date:** 2026-04-18
**Phase:** 01-enabling-refactors-courtlistener-pilot
