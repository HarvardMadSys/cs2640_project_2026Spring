#pragma once
#include <vector>
#include <string>
#include <cstdint>
#include <random>
#include <algorithm>
#include <cmath>
#include "cache.h"

// Zipf distribution generator using inverse CDF sampling
class ZipfGenerator {
    std::vector<double> cdf_;
    std::mt19937_64 rng_;
    std::uniform_real_distribution<double> dist_{0.0, 1.0};

public:
    ZipfGenerator(uint64_t n, double alpha, uint64_t seed = 42);
    uint64_t next();
};

// Load a trace CSV (timestamp,key,size) with a one-line header
std::vector<TraceEntry> load_trace(const std::string& filename);

// Generate a synthetic Zipf-distributed trace with log-normal object sizes.
// size_mu and size_sigma parameterize the underlying lognormal_distribution
// (μ and σ of the underlying normal distribution). Defaults preserve the
// historical (8.3, 1.5) parameterization used in Phase 1–6.
std::vector<TraceEntry> generate_zipf_trace(uint64_t num_requests, uint64_t num_objects,
                                             double alpha, uint64_t seed = 42,
                                             double size_mu = 8.3,
                                             double size_sigma = 1.5);

// Replay real keys/sizes from a trace with Zipf-distributed popularity.
// Extracts unique (key, size) pairs from the input trace, ranks them
// arbitrarily, and generates num_requests accesses where the probability
// of accessing rank-k object follows Zipf(alpha).
std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed = 42);

// Dedupe a real trace into unique (key, size) pairs and shuffle
// deterministically using std::mt19937_64(seed). Call ONCE per raw trace;
// the returned object list is reused across alpha values in replay_zipf-based
// alpha sweeps to avoid O(N) re-dedup per sweep cell (REFACTOR-02, D-10).
std::vector<std::pair<std::string, uint64_t>> prepare_objects(
        const std::vector<TraceEntry>& raw_trace, uint64_t seed = 42);

// Sample num_requests accesses from a prepared object list, where the
// probability of accessing rank-k object follows Zipf(alpha). Uses
// ZipfGenerator(objects.size(), alpha, seed + 1) to preserve the legacy
// replay_zipf seeding contract (shuffle uses seed, Zipf uses seed+1).
std::vector<TraceEntry> generate_replay_trace(
        const std::vector<std::pair<std::string, uint64_t>>& objects,
        uint64_t num_requests, double alpha, uint64_t seed = 42);
