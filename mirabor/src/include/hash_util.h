#pragma once
#include <cstdint>
#include <string>

// ==================== FNV-1a 64-bit ====================
// Deterministic across compilers and libstdc++ versions. Replaces the banned
// std::hash<std::string>, which is implementation-defined. See D-12/D-14 in
// .planning/phases/01-enabling-refactors-courtlistener-pilot/01-CONTEXT.md.

constexpr uint64_t FNV_BASIS = 14695981039346656037ULL;
constexpr uint64_t FNV_PRIME = 1099511628211ULL;

// Four golden-ratio-derived 64-bit seeds. Consumed by Phase 2's Count-Min
// Sketch as 4 pseudo-independent hash seeds (depth=4). Values chosen from
// well-known avalanche constants (splitmix64, murmur3 finalizer family).
constexpr uint64_t FNV_SEED_A = 0x9e3779b97f4a7c15ULL;
constexpr uint64_t FNV_SEED_B = 0xbf58476d1ce4e5b9ULL;
constexpr uint64_t FNV_SEED_C = 0x94d049bb133111ebULL;
constexpr uint64_t FNV_SEED_D = 0xda942042e4dd58b5ULL;

// FNV-1a 64-bit. Seed defaults to FNV_BASIS so fnv1a_64(s) matches the
// canonical FNV-1a output (identical to the pre-refactor SHARDS::hash_key).
inline uint64_t fnv1a_64(const std::string& s, uint64_t seed = FNV_BASIS) {
    uint64_t hash = seed;
    for (char c : s) {
        hash ^= (uint64_t)(unsigned char)c;
        hash *= FNV_PRIME;
    }
    return hash;
}

// Startup self-test. Asserts fnv1a_64("hello") matches the published
// FNV-1a-64 vector 0xa430d84680aabd0b. Callers should invoke once at
// program start and abort on false. Caller style: see src/main.cpp.
inline bool hash_util_self_test() {
    return fnv1a_64("hello") == 0xa430d84680aabd0bULL;
}
