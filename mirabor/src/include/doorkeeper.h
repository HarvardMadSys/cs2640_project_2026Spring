#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <algorithm>
#include "hash_util.h"

// Doorkeeper Bloom filter for TinyLFU pre-CMS record filtering (D-05).
//
// Paper: Einziger, Friedman, Manes "TinyLFU: A Highly Efficient Cache
// Admission Policy" (ACM TOS 2017) §4.3 — the Doorkeeper absorbs first-time
// touches so that one-hit-wonder traffic does not bias the CMS frequency
// counters. Sizing per .planning/research/STACK.md §Doorkeeper: 4 bits per
// element gives ≈13% false-positive rate at the recommended load factor.
//
// Hash scheme per CONTEXT.md D-07: Kirsch-Mitzenmacher double-hashing with
// FNV-1a seeds A and B from include/hash_util.h. Two hash functions (k=2)
// are sufficient for first-time filtering; we are not building a high-
// precision hash set.
//
// Reset cadence: synchronized with the CountMinSketch aging cadence via the
// CMS on_age callback registered by WTinyLFUCache (D-09). The Doorkeeper
// itself has no internal counter — it is reset by the outer cache only.
//
// STATS SINGLE-SOURCE INVARIANT (L-12 from Phase 2): Doorkeeper is a sub-
// component of WTinyLFUCache. It does NOT inherit from CachePolicy and does
// NOT touch any `stats` member. Grep-enforced:
//   grep -cE "record\(\s*(true|false)" include/doorkeeper.h  ==  0
//
// Header-only — no .cpp companion (Phase 2 L-1: "no new external deps").

class Doorkeeper {
public:
    // D-06: bit-array length = 4 × n_objects_hint (Einziger-Friedman 4 bits/element).
    // n_objects_hint is the same value already threaded to CountMinSketch via
    // make_policy(..., n_obj_hint) — so Doorkeeper sizing automatically tracks
    // the effective cache capacity.
    explicit Doorkeeper(uint64_t n_objects_hint) {
        size_ = 4ULL * std::max<uint64_t>(n_objects_hint, 1);
        // Pack bits into uint64_t words; round up so size_ bits always fit.
        bits_.assign(static_cast<size_t>((size_ + 63) / 64), 0);
    }

    // D-05/D-07: returns true iff both Kirsch-Mitzenmacher bits are set.
    // False negatives are impossible (a key just add()-ed always tests true).
    // False positives are bounded by the FPR formula at load factor.
    bool contains(const std::string& key) const {
        const uint64_t h1 = fnv1a_64(key, FNV_SEED_A);
        const uint64_t h2 = fnv1a_64(key, FNV_SEED_B);
        for (uint64_t i = 0; i < 2; ++i) {
            const uint64_t bit = (h1 + i * h2) % size_;
            if (!((bits_[bit >> 6] >> (bit & 63)) & 1ULL)) return false;
        }
        return true;
    }

    // D-05/D-07: set both Kirsch-Mitzenmacher bits.
    void add(const std::string& key) {
        const uint64_t h1 = fnv1a_64(key, FNV_SEED_A);
        const uint64_t h2 = fnv1a_64(key, FNV_SEED_B);
        for (uint64_t i = 0; i < 2; ++i) {
            const uint64_t bit = (h1 + i * h2) % size_;
            bits_[bit >> 6] |= (1ULL << (bit & 63));
        }
    }

    // D-09: zeroes all bits. Called from WTinyLFUCache when the CMS ages,
    // so the DK and CMS freshness windows stay aligned.
    void clear() {
        std::fill(bits_.begin(), bits_.end(), static_cast<uint64_t>(0));
    }

    // Test-only inspector (mirrors CountMinSketch::width()).
    uint64_t size() const { return size_; }

private:
    uint64_t size_;
    std::vector<uint64_t> bits_;
};
