// Standalone Doorkeeper Bloom-filter test binary (Phase 4 Plan 04-02).
// Pure C++17 assert-based per Phase 2 D-06 — no third-party test framework.
// Run: `make test` (builds build/test/test_doorkeeper alongside test_wtinylfu).
//
// Coverage:
//   T1. test_contains_after_add — fresh filter has no FPs on unseen keys;
//       add() then contains() always returns true; idempotent re-check.
//   T2. test_clear_zeros_all — after add()ing 100 keys and calling clear(),
//       all 100 added keys MUST report contains()==false (false negatives
//       are impossible after a clean clear regardless of FPR).
//   T3. test_fpr_sanity — at load = n_objects_hint, the empirical false-
//       positive rate on 10K disjoint queries falls within [0.05, 0.25].
//       The ~13% paper target is the centerline; the loose band absorbs
//       seed-to-seed noise. A regression to ~50% FPR (e.g., single-hash
//       bug, modulo collision, dead bit array) blows this gate.

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

// T1: contains-after-add — the fundamental Bloom filter invariant.
static void test_contains_after_add() {
    const char* tn = "contains_after_add";
    Doorkeeper dk(1024);
    // Fresh filter: NO key should be contained yet (sanity — bits all zero).
    TEST_ASSERT(!dk.contains("alpha"), tn);
    TEST_ASSERT(!dk.contains("beta"), tn);
    TEST_ASSERT(!dk.contains("gamma"), tn);

    // After add(): the key MUST be contained (no false negatives).
    dk.add("alpha");
    TEST_ASSERT(dk.contains("alpha"), tn);

    // Idempotent re-check: contains() does not consume / mutate.
    TEST_ASSERT(dk.contains("alpha"), tn);
    TEST_ASSERT(dk.contains("alpha"), tn);

    // Other keys should not have leaked into "contained" by adding "alpha"
    // (high probability — at size=4096 bits a single add() flips 2 bits;
    // an unseen key's 2 bits land elsewhere with probability >> 0.999).
    TEST_ASSERT(!dk.contains("beta_never_added_xyz"), tn);
    TEST_ASSERT(!dk.contains("gamma_never_added_uvw"), tn);

    // Add a second key and re-verify the first.
    dk.add("beta");
    TEST_ASSERT(dk.contains("beta"), tn);
    TEST_ASSERT(dk.contains("alpha"), tn);  // first key still there

    std::fprintf(stderr, "PASS: %s\n", tn);
}

// T2: clear() zeros all bits — added keys must NOT be contained after clear().
static void test_clear_zeros_all() {
    const char* tn = "clear_zeros_all";
    Doorkeeper dk(256);
    // Add 100 keys.
    for (int i = 0; i < 100; ++i) {
        dk.add("key_" + std::to_string(i));
    }
    // Sanity: a sample of the added keys are contained.
    TEST_ASSERT(dk.contains("key_0"), tn);
    TEST_ASSERT(dk.contains("key_50"), tn);
    TEST_ASSERT(dk.contains("key_99"), tn);

    // Clear and re-test: every previously-added key MUST now report
    // not-contained. (False positive on a fresh OTHER key after clear is
    // still possible at any time, but items that WERE added must be gone
    // because the bits they set are now zero.)
    dk.clear();
    int still_contained = 0;
    for (int i = 0; i < 100; ++i) {
        if (dk.contains("key_" + std::to_string(i))) {
            ++still_contained;
        }
    }
    TEST_ASSERT(still_contained == 0, tn);

    // Re-add one key after clear — should work normally (clear did not
    // corrupt internal state).
    dk.add("post_clear");
    TEST_ASSERT(dk.contains("post_clear"), tn);

    std::fprintf(stderr, "PASS: %s (post-clear contained=%d/100)\n",
                 tn, still_contained);
}

// T3: empirical FPR at the recommended load factor.
// Paper target: ~13% at 4 bits/element (D-06 / STACK.md §Doorkeeper).
// Tolerance: [0.05, 0.25] — generous enough to absorb seed/key-distribution
// variance without masking obvious regressions (single-hash bug → ~30-50%).
static void test_fpr_sanity() {
    const char* tn = "fpr_sanity";
    const uint64_t n = 10000;
    Doorkeeper dk(n);

    // Add n distinct "added_*" keys (load factor = n / n = 1.0 → 4 bits/elt).
    for (uint64_t i = 0; i < n; ++i) {
        dk.add("added_" + std::to_string(i));
    }

    // Query n disjoint "query_*" keys; count false positives.
    uint64_t fp = 0;
    for (uint64_t i = 0; i < n; ++i) {
        if (dk.contains("query_" + std::to_string(i))) {
            ++fp;
        }
    }
    const double fpr = static_cast<double>(fp) / static_cast<double>(n);

    // Sanity-only band; the exact target depends on hash quality and the
    // disjointness of the FNV-1a "added_" / "query_" hash distributions.
    TEST_ASSERT(fpr >= 0.05, tn);
    TEST_ASSERT(fpr <= 0.25, tn);

    std::fprintf(stderr, "PASS: %s (fp=%llu/%llu, fpr=%.4f, target ~0.13)\n",
                 tn,
                 static_cast<unsigned long long>(fp),
                 static_cast<unsigned long long>(n),
                 fpr);
}

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
