# Architecture Patterns

**Domain:** C++17 cache-policy simulator extension — W-TinyLFU policy, second trace source (PACER/CourtListener), large-scale SHARDS validation, cross-workload analysis
**Researched:** 2026-04-16
**Confidence:** HIGH (existing code fully mapped; W-TinyLFU structure from Einziger & Friedman 2015 and standard Caffeine design)

## Guiding Principle

**Extend, don't restructure.** The existing architecture is clean: a strategy pattern over `CachePolicy`, header-only policies, a pipeline-style `main()` driver, and CSV as the only contract to Python. Every new piece in this milestone must slot into that shape. The one place the existing pattern visibly bends is W-TinyLFU (it's genuinely larger than the other five policies combined), and that single exception is handled by a **subordinate header** that `cache.h` includes — preserving the "`make_policy` sees one header" invariant while keeping `cache.h` readable.

The constraint "header-only policies, no external C++ deps" is preserved throughout. The Count-Min Sketch, Doorkeeper (Bloom filter), and W-TinyLFU itself are all implemented in new headers using only the C++17 standard library — matching the existing five policies.

## Recommended Architecture

### Layer Map (after this milestone)

```
include/
├── cache.h                      # Existing: CachePolicy base, LRU, FIFO, CLOCK, S3-FIFO, SIEVE
│                                #   + `#include "wtinylfu.h"` at the bottom (single new line)
├── count_min_sketch.h           # NEW. CountMinSketch<W,D> + aging; header-only
├── doorkeeper.h                 # NEW. Bloom filter (single hash table of bits); header-only
├── wtinylfu.h                   # NEW. WTinyLFUCache : public CachePolicy
│                                #   includes count_min_sketch.h + doorkeeper.h
├── trace_gen.h                  # Unchanged signatures; replay_zipf now also used for
│                                #   PACER traces via the same load_trace path
├── workload_stats.h             # Unchanged
└── shards.h                     # Unchanged (existing SHARDS handles 1M+ traces;
                                 #   see "Build-Order Dependencies" for the only caveat)

src/
├── main.cpp                     # Extended: make_policy() gains `wtinylfu` branch;
│                                #   no structural rewrite
├── trace_gen.cpp                # Unchanged
├── workload_stats.cpp           # Unchanged
└── shards.cpp                   # Unchanged

scripts/
├── collect_trace.py             # Unchanged (Congress.gov-specific)
├── collect_court_trace.py       # NEW. PACER/CourtListener collector, same CSV output
│                                #   schema (timestamp,key,size) so downstream is identical
├── plot_results.py              # Extended: W-TinyLFU in POLICY_COLORS/MARKERS;
│                                #   new plot_cross_workload() reading results/compare/*
└── compare_workloads.py         # NEW. Loads two MRC CSVs (congress + court), emits
                                 #   side-by-side comparison figures + summary CSV

