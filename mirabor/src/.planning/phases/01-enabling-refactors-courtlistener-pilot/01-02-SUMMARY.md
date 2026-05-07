---
phase: 01-enabling-refactors-courtlistener-pilot
plan: 02
subsystem: trace-gen
tags: [cpp17, refactor, alpha-sweep, determinism, performance]

# Dependency graph
requires:
  - 01-01 (hash_util include + self-test already present in src/main.cpp — this plan must preserve that)
provides:
  - "include/trace_gen.h declares prepare_objects + generate_replay_trace (D-10 signatures)"
  - "src/trace_gen.cpp implements prepare_objects + generate_replay_trace; replay_zipf is a thin wrapper (D-11)"
  - "src/main.cpp alpha-sweep hoists prepare_objects above the for-loop; per-alpha body calls generate_replay_trace"
affects:
  - 01-03-throughput-measurement (sibling; will add accesses_per_sec column to the same alpha-sweep CSV)
  - phase-5-multi-seed-sweep (5 seeds x 2 workloads x 7 alphas x 6 policies = 420 runs — inherits the per-alpha dedupe+shuffle elimination)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hoisted loop-invariant preprocessing (dedupe + deterministic shuffle) out of parametric sweep"
    - "Thin backwards-compat wrapper preserving stdout diagnostic and byte-identical trace output for the same seed"
    - "Split RNG seed contract (shuffle seed, Zipf seed+1) preserved across the refactor to maintain trace identity"

key-files:
  created: []
  modified:
    - "include/trace_gen.h"
    - "src/trace_gen.cpp"
    - "src/main.cpp"

key-decisions:
  - "prepare_objects returns std::vector<std::pair<std::string, uint64_t>> by value — move-return elides the copy and matches D-10 exactly; no out-parameter variant needed"
  - "The 'Replay-Zipf: N unique objects ...' stdout diagnostic lives in the wrapper only; direct callers of generate_replay_trace (the alpha-sweep path) stay quiet. This preserves legacy wrapper behavior AND avoids 7 duplicate diagnostic lines per sweep"
  - "Seed contract locked in the new functions: prepare_objects constructs std::mt19937_64(seed); generate_replay_trace constructs ZipfGenerator(N, alpha, seed + 1). Greps in the acceptance criteria catch any future edit that breaks the +1 offset"
  - "Alpha-sweep guard uses !prepared_objects.empty() (rather than a second !raw_trace.empty() check) — prepared_objects is the canonical 'real trace is active' signal inside the sweep block"
  - "No new includes added in either file; std::pair reaches src/trace_gen.cpp via <unordered_map> and include/trace_gen.h via <algorithm>+<vector>; the 2-line syntax probe confirmed the header parses"

patterns-established:
  - "Split loop-invariant trace preprocessing into a reusable pure function + parametric sampler; keep the legacy bundled call as a wrapper for back-compat"
  - "Use 'prepared_*' or a similarly-named local hoisted above the sweep loop to communicate loop-invariant intent at the call site"

requirements-completed: [REFACTOR-02]

# Metrics
duration: 2min 45s
completed: 2026-04-19
---

# Phase 01 Plan 02: replay_zipf Split + Alpha-Sweep Hoist Summary

**Split `replay_zipf()` into `prepare_objects()` + `generate_replay_trace()` per D-10, hoisted the one-time dedupe+shuffle out of the 7-alpha sweep in `src/main.cpp`, and kept `replay_zipf()` as a thin wrapper so the one-shot main-MRC call at line 145 (and any downstream consumer) compiles unchanged.**

## Performance

- **Duration:** 2 min 45 s
- **Started:** 2026-04-19T01:20:00Z
- **Completed:** 2026-04-19T01:22:45Z (approx.)
- **Tasks:** 3
- **Files modified:** 3 (0 created, 3 edited)

## Accomplishments

