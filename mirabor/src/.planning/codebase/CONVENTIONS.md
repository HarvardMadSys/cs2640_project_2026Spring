# Coding Conventions

**Analysis Date:** 2026-04-16

## Overview

This is a CS 2640 academic project implementing a C++17 cache eviction policy simulator with Python scripts for trace collection and plotting. The codebase has two distinct style regimes:

- **C++ core** (`include/*.h`, `src/*.cpp`): Pragmatic modern C++17 with a slight header-only bias for classes and an underscore-suffix member convention.
- **Python scripts** (`scripts/*.py`): PEP 8–adherent with module docstrings, function-level docstrings where helpful, and pragmatic (not pervasive) type hints.

No linter or formatter config files exist at the repo root (no `.clang-format`, no `.clang-tidy`, no `pyproject.toml`, no `.flake8`, no `.black`, no `.ruff.toml`). Style is enforced by convention and code review, not tooling.

---

## C++ Conventions

### Naming Patterns

**Files:**
- Headers: `lower_snake_case.h` in `include/` (e.g., `cache.h`, `trace_gen.h`, `workload_stats.h`, `shards.h`)
- Implementation: `lower_snake_case.cpp` in `src/` (e.g., `main.cpp`, `trace_gen.cpp`, `shards.cpp`, `workload_stats.cpp`)
- Header/source pair: 1:1 name matching — `shards.h` pairs with `src/shards.cpp`

**Types (classes, structs):**
- `UpperCamelCase` — e.g., `CachePolicy`, `LRUCache`, `FIFOCache`, `CLOCKCache`, `S3FIFOCache`, `SIEVECache`, `TraceEntry`, `CacheStats`, `WorkloadStats`, `MRCPoint`, `ZipfGenerator`, `SHARDS` (acronym preserved as all-caps)
- Nested structs use `UpperCamelCase` too: `SmallEntry`, `MainEntry`, `Node`, `Entry` in `include/cache.h`

**Functions and methods:**
- `lower_snake_case` — e.g., `access()`, `miss_ratio()`, `byte_miss_ratio()`, `evict_one()`, `evict_small()`, `evict_main()`, `stack_distance_histogram()`, `build_mrc()`, `hash_key()`, `generate_zipf_trace()`, `load_trace()`, `estimate_zipf_alpha()`, `one_hit_wonder_ratio()`, `characterize()`, `exact_stack_distances()`, `mrc_from_stack_distances()`, `working_set_bytes()`, `make_policy()`, `run_simulation()`, `print_usage()`, `split()`
- No `camelCase` method names anywhere in the C++ code

**Member variables (instance state):**
- `lower_snake_case_` with **trailing underscore** — this is the project's key convention
- Examples from `include/cache.h`: `capacity_`, `current_size_`, `order_`, `map_`, `queue_`, `hand_`, `count_`, `buffer_`, `total_capacity_`, `small_capacity_`, `main_capacity_`, `small_queue_`, `small_map_`, `small_size_`, `main_queue_`, `main_map_`, `main_size_`, `ghost_queue_`, `ghost_set_`, `hand_valid_`
- Examples from `include/shards.h`: `rate_`, `threshold_`, `modulus_`, `last_access_`, `access_times_`, `logical_time_`, `sd_hist_`, `total_sampled_`
- Examples from `include/trace_gen.h`: `cdf_`, `rng_`, `dist_`

**Exception — public plain-data fields:**
- `CacheStats` (`include/cache.h:17-31`) uses bare names: `hits`, `misses`, `byte_hits`, `byte_misses` — no trailing underscore because it is a POD-style aggregate with public data members
- `WorkloadStats` (`include/workload_stats.h:7-15`) uses the same convention: `zipf_alpha`, `one_hit_wonder_ratio`, `unique_objects`, `total_requests`, `mean_size`, `median_size`, `p99_size`
- `TraceEntry` (`include/cache.h:11-15`): `timestamp`, `key`, `size`
- **Rule of thumb:** public POD fields use no underscore; private class state uses a trailing underscore

