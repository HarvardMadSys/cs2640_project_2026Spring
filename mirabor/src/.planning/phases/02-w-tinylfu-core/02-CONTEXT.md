# Phase 2: W-TinyLFU Core — Context

**Gathered:** 2026-04-19
**Status:** Ready for planning

<domain>
## Phase Boundary

A working, validated W-TinyLFU policy plugged into the existing `CachePolicy` hierarchy in `include/cache.h`. Must exhibit correct "hot object survives scan" behavior and the expected α-regime relationship to LRU on Congress replay (W-TinyLFU beats LRU at α≥0.8 on every cache size; within ±2% of LRU at α=0).

What's explicitly IN scope:
1. `include/count_min_sketch.h` — 4-bit × depth 4 × nextpow2 width CMS with Caffeine-compatible aging
2. `include/wtinylfu.h` — 1% window LRU + 99% main SLRU (80% protected / 20% probation) + TinyLFU admission
3. `include/cache.h` — one `#include "wtinylfu.h"` line
4. `src/main.cpp::make_policy()` — new `wtinylfu` branch; CLI label updates
5. `tests/test_wtinylfu.cpp` — standalone test binary + `make test` target exercising CMS basics, the hot-object-survives-scan invariant, and determinism
6. `scripts/plot_results.py` — W-TinyLFU in `POLICY_COLORS`/`POLICY_MARKERS` so existing sweep plots render it
7. Validation run: 6-policy sweep on Congress trace; confirms α-regime acceptance criteria

