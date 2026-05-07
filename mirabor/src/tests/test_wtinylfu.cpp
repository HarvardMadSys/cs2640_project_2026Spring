// Standalone W-TinyLFU + CountMinSketch test binary.
// Pure C++17 assert() per CONTEXT.md D-06 — no third-party test framework.
// Run: make test (builds build/test/test_wtinylfu and runs it).
//
// Coverage (CONTEXT.md D-05):
//   T1. CMS basics: record a key N times; estimate(key) in [N, N+3] (bounded tolerance),
//       saturating at COUNTER_MAX = 15.
//   T2. CMS aging: force_age() halves all counters and resets sample_count to 0;
//       a separate natural-threshold burst exercises the auto-halve code path.
//   T3. Hot-survives-scan (WTLFU-04 literal): hot key accessed 20x, then 1000 unique
//       scan accesses, hot key still in cache (confirmed via access() returning true).
//   T4. Determinism: two back-to-back runs with the same input sequence produce
//       identical access()-hit-sequences (observable cache behavior is deterministic
//       given the same FNV-1a seed set and no wall-clock/RNG inputs).

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "count_min_sketch.h"
#include "cache.h"  // brings in CachePolicy + WTinyLFUCache (cache.h #includes wtinylfu.h)

// Global failure counter. We ACCUMULATE rather than abort-on-first so a single
// `make test` run surfaces every broken invariant. Exit code is
// (failures > 0) ? 1 : 0 per D-06.
static int failures = 0;

#define TEST_ASSERT(expr, test_name) do {                                      \
    if (!(expr)) {                                                             \
        std::fprintf(stderr, "FAIL: %s — assertion \"%s\" failed at %s:%d\n", \
                     (test_name), #expr, __FILE__, __LINE__);                  \
        ++failures;                                                            \
    }                                                                          \
} while (0)

// --------------------------------------------------------------------------
// T1: CMS basics — record N times; estimate within [N, min(N+3, COUNTER_MAX)].
// Uses a small N (N=10) that is well below the 4-bit saturation ceiling of 15,
// so the CONSERVATIVE update rule (WTLFU-01) produces an exact estimate of 10
// with overwhelming probability at width >= 1024.
// --------------------------------------------------------------------------
static void test_cms_basics() {
    const char* tn = "cms_basics";
    CountMinSketch cms(1024);
    // width is nextpow2 of the hint; hint=1024 is already a power of 2.
    TEST_ASSERT(cms.width() >= 1024, tn);
    TEST_ASSERT((cms.width() & (cms.width() - 1)) == 0, tn);

    const std::string key = "hot_key";
    const int N = 10;
    for (int i = 0; i < N; ++i) cms.record(key);
    uint32_t est = cms.estimate(key);
    // CMS is an OVER-estimate (min across rows). Tolerance +3 per D-05 first bullet;
    // saturate at COUNTER_MAX = 15.
    uint32_t lo = static_cast<uint32_t>(N);
    uint32_t hi = static_cast<uint32_t>(N + 3);
    if (hi > CountMinSketch::COUNTER_MAX) hi = CountMinSketch::COUNTER_MAX;
    TEST_ASSERT(est >= lo, tn);
    TEST_ASSERT(est <= hi, tn);

    // A never-recorded key should estimate 0 with very high probability — a
    // full 4-row collision with "hot_key" at width >= 1024 is astronomically
    // unlikely. This catches "estimate() returns garbage" regressions.
    uint32_t zero_est = cms.estimate("never_recorded_key_xyz");
    TEST_ASSERT(zero_est == 0, tn);

    std::fprintf(stderr, "PASS: %s\n", tn);
}

// --------------------------------------------------------------------------
// T2: CMS aging — force_age() immediately halves counters and resets sample_count.
// Also drives the natural aging threshold via a burst to confirm that code path
// is exercised (sample_count_ >= sample_size_ fires halve_all_).
// --------------------------------------------------------------------------
static void test_cms_aging() {
    const char* tn = "cms_aging";
    CountMinSketch cms(256);

    // Record "A" twice. Fresh sketch, no collisions at these counts, so estimate
    // should read as exactly 2. CONSERVATIVE update increments only min-rows, but
    // a fresh sketch has all rows at 0 -> all 4 rows get incremented -> min = 2.
    cms.record("A");
    cms.record("A");
    uint32_t before = cms.estimate("A");
    TEST_ASSERT(before >= 2, tn);
    TEST_ASSERT(before <= 2 + 3, tn);  // tolerance per D-05

    cms.force_age();  // test-only hook (D-10): halve all + reset sample_count_.

    uint32_t after = cms.estimate("A");
    TEST_ASSERT(after == before / 2, tn);  // 2 -> 1
    TEST_ASSERT(cms.sample_count() == 0, tn);

    // Drive the NATURAL aging trigger. sample_size = 10 * width * DEPTH.
    // After sample_size records, sample_count_ resets to 0 in halve_all_.
    // Record sample_size + 10 times; the burst crosses the threshold once,
    // leaving sample_count_ at 10 (strictly less than sample_size).
    cms.reset();
    const uint64_t ss = cms.sample_size();
    const uint64_t burst = ss + 10;
    for (uint64_t i = 0; i < burst; ++i) {
        cms.record("nat_key_" + std::to_string(i % 5000));
    }
    TEST_ASSERT(cms.sample_count() < ss, tn);
    TEST_ASSERT(cms.sample_count() == 10, tn);  // exact: burst - ss

    std::fprintf(stderr, "PASS: %s\n", tn);
}