**Local variables and parameters:**
- `lower_snake_case` with no underscore suffix — e.g., `capacity`, `cache_bytes`, `num_requests`, `alpha`, `seed`, `trace_file`, `policy_names`
- Single-letter loop and math variables are acceptable: `i`, `j`, `k`, `r`, `a`, `h`, `n`, `sum`, `cumulative` (`src/trace_gen.cpp:11-20`)

**Constants and macros:**
- No `#define` constants in the code — magic numbers are inline with comments
- Numeric literals use digit separators for readability: `10'000'000` (`src/trace_gen.cpp:63`)
- Hash seeds are literal constants: `14695981039346656037ULL`, `1099511628211ULL` (`src/shards.cpp:87,90`)

### Header vs. Source Split

**Header-only (in `include/cache.h`):**
- All five cache policy implementations — `CachePolicy`, `LRUCache`, `FIFOCache`, `CLOCKCache`, `S3FIFOCache`, `SIEVECache` — are defined entirely in the header
- Justified in `PROCESS.md` Phase 2: "the cache policies stayed in `include/cache.h` as a header-only implementation since they're templated on nothing and small enough to inline"
- POD aggregates (`TraceEntry`, `CacheStats`, `MRCPoint`, `WorkloadStats`) are also header-only

**Split .h / .cpp (when non-trivial or large):**
- `SHARDS` — declared in `include/shards.h`, defined in `src/shards.cpp`
- `ZipfGenerator` — declared in `include/trace_gen.h`, defined in `src/trace_gen.cpp`
- Free functions with non-trivial bodies (`estimate_zipf_alpha`, `characterize`, `load_trace`, `generate_zipf_trace`, `replay_zipf`) go in `.cpp`

**When to keep in header vs. split:**
- Keep in header: short methods (one screenful), pure data carriers, templates (none here)
- Split to .cpp: anything with large loops, helper static functions (e.g., `generalized_harmonic`, `generalized_harmonic_deriv` in `src/workload_stats.cpp:23-44`), or algorithms dense enough to warrant separation from interface

**Header guards:**
- Always `#pragma once` (not `#ifndef` include guards) — see `include/cache.h:1`, `include/shards.h:1`, `include/trace_gen.h:1`, `include/workload_stats.h:1`

### Code Style

**Language standard:**
- C++17 — set in `Makefile:2` via `-std=c++17`

**Compiler flags (from `Makefile:2`):**
- `-O2` (optimize), `-Wall -Wextra` (warnings), `-Iinclude` (headers), `-MMD -MP` (auto dep tracking)
- No `-Werror` — warnings permitted but discouraged

**Indentation:**
- 4 spaces, no tabs
- Opening brace on same line (K&R style): `class LRUCache : public CachePolicy {`

**Braces:**
- Always braced for multi-line bodies
- Single-statement `if`/`while` can omit braces when on one line: `if (hand_ >= buffer_.size()) hand_ = 0;` (`include/cache.h:178`)
- Single-statement `return` allowed without braces: `if (!token.empty()) tokens.push_back(token);` (`src/main.cpp:40`)

**Pointer/reference placement:**
- `type& name` and `const std::string& key` — the `&` / `*` binds to the type, not the name
- Example: `bool access(const std::string& key, uint64_t size) override;` (`include/cache.h:37`)

**Integer types:**
- Prefer fixed-width types from `<cstdint>`: `uint64_t` (dominant), `uint8_t` for bit-packed fields like frequency counters (`include/cache.h:214, 224`)
- `size_t` used for indices into STL containers: `size_t hand_ = 0; size_t count_ = 0;` (`include/cache.h:134-135`)
- `int` used for loop counters in numeric code: `int n`, `int k`, `int iter` (`src/workload_stats.cpp`)
- `double` for all floating-point math (no `float`)

**Casts:**
- C-style casts preferred in performance-sensitive numeric code: `(double)misses / total` (`include/cache.h:25`), `(uint64_t)(total_bytes * frac)` (`src/main.cpp:182`)
- No `static_cast<>` / `reinterpret_cast<>` used anywhere in the codebase

### Import / Include Organization

