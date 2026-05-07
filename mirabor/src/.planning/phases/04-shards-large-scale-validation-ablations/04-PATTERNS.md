# Phase 4: SHARDS Large-Scale Validation & Ablations — Pattern Map

**Mapped:** 2026-04-20
**Files analyzed:** 22 (across 4 work-axes A/B/C/D)
**Analogs found:** 22 / 22 (100% — Phase 4 is pure extension of existing shapes)

> **Planner note:** Every file in Phase 4 has a strong in-repo analog. There is no "RESEARCH.md patterns" fallback needed. The ctor-default + factory-branch + make-target pattern from Phase 2 is reused four times. When in doubt, grep the analog and copy.

## File Classification

### Axis A — SHARDS large-scale (file list per CONTEXT.md §Phase Boundary items 1, 2, 8, 10, 11, §Integration Points)

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| `scripts/generate_shards_trace.py` *(NEW, if Python chosen)* | script | batch / file-I/O | `scripts/collect_trace.py` (Python CSV writer) + `src/trace_gen.cpp::generate_zipf_trace` (C++ reference) | role-match |
| `src/main.cpp` — `--shards-rates` CLI flag **and** optional `--limit N` flag (D-03) **and** optional `--emit-trace <path>` flag (D-15 alt) | config-parse extension | request-response | `src/main.cpp:100-102` (`--cache-sizes` comma-split pattern) | exact |
| `src/main.cpp` — SHARDS rate-grid at line 304; split into self-convergence CSV writer + 50K oracle regime (D-02/D-03) | orchestrator extension | batch transform | `src/main.cpp:302-384` (existing SHARDS block) | exact |
| `results/shards_large/shards_convergence.csv` *(NEW schema)* | CSV output | batch | `results/.../shards_error.csv` schema @ `src/main.cpp:352-353,377` | role-match |
| `results/shards_large/{shards_mrc.csv, shards_error.csv, figures/}` | CSV output | batch | existing same-named files produced at `src/main.cpp:308-310,342-343,352-353` | exact |
| `Makefile` — `shards-large` target *(NEW)* | build target | batch | `Makefile:40-57` (`run`, `run-sweep`) | role-match |
| `scripts/plot_results.py::plot_shards_convergence` *(NEW)* | plot function | batch transform | `plot_shards_error` @ `plot_results.py:289-314` | exact |
| `scripts/plot_results.py::plot_shards_mrc_overlay` *(NEW)* | plot function | batch transform | `plot_shards` @ `plot_results.py:193-234` (already does overlay) | exact |

### Axis B — Doorkeeper ablation (items 3, 4, 5, 7, 9, 10, 12)

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| `include/doorkeeper.h` *(NEW header-only)* | header (data structure) | request-response | `include/count_min_sketch.h` (header-only, uses `hash_util.h`, has `reset()`) | exact |
| `include/count_min_sketch.h` — `on_age_cb_` hook (D-09) | header extension | event-driven (callback) | same file — `force_age()` hook @ lines 98-100 (one-line test-hook precedent) | exact |
| `include/wtinylfu.h` — ctor bool param + `access()` pre-CMS filter (D-05/D-08) | header extension | request-response | same file — ctor init-list @ lines 39-53 + `cms_.record(key)` @ line 60 | exact |
| `src/main.cpp::make_policy` — `wtinylfu-dk` branch | factory extension | request-response | same function — `wtinylfu` branch @ line 69 | exact |
| `src/main.cpp` policy-label map — `"W-TinyLFU+DK"` display string | label mapping | batch | lines 184-188, 234-238 | exact |
| `tests/test_doorkeeper.cpp` *(NEW)* | test | assertion | `tests/test_wtinylfu.cpp` (199 lines, TEST_ASSERT macro, failures counter, `int main()`) | exact |
| `Makefile` — `test_doorkeeper` build rule | build target | batch | `Makefile:79-96` (TEST_SRC/TEST_OBJDIR/TEST_OBJ/TEST_TARGET 4-var pattern) | exact |
| `Makefile` — `ablation-doorkeeper` target *(NEW)* | build target | batch | `Makefile:40-57` (`run`/`run-sweep`) | role-match |
| `results/{congress,court}/ablation_doorkeeper.csv` *(schema = mrc.csv + `variant` column per §specifics)* | CSV output | batch | `results/{workload}/mrc.csv` @ `src/main.cpp:179` | exact |
| `scripts/plot_results.py::plot_ablation_doorkeeper` *(2×2 workload × variant grid)* | plot function | batch transform | `plot_alpha_sensitivity` @ `plot_results.py:140-167` | role-match |
| `scripts/plot_results.py` — POLICY_COLORS/MARKERS entry for `"W-TinyLFU+DK"` | styling dict | config | dict literals @ lines 45-61 | exact |

### Axis C — S3-FIFO small-queue ratio ablation (items 6, 7, 9, 10, 11)

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| `include/cache.h` — `S3FIFOCache` ctor `small_frac` param (D-11) | header extension | request-response | same file — S3FIFOCache ctor @ lines 235-238 | exact |
| `src/main.cpp::make_policy` — `s3fifo-5/-10/-20` branches (D-11) | factory extension | request-response | same function — `s3fifo` branch @ line 67 | exact |
| `Makefile` — `ablation-s3fifo` target | build target | batch | `Makefile:40-57` | role-match |
| `results/{congress,court}/ablation_s3fifo.csv` | CSV output | batch | alpha_sensitivity.csv schema @ `src/main.cpp:229` | exact |
| `scripts/plot_results.py::plot_ablation_s3fifo` | plot function | batch transform | `plot_alpha_sensitivity` @ `plot_results.py:140-167` | exact |
| `scripts/plot_results.py` — POLICY_COLORS/MARKERS for `"S3-FIFO-5"`, `"S3-FIFO-20"` | styling dict | config | lines 45-61 | exact |

### Axis D — SIEVE visited-bit ablation (items 6, 7, 9, 10, 11)

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| `include/cache.h` — `SIEVECache` ctor `promote_on_hit` + hit-path guard (D-12) | header extension | request-response | same file — SIEVECache ctor @ line 354 + hit-path @ line 362 | exact |
| `src/main.cpp::make_policy` — `sieve-noprom` branch | factory extension | request-response | `sieve` branch @ line 68 | exact |
| `Makefile` — `ablation-sieve` target | build target | batch | `Makefile:40-57` | role-match |
| `results/{congress,court}/ablation_sieve.csv` | CSV output | batch | alpha_sensitivity.csv schema | exact |
| `scripts/plot_results.py::plot_ablation_sieve` | plot function | batch transform | `plot_alpha_sensitivity` | exact |
| `scripts/plot_results.py` — POLICY_COLORS/MARKERS for `"SIEVE-NoProm"` | styling dict | config | lines 45-61 | exact |