- `include/trace_gen.h` now declares three functions in the replay family: unchanged `replay_zipf`, new `prepare_objects`, new `generate_replay_trace`. Declarations match D-10 byte-for-byte. No new `#include` lines — the 2-line syntax probe (`#include "trace_gen.h"` + `int main(){}`) compiles cleanly under `g++ -std=c++17 -Iinclude -fsyntax-only`.
- `src/trace_gen.cpp` implements `prepare_objects` (dedupe preserving first-seen size via `std::unordered_map`, then deterministic shuffle via `std::mt19937_64(seed)`) and `generate_replay_trace` (Zipf sampling via `ZipfGenerator(objects.size(), alpha, seed + 1)`). `replay_zipf` is now a 3-statement wrapper that calls both and emits the legacy `"Replay-Zipf: N unique objects from real trace, generating X accesses with alpha=Y"` stdout line.
- `src/main.cpp` alpha-sweep block (at lines 225-241 post-01-01) now declares `std::vector<std::pair<std::string, uint64_t>> prepared_objects` above the `for (double a : alphas)` loop, populates it via `prepare_objects(raw_trace)` exactly once when `!raw_trace.empty()`, and consumes it inside the loop via `generate_replay_trace(prepared_objects, num_requests, a)`. The synthetic fallback (`generate_zipf_trace`) is unchanged.
- **Preservation verified:** The one-shot non-sweep call `trace = replay_zipf(raw_trace, num_requests, alpha);` at `src/main.cpp:145` is untouched. `#include "hash_util.h"` at line 15 and the `hash_util_self_test()` gate at line 128 (both from 01-01) remain intact.
- `make clean && make` is warning-free under `-Wall -Wextra` on all three tasks.
- Smoke tests all pass with exit 0:
  - Synthetic alpha-sweep (`--alpha-sweep --num-requests 20000 --num-objects 5000`): 36-line `alpha_sensitivity.csv` (header + 7 alphas x 5 policies).
  - Fake-trace replay-zipf alpha-sweep (`--trace /tmp/fake_traces/fake.csv --replay-zipf --alpha-sweep --num-requests 50000`): also 36 lines.
  - The `"Replay-Zipf:"` diagnostic appears exactly once per run (from the one-shot wrapper call at line 145) — confirming the sweep path skips the wrapper and calls `generate_replay_trace` directly.

## Task Commits

Each task was committed atomically with `--no-verify` (parallel-wave executor convention):

1. **Task 1: Declare prepare_objects + generate_replay_trace in include/trace_gen.h** — `3a42e0b` (feat)
2. **Task 2: Implement prepare_objects + generate_replay_trace in src/trace_gen.cpp; convert replay_zipf to a wrapper** — `958cd18` (refactor)
3. **Task 3: Hoist prepare_objects above the alpha-sweep loop in src/main.cpp** — `4fe591b` (refactor)

## Final Signatures as Shipped

```cpp
// include/trace_gen.h
std::vector<std::pair<std::string, uint64_t>> prepare_objects(
        const std::vector<TraceEntry>& raw_trace, uint64_t seed = 42);

std::vector<TraceEntry> generate_replay_trace(
        const std::vector<std::pair<std::string, uint64_t>>& objects,
        uint64_t num_requests, double alpha, uint64_t seed = 42);

// legacy, preserved byte-for-byte
std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed = 42);
```

`prepare_objects` body (src/trace_gen.cpp): `seen` map dedupe + `objects.push_back({key, size})` + `std::mt19937_64 shuffle_rng(seed); std::shuffle(...)`.

`generate_replay_trace` body: `ZipfGenerator zipf(objects.size(), alpha, seed + 1);` then the N-iteration loop emitting `{i, objects[rank].first, objects[rank].second}`.

`replay_zipf` body (now 3 logical statements): `auto objects = prepare_objects(real_trace, seed); std::cout << "Replay-Zipf: ..."; return generate_replay_trace(objects, num_requests, alpha, seed);`.

## Timing Note (D-11 speedup)

A micro-benchmark harness (compiled against `build/trace_gen.o` in `/tmp/bench_prereef`) ran the two paths back-to-back on a 200,000-entry / 20,000-unique fake trace with `num_requests=50000` per alpha over the canonical 7 alphas (0.6..1.2):

| Path | Region timed | Wall-clock |
|------|--------------|-----------:|
| Pre-refactor equivalent (7x `replay_zipf`) | dedupe+shuffle + Zipf sample, 7 times | 0.0703 s |
| Post-refactor (1x `prepare_objects` + 7x `generate_replay_trace`) | dedupe+shuffle once + Zipf sample 7 times | 0.0282 s |
| **Speedup** | — | **2.49x** |

Speedup scales with the `raw_trace.size() / num_requests` ratio and with the shuffle RNG cost; on a production real trace (larger `raw_trace`, same 7 alphas) the absolute savings will be larger. No pre-refactor baseline was captured from the full end-to-end `./cache_sim --alpha-sweep` binary (the alpha-sweep region is dominated by `run_simulation()` cost across 5 policies, so the full-binary wall-clock would partially mask the trace-gen speedup); the micro-benchmark isolates the refactored region cleanly.