results/
├── mrc.csv, alpha_sensitivity.csv, ...  # Existing per-run outputs (one workload at a time)
├── congress/                    # NEW convention: per-trace subdirectory
│   └── mrc.csv, shards_mrc.csv, ...
├── court/                       # NEW: parallel tree for second trace
│   └── mrc.csv, shards_mrc.csv, ...
├── validation/                  # Existing: small exact-SHARDS run
├── shards_large/                # NEW: 1M+ synthetic trace for SHARDS rigor
└── compare/                     # NEW: cross-workload analysis outputs
    ├── mrc_compare.csv          # Long-form: trace,cache_frac,policy,miss_ratio
    ├── policy_ranking.csv       # Ranks per policy per trace
    └── figures/*.pdf
```

### Component Boundaries

| Component | Responsibility | Communicates With | New/Modified |
|-----------|---------------|-------------------|--------------|
| `CountMinSketch` (`include/count_min_sketch.h`) | 4-row × W-column 4-bit counter sketch with `increment(key)`, `estimate(key)`, `reset_all()` aging when sum hits sample-size threshold | `WTinyLFUCache` only | New |
| `Doorkeeper` (`include/doorkeeper.h`) | Single-hash-family Bloom filter for "has this key been seen once?" — absorbs first-access noise before it touches the CMS. `contains()`, `add()`, `clear()` on aging | `WTinyLFUCache` only | New |
| `WTinyLFUCache` (`include/wtinylfu.h`) | Window LRU (1%) + SLRU main (protected 80% / probation 20%) with TinyLFU admission (CMS+Doorkeeper). Inherits `CachePolicy`, exposes only `access()`/`name()`/`reset()` | `CachePolicy` base (up), its three internals (down). **No coupling to other policies.** | New |
| `cache.h` | Existing five policies; gains one `#include "wtinylfu.h"` so `main.cpp` keeps its single include | `wtinylfu.h` | Modified (1 line) |
| `main.cpp::make_policy()` | Factory; adds `if (name == "wtinylfu") return std::make_unique<WTinyLFUCache>(capacity);` branch | `WTinyLFUCache` | Modified (1 branch + default policy list + label mapping) |
| `collect_court_trace.py` | PACER/CourtListener trace collector: emits `timestamp,key,size` rows identical to Congress format | Filesystem: writes `traces/court_trace.csv` | New |
| `compare_workloads.py` | Offline post-processor: reads two `results/{congress,court}/mrc.csv` and emits unified long-form CSV + figures | pandas/matplotlib | New |
| SHARDS (`src/shards.cpp`) | Unchanged interface; at 1M+ traces the `std::set<uint64_t>` access-times structure is the hot path. Verify memory before Phase X (see Pitfalls) | — | Unchanged |

### Why W-TinyLFU gets its own header

Three reasons, in order:
1. **Lines of code.** CMS + Doorkeeper + Window LRU + SLRU main + admission logic + aging is ~250–350 LOC. Dropping it inline in `cache.h` pushes that file past 700 lines and buries the other five policies.
2. **Internal types.** W-TinyLFU needs `CountMinSketch` and `Doorkeeper` types. Nesting them inside `WTinyLFUCache` works but hurts testability (you can't write a quick standalone CMS sanity test). Separate headers mean each internal is independently includable.
3. **Precedent in the codebase.** The existing split is: "headers define; .cpp implement" except for cache policies which are header-only. W-TinyLFU stays header-only (preserves the rule) but the header itself is modular. `cache.h` gains one `#include` line — `make_policy()` doesn't need to know.

**Explicit rejection of alternatives:**
- *"Put everything in cache.h"* — rejected: file becomes unnavigable; violates "each policy is a self-contained section" spirit even though it preserves the letter.
- *"Put W-TinyLFU in a .cpp"* — rejected: breaks the header-only-policies convention, asymmetric with the other five, forces `make_policy` to link a new TU when the existing pattern is pure include.

### Data Flow

#### Single-workload flow (unchanged shape, W-TinyLFU added)

```
                         ┌──────────────────────┐
                         │ scripts/             │
                         │  collect_trace.py    │  ── Congress.gov API, rate-limited
                         │  collect_court_      │  ── PACER/CourtListener (NEW)
                         │    trace.py          │
                         └──────────┬───────────┘
                                    │ CSV (timestamp,key,size)
                                    ▼
                   traces/{congress,court}_trace.csv
                                    │
                                    ▼
               ┌──────────────────────────────────────┐
               │ cache_sim --trace <file>             │
               │   [--replay-zipf] [--output-dir …]   │
               └──────────────────┬───────────────────┘
                                  │
                 ┌────────────────┼──────────────────┐
                 ▼                ▼                  ▼
          load_trace()    characterize()     working_set_bytes()
                 │                │                  │
                 └────────┬───────┴──────┬───────────┘
                          │              │
                          ▼              ▼
                ┌───────────────────────────────┐
                │  For each (cache_frac, pol):  │
                │    make_policy(pol, bytes)    │
                │      ├─ LRU                   │
                │      ├─ FIFO                  │
                │      ├─ CLOCK                 │
                │      ├─ S3-FIFO               │
                │      ├─ SIEVE                 │
                │      └─ W-TinyLFU  (NEW)      │
                │         │                     │
                │         ├─ Window LRU (1%)    │
                │         └─ Main SLRU (99%)    │
                │              ▲                │
                │              │ admission      │
                │              │ decision       │
                │         ┌────┴────────────┐   │
                │         │ CountMinSketch  │   │
                │         │ + Doorkeeper    │   │
                │         └─────────────────┘   │
                │    run_simulation(trace, *p)  │
                └──────────────┬────────────────┘
                               │
                               ▼
                  results/{trace_subdir}/mrc.csv
                  results/{trace_subdir}/alpha_sensitivity.csv
                  results/{trace_subdir}/one_hit_wonder.csv
                  results/{trace_subdir}/shards_mrc.csv
```

The only new arrow inside the simulator is **admission-filter feedback into W-TinyLFU's main region**: when the window LRU evicts a candidate, W-TinyLFU compares the candidate's CMS estimate against the victim's CMS estimate at the main region's probation head and admits the higher-frequency item. This is **entirely internal to `WTinyLFUCache`** — nothing else in the simulator changes.

#### Cross-workload flow (new)

```
results/congress/mrc.csv ─┐
                           ├─► compare_workloads.py ─► results/compare/mrc_compare.csv
results/court/mrc.csv    ─┘                         ─► results/compare/policy_ranking.csv
                                                    ─► results/compare/figures/*.pdf
```

Key design decision: **`main.cpp` does NOT get a multi-trace mode.** The simulator stays single-trace-per-invocation. Cross-workload comparison is a pure post-processing step in Python, reading already-produced CSVs. Rationale:
- The simulator is already correctly factored for one trace per run; making it multi-trace-aware would duplicate state (workload characterization, MRC tables, SHARDS) with no real gain.
- Two `./cache_sim` invocations — one per trace — are cheap and parallelizable.
- Placing comparison in Python keeps the C++ core focused and means new comparisons (e.g., adding a third trace later, or slicing by alpha) don't require a recompile.
- Follows the existing "CSV is the contract" rule exactly.

Invocation convention for this milestone:
```
./cache_sim --trace traces/congress_trace.csv --replay-zipf \
            --output-dir results/congress --alpha-sweep --shards
./cache_sim --trace traces/court_trace.csv --replay-zipf \
            --output-dir results/court --alpha-sweep --shards
python3 scripts/compare_workloads.py \
        --traces congress,court --output-dir results/compare
```

The Makefile gains two targets: `run-court` (mirrors `run-sweep` but with court trace + `results/court`) and `compare` (invokes `compare_workloads.py`).

#### Large-scale SHARDS validation flow (new)

**No new code path needed.** `generate_zipf_trace(num_requests=1_000_000, num_objects=100_000, alpha=0.9)` already produces the required trace; the existing `SHARDS.process()` and `build_mrc()` handle it. Only driver-level changes:
- New Make target `run-shards-large`: `./cache_sim --num-requests 1000000 --num-objects 100000 --shards --output-dir results/shards_large`.
- Extend the sampling-rate list in `main.cpp:261` to include `0.0001` (0.01%) — one literal added to the vector.
- The exact-SD guard at `src/main.cpp:289-290` (`trace.size() > 50000`) already correctly skips the exact path for a 1M trace; no change needed. But this means exact MRC validation must stay on the smaller trace. **Keep the existing 50K validation run** (it's what the paper's SHARDS MAE claim was validated against) and add the 1M run as a **scale test, not an accuracy test** — accuracy is measured by comparing SHARDS-at-1% against SHARDS-at-10% within the large trace (convergence), since no exact reference is affordable at that size.

This point deserves callout: it is **Pitfall #1** below. The target "0.1% MAE at 1% sampling per the SHARDS paper" can only be validated at sizes where exact MRC is computable. The large-scale run demonstrates *scalability and self-consistency*, not *ground-truth accuracy*.

### State Management

No changes to the existing invariants:
- `CachePolicy` owns all its state; `reset()` clears it between cache sizes.
- Traces are immutable `std::vector<TraceEntry>` once built.
- `WTinyLFUCache::reset()` must clear: window LRU list+map, main SLRU protected + probation lists+maps, CMS (zero all counters + sample_count), Doorkeeper (clear bits). All owned by the cache; no shared state with other policies.

## Patterns to Follow

### Pattern 1: Internal Sub-Policies as Nested Components, Not Inheritance

**What:** W-TinyLFU contains a window LRU and a main SLRU internally. These are *implementation details* — not reused `LRUCache` instances.

**When:** Any policy that composes simpler eviction behaviors.

**Why:** `LRUCache` in `cache.h` records to `CacheStats` on every access. If W-TinyLFU delegated to an `LRUCache` instance, stats would double-count. Equally, window LRU is byte-bounded while main SLRU tracks object counts for some variants — they diverge from the public `LRUCache` in subtle ways.

**Example:**
```cpp
class WTinyLFUCache : public CachePolicy {
    // Do NOT do: LRUCache window_;  // wrong — double-counts stats
    // DO:
    struct WindowEntry { std::string key; uint64_t size; };
    std::list<WindowEntry> window_;
    std::unordered_map<std::string, std::list<WindowEntry>::iterator> window_map_;
    uint64_t window_bytes_ = 0;
    uint64_t window_capacity_;
    // ...similar for main_protected_ and main_probation_
    CountMinSketch cms_;
    Doorkeeper doorkeeper_;
    // ...
};
```

This matches how `S3FIFOCache` (`cache.h:205`) handles its own small/main queues inline.

### Pattern 2: Capacity Is Always Bytes, Not Objects

**What:** W-TinyLFU receives `capacity` in bytes from `make_policy()`, just like the other five. Internal sub-regions split that byte budget (1% / 99%, then within main 20% probation / 80% protected).

**When:** Every policy.

**Why:** The simulator's entire byte-miss-ratio story depends on byte-accurate capacity. The one temptation — because TinyLFU's literature often phrases things in objects — is to switch W-TinyLFU to object-count. Resist: pass bytes in, track bytes internally, evict until bytes fit.

### Pattern 3: Admission Decision as a Small Private Method

**What:** Put the TinyLFU admission rule in one method: `bool should_admit(const std::string& candidate, const std::string& victim)`. Returns true when CMS estimate of candidate > CMS estimate of victim (with the Doorkeeper short-circuit: if candidate is not in the Doorkeeper, definitely deny; add it and return false).

**When:** Anywhere admission decisions are made. Keeps the `access()` body legible and the admission policy testable.

### Pattern 4: CSV Output Follows Existing Column Conventions

**What:** New CSVs use snake_case columns, include units in names where relevant (`cache_size_bytes`, `cache_size_objects`), and match existing schemas where possible.

**Example — `results/compare/mrc_compare.csv`:**
```
trace,cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio
congress,0.01,1048576,LRU,0.42,0.51
court,0.01,4194304,LRU,0.38,0.47
...
```

Long form (trace as column), not wide form. Plays well with `pandas` pivoting and matplotlib `hue=trace`.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Generalizing `collect_trace.py` into a Polymorphic Collector

**What it looks like:** A new `scripts/collect_trace.py` with `--source congress|court` and a registry of endpoint generators.

**Why bad:**
- The two APIs differ in auth (Congress needs `CONGRESS_API_KEY` query param; CourtListener uses `Authorization: Token ...` header), rate-limit characteristics (Congress ~1 req/s; CourtListener different), endpoint taxonomy (bills/amendments/votes vs dockets/opinions/oral-args), error semantics, and pagination.
- A generalized collector would abstract all of that behind a "source driver" interface — more indirection than the 150-line script is worth for two sources.
- Two focused scripts are easier to debug when collection fails at 3 AM at request 9,527.

**Instead:** `scripts/collect_court_trace.py` is a separate file with the same output schema. Copy-paste the ~30 lines of shared CSV-writing / backoff logic. If a third source is ever added, revisit the DRY decision then.

### Anti-Pattern 2: Building a Multi-Trace Simulator Driver

**What it looks like:** `main.cpp` gains `--traces trace1.csv,trace2.csv` and runs every policy on every trace in one invocation.

**Why bad:**
- Doubles the state the driver manages (workload characterization per trace, MRC tables per trace, SHARDS runs per trace).
- Breaks the one-CSV-per-analysis convention — would need `results/mrc_congress.csv` and `results/mrc_court.csv` or a `trace` column inside a unified file; either way the driver now owns comparison semantics.
- Python is strictly better at tabular joins. Let the C++ simulator stay single-trace and let `compare_workloads.py` own the cross-cut.

**Instead:** Two invocations with different `--output-dir` values, post-processed in Python.

### Anti-Pattern 3: Using `std::hash<std::string>` for the Count-Min Sketch

**What it looks like:** `cms_[std::hash<std::string>{}(key) % W][row_idx]++` with a per-row salt.

**Why bad:** `std::hash` is libstdc++-implementation-specific; on libc++ vs libstdc++ you'll get different CMS behavior and non-reproducible results across compilers. The existing SHARDS uses FNV-1a (`src/shards.cpp:85-98`) exactly to avoid this.

**Instead:** Use FNV-1a (reuse the helper from `shards.cpp` — extract into `include/hash_util.h`) with 4 different seed values for the 4 CMS rows, or use a fixed murmur-style mixer. Whatever choice, lock it down so seed = 42 gives identical W-TinyLFU results across runs and compilers. This is a **build-order dependency**: extract the hash helper **before** writing the CMS.

### Anti-Pattern 4: Running Exact Stack Distances on the 1M Trace

**What it looks like:** Adding `--shards-exact` to the 1M-trace run and waiting for the O(n²) path.

**Why bad:** 1M × ~500K unique ~= 5 × 10^11 operations. Hours to days. And the 50K guard at `main.cpp:289` would trip, silently skipping — wasting a run.

**Instead:** Keep exact SD on the existing ≤ 50K validation trace. On the 1M trace, validate SHARDS by **self-convergence**: compare 0.1% vs 1% vs 10% sampling — if they agree to within a few tenths of a percent miss ratio at matched cache sizes, that's the scalability claim.

## Build-Order Dependencies

Order matters because some components are consumed by later ones. The roadmap phase structure should respect this topology:

```
Phase: Hash helper (tiny)
  └─ include/hash_util.h — extract FNV-1a from shards.cpp
      │
      ├─► Phase: Count-Min Sketch
      │     include/count_min_sketch.h (depends on hash_util)
      │     Test: standalone sanity — insert 100K synthetic keys,
      │           check estimate vs true count on a few hot keys.
      │     │
      │     ▼
      ├─► Phase: Doorkeeper
      │     include/doorkeeper.h (depends on hash_util)
      │     Test: false-positive rate roughly matches Bloom theory.
      │     │
      │     ▼
      └─► Phase: W-TinyLFU
            include/wtinylfu.h (depends on both above)
            Integrate into cache.h, make_policy, policy_names default.
            Validate: run against existing Congress trace, check that W-TinyLFU
                      beats LRU on high-alpha (> 0.9) workloads and doesn't
                      catastrophically lose on low-alpha.

Parallel track (independent):
  Phase: PACER/CourtListener collector
    scripts/collect_court_trace.py
      │
      ▼
  Phase: Court records trace collection (long-running, operator-run)
    traces/court_trace.csv  (≥ 20K rows)
      │
      ▼
  Phase: Cross-workload replay-Zipf + compare
    ./cache_sim --trace traces/court_trace.csv --replay-zipf --output-dir results/court
    scripts/compare_workloads.py

Parallel track (independent; does not block W-TinyLFU or court):
  Phase: Large SHARDS validation
    Extend rates vector in main.cpp to include 0.0001.
    Run on 1M synthetic trace → results/shards_large/.
    Produce self-convergence table across sampling rates.
```

**Critical ordering constraints:**
1. **`hash_util.h` before `count_min_sketch.h` before `wtinylfu.h`** — straightforward compile dependency chain. The CMS can't be written correctly until the deterministic hash is settled.
2. **Court collector before court trace before cross-workload analysis** — collecting ≥ 20K court records will likely take hours (per PROJECT.md Congress took a few hours at ~1.6 s/request; court may be slower). This is the single highest-risk item timeline-wise and should kick off **first** in parallel with the W-TinyLFU implementation track.
3. **W-TinyLFU validation on Congress trace before court trace** — debug the new policy on the known-good workload so that any surprises on the court trace are about workload differences, not policy bugs.
4. **Large SHARDS can run any time** — purely additive; no dependencies on other new work.

## Scalability Considerations

| Concern | Current (20K trace) | 1M synthetic trace | 20K court trace |
|---------|---------------------|---------------------|------------------|
| Trace `std::vector<TraceEntry>` memory | ~2 MB | ~100 MB | ~2 MB |
| SHARDS `access_times_` set | O(sampled unique) = small | At 0.01% sampling: ~100–1000 unique; at 10%: ~100K | Small |
| SHARDS `last_access_` unordered_map | Same | Same | Same |
| Exact stack distances | Fits (~5 × 10^9 ops, still minutes) | **SKIPPED** (guard at main.cpp:289) | Fits |
| W-TinyLFU CMS memory | W=16K × D=4 × 4 bits = 32 KB | Same (CMS size is fixed, not trace-dependent) | Same |
| W-TinyLFU Doorkeeper | ~1% of expected unique keys in bits | Same | Same |
| W-TinyLFU per-access cost | 4 hash + CMS update + window/main LRU ops | ~1M × O(log cache_size) = seconds | Same |
| Total simulator wall clock | Seconds | A few minutes per policy per cache size | Seconds |

The W-TinyLFU memory footprint is fixed at construction (CMS width × depth × 4 bits plus Doorkeeper bits), independent of trace size — unlike SHARDS where the access-times set can grow with unique keys. This is a feature, not a bug: sized correctly for the workload it's constant-memory.

**Tunable parameters** (document these in the W-TinyLFU header with literature defaults):
- CMS width `W` = 8 × max cache capacity in objects (Einziger & Friedman recommend 8× the sample size)
- CMS depth `D` = 4 rows
- Counter size = 4 bits (0–15, with aging halving on sum threshold)
- Sample size `S` for aging = 10 × W (when total increments reach S, halve every counter; Doorkeeper clears)
- Window ratio = 1% of capacity (Caffeine default; sometimes 0.2–2% depending on workload)
- Main SLRU split = 80% protected / 20% probation

## Sources

- **Existing codebase** (HIGH confidence — read directly):
  - `/Users/mirayu/civicache/include/cache.h` — CachePolicy base and five existing policies; S3-FIFO is the closest structural analog to W-TinyLFU
  - `/Users/mirayu/civicache/src/main.cpp` — driver structure, `make_policy()` factory, CSV output conventions
  - `/Users/mirayu/civicache/src/shards.cpp` — FNV-1a hash usage pattern (`shards.cpp:85-98`) to mirror in `hash_util.h`
  - `/Users/mirayu/civicache/scripts/collect_trace.py` — Congress.gov collector to mirror for court records
  - `/Users/mirayu/civicache/.planning/codebase/ARCHITECTURE.md` — baseline architecture
  - `/Users/mirayu/civicache/.planning/codebase/STRUCTURE.md` — "Where to Add New Code" section provides the exact extension points for new policies
- **W-TinyLFU design** (HIGH confidence — well-established in literature):
  - Einziger, Friedman, Manes, "TinyLFU: A Highly Efficient Cache Admission Policy" (ACM TOS 2017) — CMS + Doorkeeper + aging scheme
  - Caffeine cache (ben-manes/caffeine) — reference Java implementation; SLRU main with window LRU; standard parameter defaults (1% window, 80/20 SLRU split, 4-bit CMS counters, 4 rows)
- **PACER vs CourtListener** (MEDIUM confidence — per PROJECT.md TRACE-03 is an open research question; actual API/limits must be verified during the collector-build phase): PROJECT.md already lists PACER as pending, with CourtListener/RECAP as the likely free-tier fallback.

---

*Architecture research: 2026-04-16*
