---
phase: 04-shards-large-scale-validation-ablations
reviewed: 2026-04-20T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - include/cache.h
  - include/count_min_sketch.h
  - include/wtinylfu.h
  - include/doorkeeper.h
  - src/main.cpp
  - Makefile
  - scripts/plot_results.py
  - tests/test_doorkeeper.cpp
findings:
  critical: 0
  warning: 1
  info: 3
  total: 4
status: clean
---

# Phase 04: Code Review Report

**Reviewed:** 2026-04-20
**Depth:** standard
**Files Reviewed:** 8 (7 source + 1 test)
**Status:** clean (no blocker or major findings)

## Summary

Phase 4 adds three ablation axes (S3-FIFO small-queue ratio, SIEVE visited-bit, W-TinyLFU+Doorkeeper), the header-only Doorkeeper Bloom filter, SHARDS large-scale validation infrastructure (`--shards-rates`, `--limit`, `--emit-trace`), and three new plot functions. All invariants from the focus areas verified clean:

- **L-12 stats single-source:** `grep -cE "record\(\s*(true|false)" include/wtinylfu.h` returns **4** — the DK-integrated access() path introduces no additional `record()` call. The if/else wraps the existing single `cms_.record()` invocation; hit/miss accounting is unchanged.
- **Back-compat guard:** `(small_frac == 0.1)` comparison is safe. In IEEE 754, the literal `0.10` and `0.1` are the same double. `s3fifo-10` (passes `0.10`) correctly triggers the `capacity / 10` integer path, producing the same `small_capacity_` as legacy `s3fifo` — which is the intended design for the 10%-baseline ablation arm.
- **Determinism:** `--emit-trace` path hardcodes `seed=42`. The `generate_zipf_trace(...)` default parameter is also `seed=42`. No `std::random_device`, `time()`, or non-deterministic source introduced.
- **D-14 std::hash ban:** Zero hits for `std::hash` in `include/` or `src/`. The only occurrence is a comment in `include/hash_util.h` documenting the ban. Doorkeeper uses FNV-1a from `hash_util.h`.
- **Null/ownership:** `dk_` (embedded `Doorkeeper doorkeeper_`) is a value member, not a pointer — no null-check required. The `use_doorkeeper_` runtime gate correctly wraps all `doorkeeper_.contains()`, `doorkeeper_.add()`, and `doorkeeper_.clear()` calls. Initialization uses the ctor parameter `use_doorkeeper` (no underscore), not the partially-initialized member, so member-init order is safe.
- **Thread-safety:** No `thread_local` or heap-allocated statics introduced in Phase 4.

One pre-existing warning surfaced during review (the alpha-sweep null-dereference); it is flagged because Phase 4 added new policy names that increase the exposure surface.

---

## Warnings

### WR-01: Null-pointer dereference in alpha-sweep loop when policy name is unknown

**File:** `src/main.cpp:348-351`
**Issue:** The MRC loop (line 276) null-checks the `unique_ptr<CachePolicy>` returned by `make_policy()` before dereferencing it. The alpha-sweep loop at lines 348–351 does **not**. If a user passes `--policies "wtinylfu-dk,typo" --alpha-sweep`, `make_policy("typo")` returns `nullptr` and `run_simulation(sweep_trace, *p)` immediately dereferences a null pointer, crashing.

This bug pre-dates Phase 4 (introduced in Phase 2 commit `8599b60`). Phase 4 exacerbates the risk by adding four new policy names (`s3fifo-5`, `s3fifo-10`, `s3fifo-20`, `sieve-noprom`, `wtinylfu-dk`) that users can pass via `--policies`, increasing the likelihood of a typo.

**Fix:**
```cpp
// src/main.cpp, alpha-sweep inner loop (~line 348)
for (auto& pn : policy_names) {
    auto p = make_policy(pn, cache_bytes, n_obj_hint);
    if (!p) {
        std::cerr << "Unknown policy: " << pn << "\n";
        continue;
    }
    auto t_start = std::chrono::steady_clock::now();
    run_simulation(sweep_trace, *p);
    // ... rest unchanged
}
```

---

## Info

### IN-01: Coverage gap — no tests for ablation ctor extensions