For Phase 5's 5 seeds x 2 workloads x 7 alphas x 6 policies = 420 runs, each sweep call now does 1 dedupe+shuffle instead of 7 — i.e., over the whole sweep matrix, `raw_trace.size() * (7 - 1) * 5 * 2 = 60 * raw_trace.size()` bytes of redundant work is eliminated per alpha-column traversal.

## Verification Results

| Check | Expected | Actual | Pass |
|-------|---------:|-------:|:---:|
| `grep 'prepare_objects(' include/trace_gen.h src/trace_gen.cpp src/main.cpp \| wc -l` | >= 4 | 4 | YES |
| `grep 'generate_replay_trace(' include/trace_gen.h src/trace_gen.cpp src/main.cpp \| wc -l` | >= 3 | 4 | YES |
| `grep -c 'replay_zipf(' src/main.cpp` (one-shot at line 145 only) | 1 | 1 | YES |
| `grep -n '#include "hash_util.h"' src/main.cpp` (01-01 preserved) | line 15 | line 15 | YES |
| `grep -n 'hash_util_self_test' src/main.cpp` (01-01 gate preserved) | line 128 | line 128 | YES |
| `make clean && make` | exit 0, no warnings | exit 0, no warnings | YES |
| Synthetic 20K alpha sweep exit / `alpha_sensitivity.csv` lines | exit 0, >= 36 lines | exit 0, 36 lines | YES |
| Fake-trace 50K replay-zipf alpha sweep exit / lines | exit 0, >= 36 lines | exit 0, 36 lines | YES |
| `"Replay-Zipf:"` diagnostic count per sweep run | 1 (one-shot only) | 1 | YES |
| Seed contract grep (`std::mt19937_64 shuffle_rng(seed);` + `ZipfGenerator zipf(objects.size(), alpha, seed + 1);`) | both present | both present | YES |

## Deviations from Plan

None. All three tasks executed as written in the 01-PATTERNS.md target split. Plan task 3 expected the pre-refactor alpha-sweep block at `src/main.cpp` lines 219-222; the post-01-01 `main.cpp` places that block at lines 225-228 (the extra 6 lines are the 01-01 `#include "hash_util.h"` and self-test gate block). This was anticipated in the plan's "NOTE ON WAVE" section and did not require any plan adjustment — the Edit operated on a unique anchor (`std::cout << "\n" << std::string(8 + 10 * policy_names.size(), '-') << "\n";` + the original ternary block) rather than line numbers, so the shift was transparent.

No auto-fixes (Rules 1-3) were applied — no bugs, missing critical functionality, or blocking issues were encountered.

No authentication gates encountered.

No `traces/congress_trace.csv` was present in this worktree, so the real-trace smoke test in the plan's Task 3 action step (the one that requires `traces/congress_trace.csv`) was replaced with a synthetically-generated 2K / 200K-row fake trace placed in `/tmp/fake_traces/fake.csv` and `/tmp/fake_traces/fake_big.csv`. The CSV-schema shape (`timestamp,key,size` with one header row) matches `load_trace()`'s expectations. Real-trace verification by the orchestrator or in a downstream phase against the actual `traces/congress_trace.csv` remains the authoritative end-to-end check.

## Threat Flags

None. The refactor is purely internal: no new network endpoints, no new input-parsing surface, no new trust boundaries. STRIDE T-01-04 (seed-contract tampering) is mitigated by the grep-level acceptance criteria on the literal `shuffle_rng(seed)` and `ZipfGenerator(..., seed + 1)` strings, which would catch a future edit that flipped or removed the +1 offset.

## Self-Check: PASSED

- Created files: none (this plan modifies only).
- Modified files verified present:
  - `include/trace_gen.h` — FOUND (has `prepare_objects` + `generate_replay_trace` + unchanged `replay_zipf`)
  - `src/trace_gen.cpp` — FOUND (has `prepare_objects` def, `generate_replay_trace` def, wrapper `replay_zipf` def)
  - `src/main.cpp` — FOUND (`prepared_objects` declared, hoisted `prepare_objects(raw_trace)` call, per-alpha `generate_replay_trace(prepared_objects, num_requests, a)` call, one-shot `replay_zipf(raw_trace, num_requests, alpha)` at line 145, hash_util include + self-test gate intact)
- Task commits verified via `git log --oneline`:
  - `3a42e0b` (Task 1) — FOUND
  - `958cd18` (Task 2) — FOUND
  - `4fe591b` (Task 3) — FOUND