### Cross-axis composition

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|-------------------|------|-----------|----------------|-------|
| `Makefile` — `phase-04` composition target (optional per Claude's Discretion) | build target | batch | `Makefile:13` `.PHONY` + multi-target convenience pattern | role-match |

---

## Pattern Assignments

### Axis A — SHARDS

---

### `src/main.cpp` — `--shards-rates` CLI flag (D-18)

**Role:** config-parse extension. **Analog:** existing `--cache-sizes` flag at `src/main.cpp:100-102`.

**What to copy — argument parsing idiom** (lines 100-102, exact 3-line pattern):
```cpp
} else if (std::strcmp(argv[i], "--cache-sizes") == 0 && i + 1 < argc) {
    cache_fracs.clear();
    for (auto& s : split(argv[++i], ',')) cache_fracs.push_back(std::stod(s));
```

**What to write** (new block, insert alongside the existing flag parsers at ~line 120):
```cpp
} else if (std::strcmp(argv[i], "--shards-rates") == 0 && i + 1 < argc) {
    shards_rates.clear();
    for (auto& s : split(argv[++i], ',')) shards_rates.push_back(std::stod(s));
}
```

**Default-init site** (near line 86, alongside `cache_fracs` default):
```cpp
std::vector<double> shards_rates = {0.001, 0.01, 0.1};  // D-18: preserve Phase 1 back-compat
```

**Usage site replacement** at `src/main.cpp:304`:
- **Before:** `std::vector<double> rates = {0.001, 0.01, 0.1};`
- **After:** *(the outer `shards_rates` is now in scope; delete this line)*

**Also add** `--limit N` flag for D-03's "first 50K rows" oracle slice (similarly shaped — single `--limit` keyword + `i+1<argc` guard + `std::stoull`). Pattern literal from `--num-requests` at lines 107-109.

**Also add** `--emit-trace <path>` flag per D-15 alternative (Python vs C++ trace-gen). Copy the `--output-dir` pattern at lines 105-106 and call `generate_zipf_trace(1'000'000, 100'000, 0.8, 42)` + write `timestamp,key,size\n` CSV header and rows.

**Invariants to preserve:**
- Default values keep Phase 1 Congress sweeps bit-identical (D-18).
- `print_usage()` at lines 19-34 must gain a line for each new flag — do not skip this (users rely on `-h` for the flag taxonomy).
- Self-test call `hash_util_self_test()` at line 132 stays before any work begins.

---

### `src/main.cpp` SHARDS block — self-convergence CSV + 50K oracle regime (D-02/D-03/D-16)

**Role:** orchestrator extension. **Analog:** existing SHARDS block at lines 302-384 (entire `if (run_shards)` block).

**What to copy — full CSV-writer idiom** (lines 308-328, the `shards_mrc.csv` emitter loop):
```cpp
std::string csv_path = output_dir + "/shards_mrc.csv";
std::ofstream csv(csv_path);
csv << "sampling_rate,cache_size_objects,miss_ratio,accesses_per_sec\n";

for (double rate : rates) {
    SHARDS shards(rate);
    auto t_start = std::chrono::steady_clock::now();
    shards.process(trace);
    auto t_end = std::chrono::steady_clock::now();
    double elapsed = std::chrono::duration<double>(t_end - t_start).count();
    double accesses_per_sec = elapsed > 0 ? (double)trace.size() / elapsed : 0.0;

    auto mrc = shards.build_mrc(max_cache, 100);
    std::cout << "  Rate=" << rate * 100 << "% : sampled " << shards.total_sampled()
              << " accesses (" << std::setprecision(2) << elapsed << "s)\n";

    for (auto& pt : mrc) {
        csv << rate << "," << pt.cache_size << "," << std::setprecision(6) << pt.miss_ratio << ","
            << accesses_per_sec << "\n";
    }
}
csv.close();
```

**What to write — self-convergence CSV emitter** (new block, after `shards_mrc.csv` block, **before** `shards_exact` block):
```cpp
// D-02: self-convergence — MAE of each rate vs. 10% reference.
// Schema: reference_rate,compared_rate,mae,max_abs_error,num_points,
//         n_samples_reference,n_samples_compared
// Three rows (for rates 0.0001, 0.001, 0.01 all compared against 0.1).
std::string conv_path = output_dir + "/shards_convergence.csv";
std::ofstream conv_csv(conv_path);
conv_csv << "reference_rate,compared_rate,mae,max_abs_error,num_points,"
            "n_samples_reference,n_samples_compared\n";

// Process the reference rate (0.1) once; keep its MRC + sample count.
SHARDS ref(0.1);
ref.process(trace);
auto ref_mrc = ref.build_mrc(max_cache, 100);
uint64_t ref_samples = ref.total_sampled();

for (double rate : shards_rates) {
    if (rate == 0.1) continue;  // skip self-vs-self
    SHARDS cmp(rate);
    cmp.process(trace);
    auto cmp_mrc = cmp.build_mrc(max_cache, 100);
    size_t n = std::min(ref_mrc.size(), cmp_mrc.size());
    double sum_err = 0, max_err = 0;
    for (size_t j = 0; j < n; j++) {
        double err = std::abs(cmp_mrc[j].miss_ratio - ref_mrc[j].miss_ratio);
        sum_err += err;
        max_err = std::max(max_err, err);
    }
    double mae = n > 0 ? sum_err / n : 0;
    conv_csv << 0.1 << "," << rate << "," << mae << "," << max_err << "," << n
             << "," << ref_samples << "," << cmp.total_sampled() << "\n";
}
conv_csv.close();
```

**What to write — 50K oracle regime** (D-03): reuse the existing `shards_exact` block verbatim, but preface it with a trace-slicing step that truncates `trace` to its first 50,000 rows when `--limit 50000` is passed. Do **NOT** re-copy the `exact_stack_distances` loop — the existing block at lines 338-381 already handles it. Just guard the rate grid inside the exact block to drop 0.0001 (5 samples at 50K is pointless per D-03).

**Invariants to preserve:**
- Existing `shards_error.csv` schema (`sampling_rate,mae,max_abs_error,num_points`) is the Phase 1 contract — do NOT add columns to it; the new columns go into `shards_convergence.csv` only (D-16).
- `build_mrc(max_cache, 100)` with `max_cache = ws.unique_objects` (line 306) is the alignment contract per D-04 — do not change the grid size.
- The 50K oracle regime's exact MRC uses the sliced trace's `unique_objects`, not the full-trace value. Recompute `ws_50k = characterize(sliced_trace)` for the sliced block.

---

### `results/shards_large/shards_convergence.csv` — NEW schema

**Role:** CSV output. **Analog:** `shards_error.csv` at `src/main.cpp:352-353`:
```cpp
err_csv << "sampling_rate,mae,max_abs_error,num_points\n";
// ...
err_csv << rate << "," << mae << "," << max_err << "," << n << "\n";
```

**What to write — NEW schema** (per D-16):
```
reference_rate,compared_rate,mae,max_abs_error,num_points,n_samples_reference,n_samples_compared
0.1,0.0001,<mae>,<max_err>,100,<ref_samples>,100
0.1,0.001,<mae>,<max_err>,100,<ref_samples>,1000
0.1,0.01,<mae>,<max_err>,100,<ref_samples>,10000
```

**Invariants to preserve:**
- Snake_case column names (Phase 1 CSV-as-contract convention).
- `n_samples_compared` carries the D-01 caveat flag for 0.01% (100 samples < 200 PITFALLS M4 floor).
- Three rows only (per D-02 — the full pairwise matrix is rejected).

---

### `Makefile` — `shards-large` target

**Role:** build target. **Analog:** `Makefile:40-57` (`run` and `run-sweep` targets):
```makefile
run: $(TARGET)
	mkdir -p $(WORKLOAD_RESULTS_DIR)
	./$(TARGET) --output-dir $(WORKLOAD_RESULTS_DIR)

# ...
run-sweep: $(TARGET)
	mkdir -p $(WORKLOAD_RESULTS_DIR)
	./$(TARGET) $(SWEEP_FLAGS)
```

**What to write** (append to Makefile — per D-17 "independent of WORKLOAD=/TRACE= plumbing"):
```makefile
# ==================== Phase 4 targets (D-17) ====================
# Each target owns its invocation shape; does NOT reuse WORKLOAD=/TRACE=.

# SHARDS 1M-scale validation (D-15 trace regen + self-convergence + 50K oracle).
traces/shards_large.csv: $(TARGET)
	./$(TARGET) --emit-trace traces/shards_large.csv \
	            --num-requests 1000000 --num-objects 100000 --alpha 0.8

shards-large: $(TARGET) traces/shards_large.csv
	mkdir -p results/shards_large
	./$(TARGET) --trace traces/shards_large.csv \
	            --shards --shards-exact --limit 50000 \
	            --shards-rates 0.0001,0.001,0.01,0.1 \
	            --output-dir results/shards_large
```

**Also add — `ablation-s3fifo`, `ablation-sieve`, `ablation-doorkeeper`** (per D-14, runs on BOTH workloads):
```makefile
ablation-s3fifo: $(TARGET)
	mkdir -p results/congress results/court
	./$(TARGET) --trace traces/congress_trace.csv --replay-zipf \
	            --alpha-sweep --policies s3fifo-5,s3fifo-10,s3fifo-20 \
	            --output-dir results/congress
	mv results/congress/alpha_sensitivity.csv results/congress/ablation_s3fifo.csv
	./$(TARGET) --trace traces/court_trace.csv --replay-zipf \
	            --alpha-sweep --policies s3fifo-5,s3fifo-10,s3fifo-20 \
	            --output-dir results/court
	mv results/court/alpha_sensitivity.csv results/court/ablation_s3fifo.csv
```

*(Analogous shapes for `ablation-sieve` and `ablation-doorkeeper`. Note: the `mv` rename is because the ablation sweeps reuse the alpha-sweep path. Planner may prefer a new `--ablation-output <name>` flag if cleaner.)*

**Invariants to preserve:**
- `.PHONY` at line 13 must gain all new targets: `shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper phase-04`.
- `clean` at line 28-29 should also `rm -rf results/shards_large` — but do **NOT** add this automatically; let the user decide.

---

### `scripts/plot_results.py::plot_shards_convergence` — NEW

**Role:** plot function. **Analog:** `plot_shards_error` at `plot_results.py:289-314`.

**What to copy** (full function body, lines 289-314):
```python
def plot_shards_error(results_dir, figures_dir):
    """Bar chart of SHARDS MAE and max error by sampling rate."""
    path = os.path.join(results_dir, "shards_error.csv")
    if not os.path.exists(path):
        return  # silently skip, only exists after --shards-exact

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(5, 4))
    x = np.arange(len(df))
    width = 0.35
    labels = [f"{r*100:.1f}%" for r in df["sampling_rate"]]

    ax.bar(x - width/2, df["mae"], width, label="MAE", color="#1f77b4")
    ax.bar(x + width/2, df["max_abs_error"], width, label="Max Error", color="#d62728")
    ax.set_xlabel("Sampling Rate")
    ax.set_ylabel("Absolute Error (miss ratio)")
    ax.set_title("SHARDS Approximation Error")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()

    out = os.path.join(figures_dir, "shards_error.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")
```

**What to write — `plot_shards_convergence`** (reads `shards_convergence.csv`, line chart: MAE vs. compared-rate with the `n_samples_compared` column annotated as text on each point; if `n_samples_compared < 200`, add a caveat asterisk per D-01):
```python
def plot_shards_convergence(results_dir, figures_dir):
    """MAE of SHARDS at each rate vs. 10% reference. D-01 caveat: flag <200 samples."""
    path = os.path.join(results_dir, "shards_convergence.csv")
    if not os.path.exists(path):
        return  # only exists after make shards-large
    df = pd.read_csv(path).sort_values("compared_rate")

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df["compared_rate"] * 100, df["mae"],
            marker="o", color="#1f77b4", linewidth=1.5, markersize=6)
    for _, row in df.iterrows():
        label = f"n={int(row['n_samples_compared'])}"
        if row["n_samples_compared"] < 200:
            label += "*"  # D-01 caveat
        ax.annotate(label, (row["compared_rate"]*100, row["mae"]),
                    textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("Sampling Rate (%, log scale)")
    ax.set_ylabel("MAE vs. 10% reference")
    ax.set_title("SHARDS Self-Convergence at 1M")
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "shards_convergence.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")
```

**What to write — `plot_shards_mrc_overlay`**: already existing `plot_shards` at lines 193-234 does exactly this overlay. Either (a) reuse `plot_shards` verbatim against the `results/shards_large/` dir, or (b) copy it to a new function name if the caption/axes need to differ for the money-shot figure. Prefer (a) — it's a free win.

**Register in `main()`** — add after `plot_shards_error` call at line 341:
```python
plot_shards_convergence(results_dir, figures_dir)
```

**Invariants to preserve:**
- `matplotlib.use("Agg")` at line 28 (non-interactive — required for Makefile plots target).
- `plt.rcParams.update({...})` at lines 35-43 is the project-wide style; new functions inherit it automatically.
- `print(f"  Saved {out}")` trailing line — grep-findable success marker.
- `os.path.exists(path)` guard at top — required so missing CSVs silently skip (not error).

---

### Axis B — Doorkeeper

---

### `include/doorkeeper.h` — NEW header-only Bloom filter

**Role:** header (data structure). **Analog:** `include/count_min_sketch.h` (whole file, 143 lines).

**What to copy — imports + class skeleton** (`count_min_sketch.h` lines 1-10, 35-53):
```cpp
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>
#include <algorithm>
#include "hash_util.h"

class CountMinSketch {
public:
    static constexpr uint32_t DEPTH = 4;
    // ...
    explicit CountMinSketch(uint64_t n_objects_hint) {
        uint64_t w = n_objects_hint < 1 ? 1 : n_objects_hint;
        uint64_t p = 1;
        while (p < w) p <<= 1;
        width_ = p;
        // ...
    }
```

**What to write — `Doorkeeper` class skeleton** (D-05/D-06/D-07 literal semantics):
```cpp
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <algorithm>
#include "hash_util.h"

// Doorkeeper Bloom filter for TinyLFU pre-CMS record filtering (D-05).
// Paper: Einziger-Friedman §4.3. Sizing per STACK.md §Doorkeeper (4 bits per
// element, ≈13% FPR). Hash scheme per D-07: Kirsch-Mitzenmacher double-hashing
// with FNV-1a seeds A and B (reuses Phase 1 hash_util.h seeds).
//
// Reset cadence: synchronized with CMS aging via on_age callback registered
// by WTinyLFUCache (D-09). STATS SINGLE-SOURCE INVARIANT (L-12): Doorkeeper
// NEVER calls record() or touches stats — it is a sub-component of
// WTinyLFUCache and the outer cache is the sole stats recorder.

class Doorkeeper {
public:
    explicit Doorkeeper(uint64_t n_objects_hint) {
        // D-06: bit-array length = 4 × n_objects_hint (Einziger-Friedman).
        size_ = 4 * std::max<uint64_t>(n_objects_hint, 1);
        // Pack bits into uint64_t words; one bit per element after hashing.
        bits_.assign((size_ + 63) / 64, 0);
    }

    // D-05: returns true iff key's two bits (via Kirsch-Mitzenmacher double-hashing)
    // are both set. False negatives impossible; false positives ~13% at 4 bits/elt.
    bool contains(const std::string& key) const {
        uint64_t h1 = fnv1a_64(key, FNV_SEED_A);
        uint64_t h2 = fnv1a_64(key, FNV_SEED_B);
        for (int i = 0; i < 2; ++i) {
            uint64_t bit = (h1 + (uint64_t)i * h2) % size_;
            if (!((bits_[bit >> 6] >> (bit & 63)) & 1ULL)) return false;
        }
        return true;
    }

    void add(const std::string& key) {
        uint64_t h1 = fnv1a_64(key, FNV_SEED_A);
        uint64_t h2 = fnv1a_64(key, FNV_SEED_B);
        for (int i = 0; i < 2; ++i) {
            uint64_t bit = (h1 + (uint64_t)i * h2) % size_;
            bits_[bit >> 6] |= (1ULL << (bit & 63));
        }
    }

    void clear() {
        std::fill(bits_.begin(), bits_.end(), static_cast<uint64_t>(0));
    }

    // Test-only inspectors (mirrors CountMinSketch::width()/sample_size()).
    uint64_t size() const { return size_; }

private:
    uint64_t size_;
    std::vector<uint64_t> bits_;
};
```

**Invariants to preserve:**
- Header-only (L-1 Phase 2 locked: "no new external deps").
- Trailing-underscore member names (CONVENTIONS).
- `pragma once` (all `include/*.h` files use it — grep-verified).
- No `std::hash<std::string>` — use `fnv1a_64` from `hash_util.h` (D-14 Phase 1 bans non-deterministic hashing).
- No `#include "cache.h"` — Doorkeeper is a sub-component, not a `CachePolicy`.

---

### `include/count_min_sketch.h` — `on_age_cb_` hook (D-09)

**Role:** header extension. **Analog:** same file — the test-only `force_age()` hook at lines 98-100 is the one-line member-function precedent.

**What to copy** (lines 98-100):
```cpp
// Test-only hook (D-10). Immediately halves all counters and resets sample_count_.
// Used by tests/test_wtinylfu.cpp (02-04) to verify aging-cadence deterministically.
void force_age() { halve_all_(); }
```

**What to write — add `#include <functional>`** at the top (after line 7), then extend the class:
```cpp
// D-09 hook: fired from halve_all_() when the natural aging threshold fires.
// WTinyLFUCache registers [this]{ doorkeeper_.clear(); } only when DK is on;
// default-empty for baseline wtinylfu -> zero overhead (no function-call cost
// when the std::function is empty-constructible and the check is branch-predictable).
void set_on_age_cb(std::function<void()> cb) { on_age_cb_ = std::move(cb); }
```

**Modify `halve_all_()` at line 128** — append one line at the end (after `sample_count_ = 0;`):
```cpp
void halve_all_() {
    for (auto& row : rows_) {
        for (auto& byte : row) {
            byte = static_cast<uint8_t>((byte >> 1) & 0x77);
        }
    }
    sample_count_ = 0;
    if (on_age_cb_) on_age_cb_();  // D-09: fire DK clear (when registered)
}
```

**Add private member** (after line 111):
```cpp
std::function<void()> on_age_cb_;  // D-09
```

**Invariants to preserve:**
- `force_age()` test hook still fires `halve_all_()` → meaning the test can verify the callback fires too.
- `reset()` at line 91 does NOT fire the callback (only the natural aging cadence does — `halve_all_()` is the aging event, `reset()` is a full wipe). The test file's existing aging assertion at `tests/test_wtinylfu.cpp:90-94` still passes.
- CMS width / sample_size / conservative-update rule UNCHANGED (locked by WTLFU-01 per file header).
- `grep -cE "record\(\s*(true|false)" include/count_min_sketch.h` stays at 0 — CMS never touches `stats`.

---

### `include/wtinylfu.h` — ctor flag + `access()` pre-CMS filter (D-05/D-08/D-09)

**Role:** header extension. **Analog:** same file (228 lines — this is the single biggest in-file diff of Phase 4).

**What to copy — ctor init-list** (lines 39-53):
```cpp
WTinyLFUCache(uint64_t capacity_bytes, uint64_t n_objects_hint)
    : window_capacity_(std::max<uint64_t>(1, capacity_bytes / 100)),
      main_capacity_(capacity_bytes > window_capacity_
                         ? capacity_bytes - window_capacity_
                         : 0),
      protected_capacity_((main_capacity_ * 80) / 100),
      cms_(std::max<uint64_t>(1, n_objects_hint))
{
    (void)capacity_bytes;
}
```

**What to write — extended ctor** (D-08: default `false` preserves Phase 2 behavior exactly):
```cpp
WTinyLFUCache(uint64_t capacity_bytes, uint64_t n_objects_hint,
              bool use_doorkeeper = false)
    : window_capacity_(std::max<uint64_t>(1, capacity_bytes / 100)),
      main_capacity_(capacity_bytes > window_capacity_
                         ? capacity_bytes - window_capacity_
                         : 0),
      protected_capacity_((main_capacity_ * 80) / 100),
      cms_(std::max<uint64_t>(1, n_objects_hint)),
      use_doorkeeper_(use_doorkeeper),
      doorkeeper_(use_doorkeeper ? std::max<uint64_t>(1, n_objects_hint) : 1)
{
    (void)capacity_bytes;
    if (use_doorkeeper_) {
        // D-09: register DK reset callback on CMS aging. No callback when
        // DK is off -> zero overhead for baseline wtinylfu.
        cms_.set_on_age_cb([this]{ doorkeeper_.clear(); });
    }
}
```

**What to copy — `access()` entry** (line 60 — the single pre-branch `cms_.record(key)` line that D-05 is amending):
```cpp
bool access(const std::string& key, uint64_t size) override {
    // Caffeine increments on EVERY access (hit OR miss, regardless of
    // region). [...]
    cms_.record(key);

    // Hit-path: probe window -> protected -> probation.
    // ...
```

**What to write — D-05 pre-CMS filter** (replace line 60 body; keep the surrounding comment):
```cpp
bool access(const std::string& key, uint64_t size) override {
    // D-05: Doorkeeper pre-CMS filter (Einziger-Friedman §4.3 paper-faithful).
    // When use_doorkeeper_ is true: a key's first touch is absorbed into the
    // Doorkeeper, not recorded into CMS. Subsequent touches (DK hit) record
    // into CMS as in baseline. When use_doorkeeper_ is false: record() is
    // called unconditionally — IDENTICAL to Phase 2 behavior.
    if (use_doorkeeper_) {
        if (doorkeeper_.contains(key)) {
            cms_.record(key);
        } else {
            doorkeeper_.add(key);
            // Skip cms_.record(): first touch is absorbed by DK per D-05.
        }
    } else {
        cms_.record(key);
    }

    // Hit-path: probe window -> protected -> probation.
    // ... (rest of access() UNCHANGED from Phase 2)
```

**Add private members** (after line 139, `CountMinSketch cms_;`):
```cpp
bool use_doorkeeper_;
Doorkeeper doorkeeper_;  // sized 1-bit when use_doorkeeper_ is false (ignored)
```

**Add include** (after line 8, `#include "count_min_sketch.h"`):
```cpp
#include "doorkeeper.h"
```

**Extend `reset()`** at line 118 — add DK clear when active:
```cpp
void reset() override {
    window_list_.clear();    window_map_.clear();    window_size_ = 0;
    protected_list_.clear(); protected_map_.clear(); protected_size_ = 0;
    probation_list_.clear(); probation_map_.clear(); probation_size_ = 0;
    cms_.reset();
    if (use_doorkeeper_) doorkeeper_.clear();  // D-09 / §specifics RAII
    stats = {};
}
```

**Invariants to preserve (critical):**
- **Grep-gate (L-12 / Phase 2 single-source invariant):** `grep -cE "record\(\s*(true|false)" include/wtinylfu.h` MUST still equal 4 after the edit. The DK branch adds **zero** new `record(true/false)` calls because pre-CMS filter sits BEFORE the existing hit-path, and the three `record(true, size)` + one `record(false, size)` calls at lines 69, 82, 101, 112 are UNCHANGED.
- **CMS `record(key)` count:** currently 1 call at line 60. After D-05, the 1 call becomes 1 call inside either branch of the `use_doorkeeper_` `if` — still 1 call per access (DK-on) or 1 call unconditional (DK-off). Do NOT introduce a duplicate.
- **Default-preserves-back-compat (Phase 4 L-8 equivalent):** `--policies wtinylfu` in a post-Phase-4 sweep must produce identical numbers to Phase 2's validation sweep. The ctor-default `use_doorkeeper=false` → all Phase 2 tests still pass (`make test` stays green on the existing `test_wtinylfu.cpp`).
- **Admission logic (D-08a..D-08e) UNCHANGED:** `admit_candidate_to_main_` at lines 168-199 is NOT touched. D-10 preserves "same admission, different counting."
- **Doorkeeper uses `n_objects_hint` same as CMS** (D-06) — same value already threaded through `make_policy`.
- **DK member allocation:** when `use_doorkeeper_=false`, `doorkeeper_(1)` allocates a 1-word (64-bit) vector — effectively free. Rejected as "allocate only when on" because non-initialized members are UB; 64 bits is noise.

---

### `src/main.cpp::make_policy` — `wtinylfu-dk` branch (D-08)

**Role:** factory extension. **Analog:** existing `wtinylfu` branch at `src/main.cpp:69`:
```cpp
if (name == "wtinylfu") return std::make_unique<WTinyLFUCache>(capacity, n_objects_hint);
```

**What to write** (add below line 69):
```cpp
if (name == "wtinylfu-dk") return std::make_unique<WTinyLFUCache>(capacity, n_objects_hint, /*use_doorkeeper=*/true);
```

**Also for Axis C** (add below line 67):
```cpp
if (name == "s3fifo")    return std::make_unique<S3FIFOCache>(capacity);           // alias of s3fifo-10 (D-11)
if (name == "s3fifo-5")  return std::make_unique<S3FIFOCache>(capacity, 0.05);
if (name == "s3fifo-10") return std::make_unique<S3FIFOCache>(capacity, 0.10);
if (name == "s3fifo-20") return std::make_unique<S3FIFOCache>(capacity, 0.20);
```

**Also for Axis D** (add below line 68):
```cpp
if (name == "sieve")        return std::make_unique<SIEVECache>(capacity);              // alias: promote_on_hit=true
if (name == "sieve-noprom") return std::make_unique<SIEVECache>(capacity, /*promote_on_hit=*/false);
```

**Invariants to preserve:**
- `(void)n_objects_hint;` comment at line 63 stays (S3-FIFO and SIEVE still ignore the hint).
- Return `nullptr` for unknown names (line 70) stays — don't silently accept typos.
- Ordering: legacy names (`s3fifo`, `sieve`, `wtinylfu`) stay above their variants for grep-findability.

---

### `src/main.cpp` — policy-label map (lines 184-190 and 234-240)

**Role:** label mapping. **Analog:** the two symmetric blocks already in `main.cpp`:
```cpp
std::string label = pn;
if (pn == "s3fifo")        label = "S3-FIFO";
else if (pn == "wtinylfu") label = "W-TinyLFU";
else for (auto& c : label) c = toupper(c);
```

**What to write — add per-variant display strings** (both blocks):
```cpp
std::string label = pn;
if      (pn == "s3fifo")      label = "S3-FIFO";
else if (pn == "s3fifo-5")    label = "S3-FIFO-5";
else if (pn == "s3fifo-10")   label = "S3-FIFO-10";
else if (pn == "s3fifo-20")   label = "S3-FIFO-20";
else if (pn == "sieve-noprom") label = "SIEVE-NoProm";
else if (pn == "wtinylfu")    label = "W-TinyLFU";
else if (pn == "wtinylfu-dk") label = "W-TinyLFU+DK";
else for (auto& c : label) c = toupper(c);
```

**IMPORTANT — `name()` inside each cache** must return the display string used in CSV output (so `plot_results.py`'s `POLICY_COLORS` dict keys match CSV rows):
- `S3FIFOCache::name()` at cache.h:328 currently returns `"S3-FIFO"`. For the 3 small_frac variants it needs to return `"S3-FIFO-5"` / `"S3-FIFO-10"` / `"S3-FIFO-20"` based on `small_frac_`. Option (a): store `small_frac_` as a member and branch in `name()`. Option (b): store the computed display string as a member set in the ctor. Prefer (b) — one string, set once, returned by value.
- `SIEVECache::name()` at cache.h:406 currently returns `"SIEVE"`. When `promote_on_hit_=false`, return `"SIEVE-NoProm"`.
- `WTinyLFUCache::name()` at wtinylfu.h:116 currently returns `"W-TinyLFU"`. When `use_doorkeeper_=true`, return `"W-TinyLFU+DK"`.

**Invariants to preserve:**
- The legacy `s3fifo` → `"S3-FIFO"` mapping stays identical (alias for `s3fifo-10` per D-11; must produce bit-identical CSV output to pre-Phase-4 runs).
- The legacy `sieve` → `"SIEVE"` mapping stays (D-12 aliases `sieve` to promote_on_hit=true).

---

### `tests/test_doorkeeper.cpp` — NEW

**Role:** test. **Analog:** `tests/test_wtinylfu.cpp` (full file — 199 lines).

**What to copy — file header + TEST_ASSERT macro + main()** (lines 1-36 and 186-198):
```cpp
// Standalone ... test binary.
// Pure C++17 assert() per CONTEXT.md D-06 — no third-party test framework.
// Run: make test (builds build/test/test_... and runs it).
//
// Coverage:
//   T1. ...
//   T2. ...
// ...

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "doorkeeper.h"

static int failures = 0;

#define TEST_ASSERT(expr, test_name) do {                                      \
    if (!(expr)) {                                                             \
        std::fprintf(stderr, "FAIL: %s — assertion \"%s\" failed at %s:%d\n", \
                     (test_name), #expr, __FILE__, __LINE__);                  \
        ++failures;                                                            \
    }                                                                          \
} while (0)

// ... T1..Tn static void test_*() functions ...

int main() {
    std::fprintf(stderr, "=== Doorkeeper test suite ===\n");
    test_contains_after_add();
    test_clear_zeros_all();
    test_fpr_sanity();
    if (failures > 0) {
        std::fprintf(stderr, "\n%d test(s) FAILED.\n", failures);
        return 1;
    }
    std::fprintf(stderr, "\nAll tests PASSED.\n");
    return 0;
}
```

**What to write — 3 coverage tests per CONTEXT.md item 12** ("contains-after-add, clear zeros all, FPR sanity at expected load"):

**T1 — `test_contains_after_add`** (copy the structure of `test_cms_basics` at lines 44-70):
```cpp
static void test_contains_after_add() {
    const char* tn = "contains_after_add";
    Doorkeeper dk(1024);
    TEST_ASSERT(!dk.contains("alpha"), tn);  // fresh filter: no false positives on unseen
    dk.add("alpha");
    TEST_ASSERT(dk.contains("alpha"), tn);   // must-hit after add
    TEST_ASSERT(dk.contains("alpha"), tn);   // idempotent re-check
    TEST_ASSERT(!dk.contains("beta_never_added_xyz"), tn);  // FP on fresh key improbable at size=4096 bits
    std::fprintf(stderr, "PASS: %s\n", tn);
}
```

**T2 — `test_clear_zeros_all`** (copy the structure of `test_cms_aging`):
```cpp
static void test_clear_zeros_all() {
    const char* tn = "clear_zeros_all";
    Doorkeeper dk(256);
    for (int i = 0; i < 100; ++i) dk.add("key_" + std::to_string(i));
    TEST_ASSERT(dk.contains("key_0"), tn);
    TEST_ASSERT(dk.contains("key_99"), tn);
    dk.clear();
    // All bits zero now — every previous add() must become "not contained".
    // (False positive after clear() is still possible on a fresh OTHER key,
    // but items that WERE added must now be gone.)
    for (int i = 0; i < 100; ++i) {
        TEST_ASSERT(!dk.contains("key_" + std::to_string(i)), tn);
    }
    std::fprintf(stderr, "PASS: %s\n", tn);
}
```

**T3 — `test_fpr_sanity`** (CONTEXT.md item 12: "FPR sanity at expected load"):
```cpp
static void test_fpr_sanity() {
    const char* tn = "fpr_sanity";
    // At 4 bits/element (D-06), paper predicts ~13% FPR at load n_objects_hint.
    // Add n_objects_hint items; test FPR on a disjoint query set of the same size.
    const uint64_t n = 10000;
    Doorkeeper dk(n);
    for (uint64_t i = 0; i < n; ++i) dk.add("added_" + std::to_string(i));
    uint64_t fp = 0;
    for (uint64_t i = 0; i < n; ++i) {
        if (dk.contains("query_" + std::to_string(i))) fp++;
    }
    double fpr = (double)fp / n;
    // Loose tolerance: target ~13%, accept [5%, 25%] to avoid flakiness.
    // A regression to ~50% FPR (e.g., only 1 hash fn) will blow this gate.
    TEST_ASSERT(fpr >= 0.05, tn);
    TEST_ASSERT(fpr <= 0.25, tn);
    std::fprintf(stderr, "PASS: %s (fpr=%.3f)\n", tn, fpr);
}
```

**Invariants to preserve:**
- No third-party test framework (Phase 2 D-06 — pure C++17 `assert()` + TEST_ASSERT macro).
- `failures` global + exit code `(failures > 0) ? 1 : 0` pattern — copy exactly from test_wtinylfu.cpp.
- No trace file dependency — tests use synthetic keys only (so `make test` runs in a clean checkout).
- Determinism: all tests use fixed inputs, no `std::random_device`. FNV-1a is deterministic across compilers (Phase 1 D-12/D-14).

---

### `Makefile` — `test_doorkeeper` build rule

**Role:** build target. **Analog:** `Makefile:79-96` (the `test_wtinylfu` block — 4-var TEST_SRC / TEST_OBJDIR / TEST_OBJ / TEST_TARGET pattern).

**What to copy** (lines 79-96):
```makefile
TEST_SRC     := tests/test_wtinylfu.cpp
TEST_OBJDIR  := build/test
TEST_OBJ     := $(TEST_OBJDIR)/test_wtinylfu.o
TEST_TARGET  := $(TEST_OBJDIR)/test_wtinylfu

$(TEST_OBJDIR):
	mkdir -p $(TEST_OBJDIR)

$(TEST_OBJ): $(TEST_SRC) | $(TEST_OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(TEST_TARGET): $(TEST_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

test: $(TEST_TARGET)
	@echo "=== Running W-TinyLFU test suite ==="
	$(TEST_TARGET)
```

**What to write — extend with DK test binary** (replace the single-file TEST_* vars with list vars and compose `test` as running both):
```makefile
# ==================== Test binaries ====================
# Two standalone assertion-based binaries: test_wtinylfu (Phase 2) and
# test_doorkeeper (Phase 4). Separate build/test/ objdir per D-07.

TEST_OBJDIR := build/test

TEST_WTLFU_SRC    := tests/test_wtinylfu.cpp
TEST_WTLFU_OBJ    := $(TEST_OBJDIR)/test_wtinylfu.o
TEST_WTLFU_TARGET := $(TEST_OBJDIR)/test_wtinylfu

TEST_DK_SRC    := tests/test_doorkeeper.cpp
TEST_DK_OBJ    := $(TEST_OBJDIR)/test_doorkeeper.o
TEST_DK_TARGET := $(TEST_OBJDIR)/test_doorkeeper

$(TEST_OBJDIR):
	mkdir -p $(TEST_OBJDIR)

$(TEST_WTLFU_OBJ): $(TEST_WTLFU_SRC) | $(TEST_OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<
$(TEST_WTLFU_TARGET): $(TEST_WTLFU_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

$(TEST_DK_OBJ): $(TEST_DK_SRC) | $(TEST_OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<
$(TEST_DK_TARGET): $(TEST_DK_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

test: $(TEST_WTLFU_TARGET) $(TEST_DK_TARGET)
	@echo "=== Running W-TinyLFU test suite ==="
	$(TEST_WTLFU_TARGET)
	@echo "=== Running Doorkeeper test suite ==="
	$(TEST_DK_TARGET)
```

**Invariants to preserve:**
- Separate `build/test/` objdir — do NOT pollute `build/` (Phase 2 D-07: `make && make test` should be fast, not rebuild main).
- `clean` at line 29 already rms `$(TEST_OBJDIR)` — no change needed.
- `make test` exits non-zero on any test failure (each binary's `main()` returns 1 on failures; Make propagates the first non-zero exit).

---

### `results/{congress,court}/ablation_doorkeeper.csv` — schema

**Role:** CSV output. **Analog:** `results/{workload}/mrc.csv` schema at `src/main.cpp:179`:
```cpp
csv << "cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio,accesses_per_sec\n";
```

**What to write — schema per §specifics** (mrc.csv columns + `variant`):
```
cache_frac,cache_size_bytes,policy,variant,miss_ratio,byte_miss_ratio,accesses_per_sec
0.01,<bytes>,W-TinyLFU,baseline,<miss>,<byte_miss>,<throughput>
0.01,<bytes>,W-TinyLFU+DK,+doorkeeper,<miss>,<byte_miss>,<throughput>
```

**Implementation note — where the `variant` column comes from:** `policy` column already carries `"W-TinyLFU"` vs `"W-TinyLFU+DK"` via `name()`. The `variant` column is a DUPLICATE in `{baseline, +doorkeeper}` form to make Phase 5's `compare_workloads.py` join trivial (per §specifics). One approach: post-process in Python (grep `-dk` suffix in the policy column and derive variant); another: emit the `variant` column directly from `main.cpp` when the `--ablation-name doorkeeper` flag is set. Planner's call — prefer whichever adds fewer C++ branches.

**Invariants to preserve:**
- Phase 1 CSV column conventions (snake_case, units in names, ARCHITECTURE Pattern 4).
- `accesses_per_sec` column (Phase 1 REFACTOR-03) — populated even if the numbers are boring (§specifics: Phase 5 Pareto plot may use it).
- Same schema for Congress and Court — "sweep CSVs share a schema across workloads" pattern (code_context §Established Patterns).

---

### `scripts/plot_results.py::plot_ablation_doorkeeper` — NEW

**Role:** plot function. **Analog:** `plot_alpha_sensitivity` at `plot_results.py:140-167` (same data shape: miss_ratio vs. alpha per policy at fixed 1% cache — ablation-doorkeeper IS an alpha sweep restricted to wtinylfu + wtinylfu-dk).

**What to copy** (lines 140-167, full function):
```python
def plot_alpha_sensitivity(results_dir, figures_dir):
    """Miss ratio vs. Zipf alpha (all 5 policies)."""
    path = os.path.join(results_dir, "alpha_sensitivity.csv")
    if not os.path.exists(path):
        print(f"  Skipping alpha plot: {path} not found")
        return

    df = pd.read_csv(path)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for policy in df["policy"].unique():
        sub = df[df["policy"] == policy].sort_values("alpha")
        color = POLICY_COLORS.get(policy, "gray")
        marker = POLICY_MARKERS.get(policy, "x")
        ax.plot(sub["alpha"], sub["miss_ratio"],
                marker=marker, markersize=5, label=policy,
                color=color, linewidth=1.5)

    ax.set_xlabel("Zipf Alpha")
    ax.set_ylabel("Miss Ratio (1% cache)")
    ax.set_title("Alpha Sensitivity")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_ylim(bottom=0)

    out = os.path.join(figures_dir, "alpha_sensitivity.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")
```

**What to write — `plot_ablation_doorkeeper`** (2×2 grid per CONTEXT.md item 10: workload × variant):
```python
def plot_ablation_doorkeeper(figures_dir, congress_dir="results/congress",
                              court_dir="results/court"):
    """DK ablation figure: 2-workload × 2-variant. Same alpha grid, 1% cache."""
    cong_path = os.path.join(congress_dir, "ablation_doorkeeper.csv")
    court_path = os.path.join(court_dir, "ablation_doorkeeper.csv")
    if not (os.path.exists(cong_path) and os.path.exists(court_path)):
        print(f"  Skipping DK ablation plot: both {cong_path} and {court_path} required")
        return
    c_df = pd.read_csv(cong_path); c_df["workload"] = "Congress"
    k_df = pd.read_csv(court_path); k_df["workload"] = "Court"
    df = pd.concat([c_df, k_df], ignore_index=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, wl in zip([ax1, ax2], ["Congress", "Court"]):
        sub_all = df[df["workload"] == wl]
        for policy in sorted(sub_all["policy"].unique()):
            sub = sub_all[sub_all["policy"] == policy].sort_values("alpha")
            color = POLICY_COLORS.get(policy, "gray")
            marker = POLICY_MARKERS.get(policy, "x")
            linestyle = "--" if policy.endswith("+DK") else "-"
            ax.plot(sub["alpha"], sub["miss_ratio"],
                    marker=marker, markersize=5, label=policy,
                    color=color, linewidth=1.5, linestyle=linestyle)
        ax.set_xlabel("Zipf Alpha")
        ax.set_title(wl)
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("Miss Ratio (1% cache)")
    ax2.legend(bbox_to_anchor=(1.02, 1), loc="upper left")

    out = os.path.join(figures_dir, "ablation_doorkeeper.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")
```

**Register in `main()`** — add (per Claude's Discretion: signature differs because this plot spans both workloads):
```python
# Cross-workload plots — only rendered when both CSVs exist.
plot_ablation_doorkeeper(os.path.join("results", "compare", "figures"))  # or equivalent
```

**Write analogous `plot_ablation_s3fifo` and `plot_ablation_sieve`** — same shape as `plot_ablation_doorkeeper`, different variants in the legend.

**Invariants to preserve:**
- `POLICY_COLORS` / `POLICY_MARKERS` dict lookup with `"gray"` / `"x"` fallback — needed for unknown future policies (lines 81-82, 116-117 precedent).
- `os.path.exists(path)` guards at top — silent skip (not error) when CSVs missing. This lets `make plots` run standalone after partial Phase 4 runs without crashing.

---

### `scripts/plot_results.py` — POLICY_COLORS / POLICY_MARKERS entries

**Role:** styling dict. **Analog:** existing dict literals at lines 45-61:
```python
POLICY_COLORS = {
    "LRU": "#1f77b4",
    "FIFO": "#ff7f0e",
    "CLOCK": "#2ca02c",
    "S3-FIFO": "#d62728",
    "SIEVE": "#9467bd",
    "W-TinyLFU": "#8c564b",
}

POLICY_MARKERS = {
    "LRU": "o",
    "FIFO": "s",
    "CLOCK": "^",
    "S3-FIFO": "D",
    "SIEVE": "v",
    "W-TinyLFU": "P",
}
```

**What to write — 4 new entries per Claude's Discretion** (distinguishable-but-related):
```python
POLICY_COLORS = {
    # ... existing 6 entries ...
    "W-TinyLFU+DK": "#8c564b",  # same brown as baseline; distinguished via linestyle dashed
    "S3-FIFO-5":   "#ff7f7f",   # lighter red variant of S3-FIFO's #d62728
    "S3-FIFO-10":  "#d62728",   # alias row; matches legacy "S3-FIFO" color
    "S3-FIFO-20":  "#8b0000",   # darker red variant
    "SIEVE-NoProm": "#9467bd",  # same purple as baseline; distinguished via dashed linestyle
}

POLICY_MARKERS = {
    # ... existing 6 entries ...
    "W-TinyLFU+DK": "X",
    "S3-FIFO-5":    "<",
    "S3-FIFO-10":   "D",
    "S3-FIFO-20":   ">",
    "SIEVE-NoProm": "v",
}
```

**Invariants to preserve:**
- Key strings must match `name()` output in C++ (grep-verifiable: `p->name()` at `src/main.cpp:214, 215, 273, 274` emits these strings into CSV rows → `POLICY_COLORS[policy]` lookup is exact-match).
- Missing entries fall back to `"gray"` / `"x"` at `POLICY_COLORS.get(policy, "gray")` — so tests won't crash on typos; but plots look uniform-gray for the mistyped policy. Belt-and-suspenders: run `make plots` on any first sweep and eyeball the legend.

---

### Axis C — S3-FIFO

---

### `include/cache.h` — `S3FIFOCache` ctor `small_frac` param (D-11)

**Role:** header extension. **Analog:** existing S3FIFOCache ctor at lines 235-238:
```cpp
S3FIFOCache(uint64_t capacity) : total_capacity_(capacity) {
    small_capacity_ = std::max((uint64_t)1, capacity / 10);
    main_capacity_ = capacity - small_capacity_;
}
```

**What to write — extended ctor** (default `0.1` preserves Phase 1 behavior exactly; `small_capacity_` formula generalizes from `capacity / 10`):
```cpp
S3FIFOCache(uint64_t capacity, double small_frac = 0.1)
    : total_capacity_(capacity), small_frac_(small_frac) {
    small_capacity_ = std::max<uint64_t>(1, static_cast<uint64_t>(capacity * small_frac));
    main_capacity_ = capacity - small_capacity_;
    // D-11 name mapping: match the policy string used in make_policy.
    if      (small_frac == 0.05) name_ = "S3-FIFO-5";
    else if (small_frac == 0.10) name_ = "S3-FIFO-10";  // also the default / legacy "S3-FIFO"
    else if (small_frac == 0.20) name_ = "S3-FIFO-20";
    else                          name_ = "S3-FIFO";     // fallback for unusual fracs
}
```

**Subtle: legacy alias.** Per D-11, the existing `s3fifo` policy string aliases to `s3fifo-10` and must produce bit-identical results to pre-Phase-4 runs. But the CSV `policy` column would now read `"S3-FIFO-10"` instead of `"S3-FIFO"`. Two options:
- (a) Always emit `"S3-FIFO-10"` in CSVs — breaks Phase 1/2 acceptance scripts that grep for `"S3-FIFO"`.
- (b) Keep legacy `"S3-FIFO"` for the default alias, use `"S3-FIFO-10"` only when the user explicitly specifies `--policies s3fifo-10`.

**Prefer (b):** the `make_policy` branch for `s3fifo` stays `S3FIFOCache(capacity)` (no `small_frac` arg → ctor default 0.1, name `"S3-FIFO-10"` in new naming, but we override to `"S3-FIFO"` in the legacy branch). Implement via a 2-arg ctor: `S3FIFOCache(uint64_t capacity, double small_frac, const char* name_override = nullptr)`. Planner's call — both are defensible.

**Add private member** (after line 233):
```cpp
double small_frac_;
std::string name_;
```

**Replace `name()` at line 328:**
```cpp
std::string name() const override { return name_; }
```

**Invariants to preserve:**
- **Default-preserves-back-compat (D-11):** `S3FIFOCache(capacity)` with `small_frac` defaulted to 0.1 must produce bit-identical CSV output to Phase 1 (`"S3-FIFO"` label, `capacity / 10` small_capacity). The `capacity / 10` vs. `capacity * 0.10` integer-truncation semantics differ for odd capacities — VERIFY this gives identical numbers or explicitly cast: `static_cast<uint64_t>(capacity * 0.10) == capacity / 10` for all `capacity` in `{0.001..0.1}` × working-set-bytes range. If not bit-identical, the D-11 default branch must keep `capacity / 10`.
- `main_capacity_ = capacity - small_capacity_` stays — do NOT add rounding that loses bytes.
- `stats = {}` is restored by `reset()` (line 332) — do not touch.

---

### `src/main.cpp` — `s3fifo-5` / `s3fifo-10` / `s3fifo-20` branches

**See "Axis B / make_policy" section above** — branches are literal one-liners. `s3fifo` (legacy) stays as `std::make_unique<S3FIFOCache>(capacity)` so the default-ctor path is exercised (ensuring the D-11 bit-identical guarantee is testable via the existing Phase 1 `mrc.csv` diff).

---

### `Makefile` — `ablation-s3fifo` target

**See "Axis A / Makefile" section above** — literal shape, just different `--policies` list.

---

### `results/{congress,court}/ablation_s3fifo.csv` — schema

**Analog:** `alpha_sensitivity.csv` at `src/main.cpp:229`:
```cpp
csv << "alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec\n";
```

**Schema — identical to alpha_sensitivity.csv** (D-13: ablation runs across the full alpha grid at fixed 1% cache):
```
alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec
0.6,S3-FIFO-5,<miss>,<byte_miss>,<throughput>
0.6,S3-FIFO-10,...
0.6,S3-FIFO-20,...
0.7,S3-FIFO-5,...
```

**Invariants to preserve:**
- Same schema as `alpha_sensitivity.csv` — the `make ablation-s3fifo` target can literally produce the file via the existing `--alpha-sweep` path and rename the output. No new C++ emitter needed if the policies list is all that differs.

---

### `scripts/plot_results.py::plot_ablation_s3fifo` — NEW

**Analog:** `plot_alpha_sensitivity` at lines 140-167 (already detailed above). Same function structure, reads `ablation_s3fifo.csv`, plots 3 lines (5/10/20) per workload. Can share the 2-panel layout from `plot_ablation_doorkeeper`.

---

### Axis D — SIEVE

---

### `include/cache.h` — `SIEVECache` ctor `promote_on_hit` (D-12)

**Role:** header extension. **Analog:** SIEVECache ctor at line 354 + hit-path at line 362:
```cpp
SIEVECache(uint64_t capacity) : capacity_(capacity) {}

// ... access() hit-path at line 356-365 ...
bool access(const std::string& key, uint64_t size) override {
    auto it = map_.find(key);
    if (it != map_.end()) {
        current_size_ -= it->second->size;
        it->second->size = size;
        current_size_ += size;
        it->second->visited = true;   // <-- line 362: the visited-bit set
        record(true, size);
        return true;
    }
    // ...
```

**What to write — extended ctor + hit-path guard** (single-line change at each site):
```cpp
SIEVECache(uint64_t capacity, bool promote_on_hit = true)
    : capacity_(capacity), promote_on_hit_(promote_on_hit),
      name_(promote_on_hit ? "SIEVE" : "SIEVE-NoProm") {}

// ... access() hit-path ...
bool access(const std::string& key, uint64_t size) override {
    auto it = map_.find(key);
    if (it != map_.end()) {
        current_size_ -= it->second->size;
        it->second->size = size;
        current_size_ += size;
        if (promote_on_hit_) it->second->visited = true;  // D-12: guarded
        record(true, size);
        return true;
    }
    // ... rest unchanged ...
```

**Add private members** (after line 351):
```cpp
bool promote_on_hit_;
std::string name_;
```

**Replace `name()` at line 406:**
```cpp
std::string name() const override { return name_; }
```

**Invariants to preserve:**
- **Default-preserves-back-compat (D-12):** `SIEVECache(capacity)` with `promote_on_hit` defaulted to `true` keeps the `it->second->visited = true` line firing on every hit — identical to Phase 1/3 SIEVE numbers. Grep-gate: `grep -c "promote_on_hit_" include/cache.h` == 2 (one init in ctor, one guard in access()).
- `evict_one()` at lines 378-404 is UNCHANGED — SIEVE's eviction hand still reads `hand_->visited` and clears it. What D-12 turns off is ONLY the hit-path promotion; new inserts at line 371 still get `visited=false`.
- `reset()` at line 407 restores `hand_valid_ = false` — do not touch.

---

### `src/main.cpp::make_policy` — `sieve-noprom` branch

**See "Axis B / make_policy" section above** — one-liner.

---

### `Makefile` — `ablation-sieve` target / `results/*/ablation_sieve.csv` / `plot_ablation_sieve`

**All three — analogs identical to Axis C.** The only file-level difference is the `--policies sieve,sieve-noprom` flag. Copy the Axis C pattern verbatim.

---

## Shared Patterns

### Pattern S1: Ctor-default preserves back-compat
**Source:** Phase 2 ctor `WTinyLFUCache(capacity, n_hint)` → Phase 4 extends to `(capacity, n_hint, bool=false)`. Same shape at 4 sites.
**Apply to:** All ctor extensions in `include/wtinylfu.h`, `include/cache.h` (S3FIFOCache, SIEVECache).
**Excerpt source:** `include/wtinylfu.h:39-53` (canonical Phase 2 ctor with zero-cost CMS member construction when the flag is off).

**The rule in one line:** new ctor params get default values; when the user passes nothing, the sweep output bits must match the prior phase's CSVs byte-for-byte. Phase 1's Congress `mrc.csv` should diff clean vs. Phase 4's Congress `mrc.csv` when neither introduces new flags.

### Pattern S2: Factory-branch for policy variants
**Source:** `src/main.cpp::make_policy` at lines 62-71 (7-line factory — one branch per policy string).
**Apply to:** All 4 new policy variants.
**Excerpt source:** `src/main.cpp:62-71`:
```cpp
static std::unique_ptr<CachePolicy> make_policy(const std::string& name, uint64_t capacity, uint64_t n_objects_hint) {
    (void)n_objects_hint;
    if (name == "lru")      return std::make_unique<LRUCache>(capacity);
    // ... one-line branches ...
    if (name == "wtinylfu") return std::make_unique<WTinyLFUCache>(capacity, n_objects_hint);
    return nullptr;
}
```
**The rule in one line:** each branch is ONE LINE, ordered with legacy names first (for grep-findability), returning `nullptr` on unknown.

### Pattern S3: Stats single-source invariant (L-12 from Phase 2)
**Source:** `include/wtinylfu.h` header comment at lines 22-28:
```cpp
// Stats single-source invariant (CONTEXT.md L-12): only the outer access()
// may call record(hit, size). Private helpers (evict_window_if_needed_,
// admit_candidate_to_main_, demote_protected_lru_to_probation_mru_) MUST
// NOT touch `stats` — doing so would double-count. Grep-enforced:
//   grep -cE "record\(\s*(true|false)" include/wtinylfu.h  ==  4
```
**Apply to:** Doorkeeper integration (MUST NOT touch `stats`), `on_age_cb_` callback (MUST NOT touch `stats`), all new test and ablation code.

**Grep gate for Phase 4:**
```bash
grep -cE "record\(\s*(true|false)" include/wtinylfu.h   # expect: 4 (unchanged from Phase 2)
grep -cE "record\(\s*(true|false)" include/doorkeeper.h # expect: 0 (DK never touches stats)
grep -cE "record\(\s*(true|false)" include/count_min_sketch.h # expect: 0 (unchanged)
```

### Pattern S4: Header-only with `hash_util.h`
**Source:** `include/count_min_sketch.h` full file structure (`#pragma once`, no `.cpp`, reuses `fnv1a_64` + seeds from `hash_util.h`).
**Apply to:** New `include/doorkeeper.h`.
**Excerpt source:** `include/count_min_sketch.h:1-10,55-60` (imports + `fnv1a_64(key, FNV_SEED_A)` pattern).

**The rule in one line:** header-only, FNV-1a seed-parameterized hash, no `std::hash<std::string>` (banned per Phase 1 D-14).

### Pattern S5: Test binary — TEST_ASSERT macro + failures counter
**Source:** `tests/test_wtinylfu.cpp:28-36, 186-198`:
```cpp
static int failures = 0;
#define TEST_ASSERT(expr, test_name) do {                                      \
    if (!(expr)) {                                                             \
        std::fprintf(stderr, "FAIL: %s — assertion \"%s\" failed at %s:%d\n", \
                     (test_name), #expr, __FILE__, __LINE__);                  \
        ++failures;                                                            \
    }                                                                          \
} while (0)

int main() {
    // ... call each test_* ...
    if (failures > 0) { std::fprintf(stderr, "\n%d test(s) FAILED.\n", failures); return 1; }
    std::fprintf(stderr, "\nAll tests PASSED.\n");
    return 0;
}
```
**Apply to:** `tests/test_doorkeeper.cpp` verbatim (change the failure message verb only).
**Invariant:** Accumulate failures (don't abort on first) → one `make test` run surfaces every broken invariant.

### Pattern S6: CSV schema-share across workloads
**Source:** Phase 1 D-04/D-05 convention + Phase 3 D-14 precedent + code_context §Established Patterns.
**Apply to:** All Phase 4 ablation CSVs (`ablation_s3fifo.csv`, `ablation_sieve.csv`, `ablation_doorkeeper.csv`) — identical schema between `results/congress/` and `results/court/`.
**Excerpt source:** `src/main.cpp:179,229` (mrc.csv and alpha_sensitivity.csv headers — same writer, different workload dirs).

**The rule in one line:** snake_case columns, units in names (`cache_size_bytes`, `cache_size_objects`), Phase-5 cross-workload join is trivial iff all schemas are workload-symmetric.

### Pattern S7: Makefile independence per D-17
**Source:** Phase 3 added `WORKLOAD=` + `TRACE=` plumbing at `Makefile:35-57`; Phase 4 D-17 EXPLICITLY does NOT reuse this. Each Phase 4 target owns its invocation shape.
**Apply to:** `shards-large`, `ablation-s3fifo`, `ablation-sieve`, `ablation-doorkeeper`, `phase-04`.
**Anti-pattern:** Do NOT add a `--shards-sweep` flag to `run-sweep` target. Do NOT thread `WORKLOAD=` into `ablation-*` targets.

### Pattern S8: `os.path.exists()` guard in plot functions
**Source:** Every existing `plot_*` function in `plot_results.py` (lines 72, 102, 143, 173, 198, 241, 291).
**Apply to:** All 4 new plot functions.
**Excerpt source:** `plot_results.py:72-74`:
```python
path = os.path.join(results_dir, "mrc.csv")
if not os.path.exists(path):
    print(f"  Skipping MRC plot: {path} not found")
    return
```
**Invariant:** `make plots` on a partial Phase 4 run never crashes — missing CSVs silently skip.

---

## No Analog Found

**None.** Every file in Phase 4 has a strong in-repo analog. Phase 4 is pure extension:
- SHARDS axis extends `src/main.cpp`'s existing SHARDS block (lines 302-384) and the existing `plot_shards_error` function.
- Doorkeeper axis extends the CMS / W-TinyLFU headers and copies the `test_wtinylfu.cpp` template.
- Ablation axes (S3-FIFO, SIEVE) extend the respective ctors in `cache.h` with one param each and copy the existing alpha-sweep plot function.

The only "new" artifact shapes are the CSV headers (`shards_convergence.csv`, `ablation_*.csv`) and the header file (`doorkeeper.h`) — both of which follow STACK.md / ARCHITECTURE.md locked parameters and existing `count_min_sketch.h` / `shards_error.csv` shapes.

---

## Metadata

**Analog search scope:**
- `include/` — all 7 headers
- `src/` — main.cpp, trace_gen.cpp, shards.cpp, workload_stats.cpp
- `tests/` — test_wtinylfu.cpp
- `scripts/` — plot_results.py, collect_trace.py, check_wtlfu_acceptance.py, collect_court_trace.py
- `Makefile`
- `.planning/phases/01-.../01-CONTEXT.md`, `02-.../02-CONTEXT.md`, `03-.../03-CONTEXT.md`, `02-.../02-01-CAFFEINE-NOTES.md`
- `.planning/research/STACK.md`, `ARCHITECTURE.md`, `PITFALLS.md`

**Files scanned:** 19
**Pattern extraction date:** 2026-04-20