**Order in implementation files (observed in `src/main.cpp:1-14`):**
1. C++ standard library headers: `<iostream>`, `<fstream>`, `<sstream>`, `<vector>`, `<memory>`, `<iomanip>`, `<unordered_map>`, `<chrono>`, `<cstring>`
2. Blank line
3. Project headers in logical dependency order: `"cache.h"`, `"trace_gen.h"`, `"workload_stats.h"`, `"shards.h"`

**Order in headers (observed in `include/cache.h:1-9`):**
1. `#pragma once`
2. Standard library headers (`<cstdint>`, `<string>`, `<unordered_map>`, `<list>`, `<vector>`, `<algorithm>`, `<unordered_set>`, `<deque>`)
3. Project headers last (e.g., `"cache.h"` in `include/shards.h:8`)

**Namespaces:**
- No `using namespace std;` anywhere — always fully qualified (`std::string`, `std::vector<TraceEntry>`, `std::unique_ptr`, `std::make_unique`, etc.)
- The codebase does not define its own namespace; everything is in the global namespace

### Polymorphism and Resource Management

**Inheritance:**
- Abstract base class pattern: `CachePolicy` in `include/cache.h:34-47` defines pure virtual `access()`, `name()`, `reset()` plus a shared `CacheStats stats` field and a concrete `record(bool hit, uint64_t size)` helper
- Always `virtual ~Destructor() = default;` in polymorphic bases
- `override` keyword always used on derived virtual methods — e.g., `bool access(...) override;` (`include/cache.h:59, 82, 96, ...`)

**Ownership:**
- Polymorphic policies owned via `std::unique_ptr<CachePolicy>`
- Factory function pattern: `make_policy(name, capacity)` returns `std::unique_ptr<CachePolicy>` (`src/main.cpp:59-66`)
- Construction: `std::make_unique<LRUCache>(capacity)` — never raw `new`

**Values preferred over pointers** where ownership is clear — traces are passed as `const std::vector<TraceEntry>&`, never as `TraceEntry*` + length.

### Error Handling

**Strategy: return codes and early-exit, no exceptions.**

The C++ code does not use `throw` / `try` / `catch` anywhere. Error reporting follows a C-style pattern:

- **Print to `std::cerr` and return a sentinel** — e.g., `load_trace` returns an empty vector when the file cannot be opened:
  ```cpp
  if (!file.is_open()) {
      std::cerr << "Error: cannot open " << filename << "\n";
      return trace;  // empty
  }
  ```
  (`src/trace_gen.cpp:33-37`)
- **Callers check the sentinel and exit** — `src/main.cpp:135-138`:
  ```cpp
  if (raw_trace.empty()) {
      std::cerr << "Failed to load trace.\n";
      return 1;
  }
  ```
- **CLI argument errors** print usage and return 1 from `main()` (`src/main.cpp:120-124`)
- **`nullptr` return for factory failure** — `make_policy` returns `nullptr` on unknown policy name; caller checks (`src/main.cpp:59-66`, `186-190`)
- **Divide-by-zero guards** inline — e.g., `return total == 0 ? 0.0 : (double)misses / total;` (`include/cache.h:25`)
- **Numerical stability guards** — Newton's method in `estimate_zipf_alpha` uses `if (std::abs(hess) < 1e-15) break;` and clamps `alpha = std::max(0.01, std::min(alpha, 5.0));` (`src/workload_stats.cpp:83-89`)

There is no exception safety discipline because no exceptions are thrown. Destructors are trivial (STL containers clean themselves up).

### Comments

**Section banners — heavy use:**
```cpp
// ==================== LRU ====================
// ==================== FIFO ====================
// ==================== Zipf Generator ====================
// ==================== Trace I/O ====================
// ==================== Zipf Alpha Estimation (MLE) ====================
```
(see `include/cache.h:49, 86, 121, 200, 336`; `src/trace_gen.cpp:7, 29, 75`; `src/workload_stats.cpp:9, 97, 114`; `src/shards.cpp:8, 36, 75`)

Use these to separate major logical regions inside a file, typically with 20 equals signs on each side. Titles describe the algorithm or component, not the syntactic unit.