// --------------------------------------------------------------------------
// T3: Hot-object-survives-scan (WTLFU-04 literal).
// Insert hot key, access 20x to establish CMS frequency, then access 1000 UNIQUE
// scan keys sequentially. Confirm hot key is still in cache by re-accessing it
// and observing a HIT (access() returns true).
// --------------------------------------------------------------------------
static void test_hot_survives_scan() {
    const char* tn = "hot_survives_scan";

    // Size cache so that 1000 unique objects CANNOT all fit but hot CAN.
    // Each object = 100 bytes; 1000 scan objects = 100,000 bytes total.
    // Cache = 20,000 bytes = room for ~200 objects. Scan overflows by 5x.
    // n_objects_hint matches the ~200 expected residents.
    const uint64_t cap_bytes  = 20000;
    const uint64_t obj_size   = 100;
    const uint64_t n_obj_hint = 200;
    WTinyLFUCache cache(cap_bytes, n_obj_hint);

    const std::string hot = "HOT";
    // 20 accesses to establish hot key's CMS frequency dominance.
    for (int i = 0; i < 20; ++i) {
        (void)cache.access(hot, obj_size);
    }

    // Sequential scan of 1000 UNIQUE keys. Each is a fresh miss. Under D-08e
    // strict `>` admission and CONSERVATIVE update, hot's CMS count (>=20)
    // dominates any scan key's count (1 each), so hot must remain resident.
    for (int i = 0; i < 1000; ++i) {
        std::string scan_key = "scan_" + std::to_string(i);
        (void)cache.access(scan_key, obj_size);
    }

    // Re-access hot. Must return true (hit). This IS the WTLFU-04 literal:
    // a 20-access hot object survives a 1000-access sequential scan.
    bool hot_hit = cache.access(hot, obj_size);
    TEST_ASSERT(hot_hit, tn);

    if (hot_hit) std::fprintf(stderr, "PASS: %s\n", tn);
}

// --------------------------------------------------------------------------
// T4: Determinism — two back-to-back identical runs produce identical
// access()-hit-sequences. FNV-1a seeds are hard-coded in hash_util.h; CMS and
// WTinyLFUCache contain no std::random_device, std::mt19937, or clock inputs.
// If a future refactor introduces non-determinism (e.g., RNG-based admission
// tiebreak), this test catches it.
// --------------------------------------------------------------------------
static void test_determinism() {
    const char* tn = "determinism";

    auto run_once = [](std::vector<bool>& hits) {
        WTinyLFUCache cache(20000, 200);
        const uint64_t obj_size = 100;
        // Mixed workload: a hot key every 5th access, unique scan keys otherwise.
        // 500 accesses is plenty to exercise window+probation+protected transitions.
        for (int i = 0; i < 500; ++i) {
            std::string key = (i % 5 == 0) ? "HOT" : ("scan_" + std::to_string(i));
            hits.push_back(cache.access(key, obj_size));
        }
    };

    std::vector<bool> run1;
    std::vector<bool> run2;
    run_once(run1);
    run_once(run2);

    TEST_ASSERT(run1.size() == run2.size(), tn);
    TEST_ASSERT(run1 == run2, tn);

    if (run1 == run2 && run1.size() == run2.size()) {
        std::fprintf(stderr, "PASS: %s\n", tn);
    }
}

int main() {
    std::fprintf(stderr, "=== W-TinyLFU + CountMinSketch test suite ===\n");
    test_cms_basics();
    test_cms_aging();
    test_hot_survives_scan();
    test_determinism();
    if (failures > 0) {
        std::fprintf(stderr, "\n%d test(s) FAILED.\n", failures);
        return 1;
    }
    std::fprintf(stderr, "\nAll tests PASSED.\n");
    return 0;
}