What's explicitly NOT in this phase:
- Doorkeeper Bloom filter prefilter — Phase 4 ablation
- Hill-climbing adaptive window tuner — explicitly OMITTED (STACK.md §Rejected; STATE.md)
- CourtListener trace run — Phase 3 (needs L13 pre-work + this phase's W-TinyLFU done first)
- Large-scale SHARDS / ablations / cross-workload analysis — Phase 4+

</domain>

<decisions>
## Implementation Decisions

### Capacity Semantics (Gray Area 1)

- **D-01:** W-TinyLFU is **byte-bounded throughout**, matching the other 5 policies in `include/cache.h`. Total capacity is bytes; window = 1% of byte budget; main = 99% of byte budget; SLRU split = 80% protected bytes / 20% probation bytes. Evict-while-over-byte-budget logic mirrors the existing `LRUCache` pattern (`include/cache.h:66-76`).
- **D-02:** **CMS width derived from trace workload_stats avg_object_size.** At W-TinyLFU construction, the caller passes a `n_objects_hint` computed as `capacity_bytes / avg_object_size_from_workload_stats`. CMS width = `nextpow2(n_objects_hint)`. Thread the avg-object-size value from `src/workload_stats.cpp`'s pre-scan output into `make_policy()` when the policy name is `"wtinylfu"`. `main.cpp` already runs workload_stats early enough that the number is available before policy construction.
- **D-03:** CMS depth = 4 (locked from STACK.md §CMS Params). CMS aging threshold = `10 × (width × depth)` accesses (L5 + Caffeine FrequencySketch.java pre-work per L13).

### Unit Test Infrastructure (Gray Area 2)

- **D-04:** **Standalone test binary.** New file `tests/test_wtinylfu.cpp` with its own `int main()`. New Makefile target `make test` builds `build/test_wtinylfu` and runs it; the binary exits non-zero on any assertion failure and prints a one-line FAIL banner.
- **D-05:** Test coverage required:
  - CMS basics — `record(k)` n times → `estimate(k) ≈ n` within +3 tolerance (depth=4 minimum; simulator sanity check)
  - CMS aging — after exactly `10×W` records, all counters halved; sample_count reset to 0
  - W-TinyLFU hot-object-survives-scan (WTLFU-04 literal) — insert a hot object, access 20×, then access 1000 unique keys sequentially, confirm hot key still in cache at end
  - Determinism — two back-to-back runs with the same seed produce identical final cache contents (serialize cache keys to a sorted list for comparison)
- **D-06:** No third-party test framework (no Catch2, no googletest). Pure C++17 `assert()` + return code. Matches the project's "no external deps" constraint and keeps the grader's build clean.
- **D-07:** Makefile `make test` target depends on `$(TARGET)` not being rebuilt on every run — use separate object files under `build/test/` so `make && make test` is fast.

### Admission Edge Cases (Gray Area 3)

- **D-08:** **Mirror Caffeine `BoundedLocalCache.java` byte-for-byte.** L13 pre-work pulls the Caffeine source BEFORE implementation so edge-case rules are captured as D-08a…D-08d here:
  - **D-08a:** Empty probation segment → candidate admitted unconditionally (no CMS comparison). Record the admission; CMS still updated on access.
  - **D-08b:** Main SLRU has spare byte capacity → candidate admitted unconditionally (no CMS comparison). Only run the `freq(candidate) > freq(victim)` test when main is at-or-over its byte budget.
  - **D-08c:** Probation→protected promotion fires on ANY hit during probation residency (Caffeine default; not the paper's "second hit" rule). Move promoted entry to protected MRU.
  - **D-08d:** Protected overflow on promotion → demoted protected LRU-tail entry moves to probation MRU (not probation tail). Caffeine's `reorderProbation` behavior.
  - **D-08e:** Admission test tie (`freq(candidate) == freq(victim)`) → reject candidate (favor incumbents; Caffeine default). Already in L7 but restated for completeness.

- **D-09:** Pre-work task (expand L13): fetch and diff against Caffeine v3.x `FrequencySketch.java` + `BoundedLocalCache.java`. Record in SUMMARY: (a) the exact `sampleSize` formula used, (b) the exact admission/promotion code paths for D-08a..D-08e, (c) any D-08 rule that does NOT match Caffeine — flag as a deliberate deviation with justification.

### CMS Aging API Boundary (Gray Area 4)

- **D-10:** **CMS self-manages aging.** Public API:
  - `record(const std::string& key)` — increments key's counters AND increments internal `sample_count_`; at `sample_count_ == 10×W`, halves all counters (shift right 1) and resets `sample_count_` to 0.
  - `estimate(const std::string& key) const` — returns min across the 4 rows (standard CMS query).
  - `reset()` — zeros all counters and sample_count (called by W-TinyLFU's `reset()`).
  - `force_age()` — test-only hook; triggers an immediate halve without waiting for threshold. Used by `tests/test_wtinylfu.cpp` to verify D-05's aging-cadence test deterministically.
- **D-11:** CMS uses FNV-1a from `include/hash_util.h` (Phase 1 artifact) with the 4 existing seeds (`FNV_SEED_A..D`). One FNV-1a call per row, keyed on `(seed, key_bytes)`. Matches L10 and the Caffeine "single-base-hash split into N independent hashes via different mixing constants" pattern.

### Locked Decisions (from research + Phase 1)

- **L-1:** Header-only C++17 for `wtinylfu.h` and `count_min_sketch.h`; no new external deps (PROJECT.md; STACK.md §Stack).
- **L-2:** Mirror Caffeine `WindowTinyLfuPolicy` line-by-line, NOT paraphrase the paper (STATE.md C6; drives D-08).
- **L-3:** Roll our own CMS; rejected xxhash/cityhash/wyhash and `cm-sketch` GitHub lib (STACK.md §Rejected).
- **L-4:** CMS counter width 4 bits, depth 4 (Caffeine standard).
- **L-5:** Aging cadence: halve every counter after every `10 × (width × depth)` records. Verified against `FrequencySketch.java` during L13 pre-work.
- **L-6:** 1% window LRU + 99% main SLRU (80% protected / 20% probation) — Caffeine defaults. NO hill-climbing.
- **L-7:** Admission test: `freq(candidate) > freq(victim)`. Ties reject candidate (captured in D-08e).
- **L-8:** Hill-climbing adaptive window tuner OMITTED (STACK.md §Rejected).
- **L-9:** Doorkeeper DEFERRED to Phase 4 (STACK.md + Phase 1 CONTEXT.md + this phase's deferred).
- **L-10:** Reuse `include/hash_util.h` from Phase 1 with its 4 seeds (`FNV_SEED_A..D`).
- **L-11:** `wtinylfu.h` is a subordinate header included from `cache.h`; `count_min_sketch.h` is a peer header for standalone testability (research/ARCHITECTURE.md).
- **L-12:** W-TinyLFU's internal window LRU + protected + probation lists are private implementation — NOT reused `LRUCache` instances. Stats single-source: only `WTinyLFUCache` records to `CacheStats` (research/ARCHITECTURE.md §191-195).
- **L-13:** Pre-work (task 0 of Phase 2): pull Caffeine v3.x `FrequencySketch.java` and `BoundedLocalCache.java`; verify `sampleSize = 10 × max(capacity, 1)` formula; capture D-08a..D-08d edge-case rules from the source. Block implementation tasks until this reading is committed.

### Claude's Discretion

- Concrete `POLICY_COLORS` / `POLICY_MARKERS` value for W-TinyLFU in `plot_results.py` — pick a color distinguishable from the existing 5 (e.g., purple `#9467bd`, marker `D`); not worth a discussion cycle.
- Exact tolerance constant in the CMS basics test (D-05 first bullet) — pick whatever makes a 10K-access smoke run stable; +3 is a starting guess.
- Logging verbosity during W-TinyLFU validation sweep — follow the existing `std::cout` pattern from the other 5 policies; no new log channels.

</decisions>

<specifics>
## Specific Ideas

- **Caffeine is the reference, paper is secondary.** Whenever Caffeine source disagrees with the Einziger-Friedman paper, Caffeine wins (per L2, D-08). The student-project Caffeine-compatibility claim is stronger than paper-fidelity for this milestone.
- **CMS width sourcing is ONE new plumbing line.** `src/main.cpp`'s existing workload_stats call already computes `avg_object_size`; pass it into `make_policy("wtinylfu", ..., avg_object_size)`. Do NOT recompute or re-scan.
- **Test binary has no trace dependency.** `tests/test_wtinylfu.cpp` generates its own synthetic keys (e.g., `"hot"`, `"scan_000000"…"scan_000999"`). Does not require `traces/congress_trace.csv` to run. Grader can `make test` in a clean checkout.
- **Policy string is `"wtinylfu"` in the CLI** (`--policies wtinylfu,lru,...`) and `"W-TinyLFU"` as `name()` for CSV output and plot legends. STACK.md §191-193 already locked this.
- **Sweep run for WTLFU-05 validation** uses the existing `make run-sweep` target with `--policies lru,fifo,clock,s3fifo,sieve,wtinylfu`. No new run-sweep scaffolding required; Phase 1's `--output-dir results/congress` already threads through.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner) MUST read these before acting.**

### Requirements & roadmap
- `.planning/REQUIREMENTS.md` §W-TinyLFU — WTLFU-01..05 must-haves
- `.planning/ROADMAP.md` §Phase 2 — 4 success criteria
- `.planning/PROJECT.md` — Core value; "no external C++ deps" non-negotiable

### Technical research (prior-work outputs)
- `.planning/research/STACK.md` §W-TinyLFU Implementation Parameters — locked numerics (L4, L5, L6, L7)
- `.planning/research/STACK.md` §Rejected alternatives — why NOT xxhash/wyhash/hill-climbing
- `.planning/research/ARCHITECTURE.md` §Why W-TinyLFU gets its own header — L11
- `.planning/research/ARCHITECTURE.md` §191-195 — hard separation from LRUCache; L12
- `.planning/research/PITFALLS.md` §C6 — "mirror Caffeine line-by-line, not paraphrase paper"

### Phase 1 artifacts (prerequisites)
- `include/hash_util.h` — 4 seeds `FNV_SEED_A..D` + `fnv1a_64()` used by CMS rows (L10, D-11)
- `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-01-SUMMARY.md` — hash_util exports and usage pattern
- `src/workload_stats.cpp` — where `avg_object_size` is computed (source for D-02's CMS width)

### External sources to pull during L13 pre-work
- Caffeine v3.x source: `com.github.benmanes.caffeine.cache.FrequencySketch` — for `sampleSize` formula and `increment`/`tryReset` implementations
- Caffeine v3.x source: `com.github.benmanes.caffeine.cache.BoundedLocalCache` — for the exact admission / promotion / eviction code paths covered in D-08a..D-08e
- Einziger, Friedman, Manes — "TinyLFU: A Highly Efficient Cache Admission Policy" (TOS 2017) — for theoretical grounding only; Caffeine source wins on disagreements

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `include/hash_util.h` (Phase 1) — `fnv1a_64(key, seed)` with 4 pre-defined seeds → CMS uses directly for its 4 rows (no new hash work)
- `include/cache.h` §`LRUCache` (lines 49-82) — byte-bounded evict-while-over-budget pattern → W-TinyLFU's internal window + probation + protected lists mirror this structure exactly, each with its own byte budget
- `include/cache.h::CachePolicy` (lines 34-46) — base class with `access()/name()/reset()` virtual interface + `stats` + `record(hit, size)` helper; `WTinyLFUCache` inherits directly, calls `record()` exactly once per `access()` call
- `src/main.cpp::make_policy()` — factory dispatch for `--policies` CLI flag; add `if (name == "wtinylfu") { … }` branch
- `src/workload_stats.cpp` — pre-scan that produces `avg_object_size_bytes`; pass result to `make_policy` for the `wtinylfu` branch
- `scripts/plot_results.py` `POLICY_COLORS` / `POLICY_MARKERS` — add `"W-TinyLFU"` key; existing plot loops iterate the dict

### Established Patterns
- **Header-only policies** — every policy in `cache.h` is fully defined inline; `wtinylfu.h` + `count_min_sketch.h` follow this rule
- **Byte-bounded capacity** — every existing policy evicts while `current_size_ > capacity_`; W-TinyLFU does the same at the outer level AND at each sub-region
- **Stats recording** — each policy calls `record(hit, size)` exactly once per `access()`; W-TinyLFU must do this at the OUTER `access()` only, never from inside its private sub-region ops (would double-count per L12)
- **reset() discipline** — every `reset()` zeros all internal state AND zeros `stats`; W-TinyLFU's `reset()` must clear window list+map, protected list+map, probation list+map, CMS table + sample_count, AND `stats`
- **CLI policy selection** — `main.cpp` parses `--policies lru,fifo,clock,...` as comma-separated; W-TinyLFU adds `wtinylfu` to the accepted set

### Integration Points
- `include/cache.h` — one-line addition: `#include "wtinylfu.h"` at bottom of file (keeps `main.cpp`'s single-include invariant per research/ARCHITECTURE.md)
- `src/main.cpp` — (a) `make_policy()` factory branch, (b) `--policies` default list gains `wtinylfu`, (c) CSV column label map if any policy-name-to-display-string table exists
- `Makefile` — new `test` target compiling `tests/test_wtinylfu.cpp` to `build/test_wtinylfu`; do NOT merge test object files with the main simulator build
- `scripts/plot_results.py` — `POLICY_COLORS["W-TinyLFU"] = "#9467bd"`; `POLICY_MARKERS["W-TinyLFU"] = "D"` (claude's-discretion defaults; user can override in a later phase)

</code_context>

<deferred>
## Deferred Ideas

- **Doorkeeper Bloom filter prefilter** — Phase 4 ablation (L9). Adding it now would expand the PR; W-TinyLFU validation is cleaner without it.
- **Hill-climbing adaptive window-size tuner** — permanently omitted (L8). If anyone proposes adding it in a future phase, re-read STACK.md §Rejected first.
- **W-TinyLFU on CourtListener trace** — Phase 3 (depends on Phase 2 done + Phase 3's 20K collection run). Validation in this phase is Congress-only (WTLFU-05).
- **CMS/W-TinyLFU parameter sensitivity study** (sweep window fraction 0.5% vs 1% vs 2%, SLRU split 70/30 vs 80/20 vs 90/10) — interesting systems-course content but explicitly NOT scoped here. Capture as a potential v2 or a Phase 6 writeup enrichment.
- **Unit test framework migration** (Catch2 / doctest) — no value for the current 1-test-file scope; revisit if tests/ grows past ~3 files.

</deferred>

<scope_creep_log>
## Scope Creep Redirected

None during this discussion — the user engaged with the 4 gray areas as bounded implementation choices and did not propose new capabilities.

</scope_creep_log>