**Algorithm comments reference the paper:**
- S3-FIFO section cites Yang et al. (SOSP '23) with a multi-line description (`include/cache.h:200-205`)
- Zipf alpha MLE references Clauset et al. (2009) with the log-likelihood formula reproduced in comments (`src/workload_stats.cpp:10-22`)
- SHARDS header explains the sampling condition algebraically (`include/shards.h:26-30`)

**Inline explanatory comments:**
- End-of-line comments used sparingly for fields: `uint64_t size; // object size in bytes` (`include/cache.h:14`)
- Complexity annotations where relevant: `// O(n^2) — only for small traces` (`include/shards.h:17`), `// O(n * unique_keys) — only use on small traces for validation` (`src/shards.cpp:9-10`)

**No JSDoc/Doxygen-style comments** (no `@param`, `@return`, `///`, `/** */` blocks). All documentation is plain `//`.

### Function Design

**Size:**
- Small methods inline in class body (5-20 lines) — see any of the policy `access()` methods
- Larger algorithmic functions (`estimate_zipf_alpha`, `generate_zipf_trace`, `main`) are in `.cpp` files and range 30-250 lines
- `main()` in `src/main.cpp` is 267 lines with section-banner structure

**Parameters:**
- Pass large inputs by `const` reference: `const std::vector<TraceEntry>& trace`, `const std::string& key`
- Pass small inputs by value: `uint64_t size`, `double alpha`, `uint64_t seed = 42`
- Default arguments used in headers for optional parameters: `uint64_t seed = 42`, `uint64_t num_points = 100` (`include/trace_gen.h:17`, `include/shards.h:24, 62`)

**Return values:**
- Return vectors / maps by value (relying on NRVO / move): `std::vector<TraceEntry> load_trace(...)` (`include/trace_gen.h:22`)
- Small inline getters return by value: `double sampling_rate() const { return rate_; }` (`include/shards.h:64`)
- Histograms returned by value as `std::map<uint64_t, uint64_t>` — intentional (ordered by distance for sweep)

**Helper scoping:**
- File-private helpers use `static` at file scope — e.g., `static double generalized_harmonic(int n, double alpha)` in `src/workload_stats.cpp:23`, `static void print_usage(...)`, `static std::vector<std::string> split(...)`, `static uint64_t working_set_bytes(...)`, `static std::unique_ptr<CachePolicy> make_policy(...)`, `static void run_simulation(...)` in `src/main.cpp:18, 35, 46, 59, 68`

### Logging and Output

- Plain `std::cout` for status/results, `std::cerr` for errors and usage
- No logging framework
- Progress output uses `std::setw`, `std::setprecision`, `std::fixed` for aligned tabular output (`src/main.cpp:155-196`)
- CSV output is written directly via `<<` to an `std::ofstream`; no CSV library

---

## Python Conventions

### Naming Patterns

**Files:**
- `lower_snake_case.py` — `scripts/collect_trace.py`, `scripts/plot_results.py`
- All Python files start with `#!/usr/bin/env python3` shebang

**Functions:**
- `lower_snake_case` — `get_api_key()`, `pick_congress()`, `generate_bill_request()`, `generate_amendment_request()`, `generate_vote_request()`, `generate_request()`, `collect_trace()`, `main()`, `plot_mrc()`, `plot_byte_mrc()`, `plot_alpha_sensitivity()`, `plot_ohw()`, `plot_shards()`, `plot_shards_error()`, `plot_workload()`

**Variables:**
- `lower_snake_case` — `api_key`, `num_requests`, `consecutive_failures`, `backoff`, `base_delay`, `max_duration`, `output_path`, `trace_path`, `figures_dir`

**Module-level constants:**
- `UPPER_SNAKE_CASE` — `BASE_URL`, `ENDPOINTS`, `CURRENT_CONGRESS`, `CURRENT_CONGRESS_WEIGHT` in `scripts/collect_trace.py:24-47`
- `POLICY_COLORS`, `POLICY_MARKERS` in `scripts/plot_results.py:31-45`

### PEP 8 Adherence

**Indentation:**
- 4 spaces, no tabs

**Line length:**
- Soft limit around 100 columns; occasional lines slightly longer (e.g., `scripts/collect_trace.py:108` wraps argparse help text)
- Multi-argument calls broken across lines with hanging indent:
  ```python
  parser.add_argument("--requests", type=int, default=5000,
                      help="Number of requests to make (default: 5000)")
  ```
  (`scripts/collect_trace.py:179-180`)

**Imports:**
- Order (observed in `scripts/collect_trace.py:15-22` and `scripts/plot_results.py:9-17`):
  1. Standard library (`argparse`, `csv`, `os`, `random`, `sys`, `time`)
  2. Blank line
  3. Third-party (`requests`, `matplotlib`, `numpy`, `pandas`)
- `import matplotlib` then `matplotlib.use("Agg")` BEFORE `import matplotlib.pyplot as plt` — non-interactive backend enforced (`scripts/plot_results.py:13-15`)

**Spacing:**
- One blank line between functions
- Two blank lines between top-level definitions
- Space around operators: `r < 0.6`, `args.requests * 1000`

**Quotes:**
- Double quotes predominant (`"timestamp"`, `"--output"`, `"POLICY_COLORS"`)
- Single quotes for nothing systematic; the codebase is consistently double-quoted

### Type Hints

**Sparse, not pervasive.** The Python scripts do **not** use type hints on function signatures. Example:

```python
def collect_trace(api_key, num_requests, max_duration, output_path, append=False):
```
(`scripts/collect_trace.py:103`)

```python
def plot_mrc(results_dir, figures_dir):
```
(`scripts/plot_results.py:48`)

Argparse types (`type=int`) are the only explicit typing. This is acceptable for academic scripts; if the codebase grows, add type hints to public function signatures.

### Docstrings

**Module-level:** Every script has a triple-quoted module docstring explaining purpose and usage (`scripts/collect_trace.py:2-13`, `scripts/plot_results.py:2-7`).

**Function-level:** One-line docstrings for plot functions:
```python
def plot_mrc(results_dir, figures_dir):
    """Miss ratio vs. cache size (all 5 policies)."""
```
(`scripts/plot_results.py:48-49`)

Shorter helpers without docstrings are acceptable (`pick_congress`, `generate_bill_request`).

No Google-style, NumPy-style, or reST-style docstrings — just plain English one-liners.

### Error Handling

**Strategy: `try/except` with explicit exception types, exponential backoff for network I/O.**

Pattern in `scripts/collect_trace.py:133-166`:
```python
try:
    resp = session.get(url, timeout=30)
    # Back off on 429 (rate limit) or 5xx
    if resp.status_code == 429 or resp.status_code >= 500:
        consecutive_failures += 1
        backoff = min(base_delay * (2 ** consecutive_failures), 300)
        print(f"  HTTP {resp.status_code}, backing off {backoff:.0f}s ...")
        time.sleep(backoff)
        continue
    ...
except requests.RequestException as e:
    consecutive_failures += 1
    backoff = min(base_delay * (2 ** consecutive_failures), 300)
    print(f"  Request failed ({consecutive_failures}x): {e}")
    time.sleep(backoff)
    continue
```

**Key patterns:**
- **Catch specific exceptions** — `except requests.RequestException as e:` not bare `except:`
- **Exponential backoff** — `min(base_delay * (2 ** consecutive_failures), 300)` with a cap
- **Separate HTTP-status retries from transport errors** — 429/5xx are retried with backoff, 4xx other than 429 are silently skipped
- **Data durability** — `f.flush()` after every successful row (`scripts/collect_trace.py:152`)
- **Reset failure counter on success** — `consecutive_failures = 0`

**Environment validation:**
- Required env vars checked at startup with `sys.exit(1)`:
  ```python
  def get_api_key():
      key = os.environ.get("CONGRESS_API_KEY")
      if not key:
          print("Error: set CONGRESS_API_KEY environment variable", file=sys.stderr)
          sys.exit(1)
      return key
  ```
  (`scripts/collect_trace.py:50-55`)

**File existence checks** in plotting scripts — skip gracefully rather than crashing:
```python
if not os.path.exists(path):
    print(f"  Skipping MRC plot: {path} not found")
    return
```
(`scripts/plot_results.py:51-53`, and throughout `plot_results.py`)

### CLI Argument Handling

Both scripts use `argparse` with a `main()` entry point and `if __name__ == "__main__": main()` guard:
- `scripts/collect_trace.py:177-201`
- `scripts/plot_results.py:296-319`

Convention: defaults provided for every argument; help strings describe the default explicitly.

### Progress Output

- `print()` with f-strings and fixed-width formatting for console progress
- No `logging` module — progress messages go straight to stdout
- Progress checkpoints every N items: `if collected % 100 == 0:` (`scripts/collect_trace.py:156`)

---

## Cross-Cutting Conventions

### Reproducibility

- **Deterministic random seeds:** Default `seed = 42` in C++ (`ZipfGenerator`, `generate_zipf_trace`, `replay_zipf` in `include/trace_gen.h`) and Python (`scripts/collect_trace.py:185-186`: `random.seed(args.seed)`)
- **Config via CLI flags**, not environment variables (except for secrets like `CONGRESS_API_KEY`)
- **No hidden state:** all simulation parameters come from argv; results are written to CSVs with a schema header row

### CSV I/O

- **Headers always written:** `csv << "cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio\n";` (`src/main.cpp:169`)
- **Python reads with pandas** (`pd.read_csv(path)`) — never the `csv` module for reading
- **Python writes with `csv.writer`** (`scripts/collect_trace.py:111`) — simpler and streams to disk for long-running collection
- **No extra whitespace, no quoting unless required**

### File Layout

```
civicache/
├── include/               # All C++ headers
│   ├── cache.h            # Header-only cache policies (5 classes)
│   ├── shards.h           # SHARDS class + free funcs declarations
│   ├── trace_gen.h        # ZipfGenerator + I/O function declarations
│   └── workload_stats.h   # WorkloadStats + analysis function declarations
├── src/                   # All C++ implementations (1 per header)
│   ├── main.cpp           # CLI driver
│   ├── shards.cpp
│   ├── trace_gen.cpp
│   └── workload_stats.cpp
├── scripts/               # Python tooling
│   ├── collect_trace.py   # Congress.gov API trace collector
│   └── plot_results.py    # matplotlib figure generation
├── traces/                # Gitignored — real collected traces
├── results/               # Gitignored — CSV outputs and PDF figures
├── build/                 # Gitignored — object files
├── Makefile               # Build system
├── requirements.txt       # Python deps (requests, matplotlib, numpy, pandas, scipy)
├── README.md
└── PROCESS.md             # Development log (gitignored as local working doc)
```

### Module Design

**One logical concern per header/source pair.** The project strictly pairs each `.cpp` with a `.h` of the same name, and the header declares exactly the public API (classes, free functions, structs) that the `.cpp` implements.

**No `using` declarations** at the namespace level — always fully qualified `std::` names. This matches the academic/systems-paper style the project targets.

**No barrel headers** — callers include the specific headers they need (`#include "cache.h"`, `#include "shards.h"`), not a single umbrella `include/all.h`.

---

## Conventions for Adding New Code

**New cache policy:** Add a new class to `include/cache.h` inheriting from `CachePolicy`. Follow the existing section-banner style (`// ==================== MYPOLICY ====================`). Register it in `make_policy()` in `src/main.cpp:59-66`. Keep member variables with a trailing underscore. Add the lowercase shortname to the `--policies` help text and default list.

**New analysis routine:**
- If small (≤40 lines) and purely combinatorial on POD structs, add it to `include/cache.h` or the relevant header as a free function.
- If non-trivial or uses multiple standard-library algorithms, declare in `include/workload_stats.h` or a new header, implement in the matching `.cpp`.

**New CLI flag:** Add to the `for (int i = 1; i < argc; i++)` loop in `src/main.cpp:92-125`, update `print_usage()` in `src/main.cpp:18-33`, and document in `README.md`.

**New Python script:** Place in `scripts/`. Start with shebang and module docstring. Use `argparse` in a `main()` function guarded by `if __name__ == "__main__":`. Follow the existing import-order convention.

---

*Convention analysis: 2026-04-16*