**File:** `tests/` (missing)
**Issue:** Phase 4 adds ctor extensions to `S3FIFOCache` (delegating ctor with `small_frac` + `name_override`) and `SIEVECache` (`promote_on_hit` flag), and a new `WTinyLFUCache` ctor path (`use_doorkeeper=true`). `tests/test_doorkeeper.cpp` covers Doorkeeper in isolation. No test binary exercises:
- `S3FIFOCache(cap, 0.05, "S3-FIFO-5")` — verifies `small_frac()` returns 0.05 and `name()` returns "S3-FIFO-5".
- `SIEVECache(cap, false)` — verifies `name()` returns "SIEVE-NoProm" and that hit-path leaves `visited` unset.
- `WTinyLFUCache(cap, n, true)` with DK + aging cycle — verifies `doorkeeper_.clear()` fires via `on_age_cb_` when the CMS halves.

This is a coverage gap, not a correctness defect. The Makefile `ablation-*` targets serve as functional regression tests via CSV output inspection, but there is no assertion-based unit test.

**Suggestion:** Add a `tests/test_ablation_ctors.cpp` covering the three ctor extensions and wire it into `make test`.

### IN-02: Makefile `TRACE` variable is unquoted and subject to shell word-splitting

**File:** `Makefile:53`
**Issue:** The `TRACE` make variable is expanded unquoted in `SWEEP_FLAGS`:
```makefile
SWEEP_FLAGS := --trace $(TRACE) --replay-zipf ...
```
A value containing shell metacharacters (spaces, semicolons) would cause word-splitting or command injection when `$(TARGET) $(SWEEP_FLAGS)` is invoked. This is a pre-Phase-4 concern for the `run-sweep` target. Phase 4 targets (`shards-large`, `ablation-*`) hardcode their own trace paths as string literals in recipe lines and are not affected.

For a research tool invoked by trusted developers this is low-severity, but it is worth noting.

**Fix:** Quote the variable where it is used, or document that `TRACE` must not contain whitespace:
```makefile
SWEEP_FLAGS := --trace "$(TRACE)" --replay-zipf --alpha-sweep --output-dir $(WORKLOAD_RESULTS_DIR)
```

### IN-03: Doorkeeper Kirsch-Mitzenmacher degeneration when h2 == 0

**File:** `include/doorkeeper.h:50-52, 61-63`
**Issue:** When `fnv1a_64(key, FNV_SEED_B)` returns exactly `0`, both Kirsch-Mitzenmacher iterations (`i=0` and `i=1`) compute the same bit index (`h1 % size_`), effectively reducing the filter to `k=1` for that key. The key will still be `add()`-ed and subsequently `contains()`-ed correctly for itself, but the filter's false-positive bound degrades for keys that collide at that slot.

The probability is `1/2^64` per distinct key — vanishingly rare in practice and not a crash or data-loss risk. No action is required for a research simulator, but the behavior is undocumented.

**Suggestion:** Add a one-line comment in `contains()` and `add()` noting the degenerate-to-k=1 behavior when `h2 == 0`, matching the Kirsch-Mitzenmacher paper's own caveat:
```cpp
// K-M note: if h2==0, both probes alias to h1%size_ (degenerate k=1).
// Probability ~1/2^64 per key; acceptable for an ablation Bloom filter.
const uint64_t h2 = fnv1a_64(key, FNV_SEED_B);
```

---

## Focus-Area Checklist Results

| Focus area | Verdict | Notes |
|---|---|---|
| L-12 stats single-source (record count == 4) | PASS | `grep -cE "record\(\s*(true|false)" include/wtinylfu.h` == 4 |
| Back-compat guard `(small_frac == 0.1)` | PASS | 0.10 == 0.1 in IEEE 754; s3fifo-10 uses `capacity/10` path as intended |
| `--emit-trace` path traversal | PASS | Path written to `std::ofstream` directly; no shell execution; OS-level access controls apply |
| Makefile command injection (new Phase 4 targets) | PASS | Phase 4 targets hardcode trace paths; `$(TRACE)` exposure is pre-Phase-4 (IN-02) |
| Determinism: no `std::random_device` or time seed | PASS | `generate_zipf_trace` defaults seed=42; `--emit-trace` hardcodes seed=42 |
| D-14 `std::hash` ban | PASS | Zero hits in `include/` and `src/` source |
| DK null-guard (`dk_` is a value member) | PASS | `use_doorkeeper_` gate wraps all DK accesses; member-init reads ctor parameter not partially-init member |
| Thread-safety — no new `thread_local` or static heap | PASS | No heap-allocated statics introduced |
| Test coverage — `test_doorkeeper.cpp` | PASS (partial) | Three well-scoped tests; ablation ctor coverage gap flagged as IN-01 |

---

_Reviewed: 2026-04-20_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
