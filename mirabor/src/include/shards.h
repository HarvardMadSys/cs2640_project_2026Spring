#pragma once
#include <vector>
#include <string>
#include <cstdint>
#include <unordered_map>
#include <map>
#include <set>
#include "cache.h"

// A single point on a miss-ratio curve: (cache_size_bytes, miss_ratio)
struct MRCPoint {
    uint64_t cache_size;
    double miss_ratio;
};

// Exact stack-distance histogram (O(n^2) — only for small traces).
// Returns a map from stack distance -> count. Distance UINT64_MAX = cold miss.
std::map<uint64_t, uint64_t> exact_stack_distances(const std::vector<TraceEntry>& trace);

// Build an exact MRC from a stack-distance histogram.
std::vector<MRCPoint> mrc_from_stack_distances(const std::map<uint64_t, uint64_t>& sd_hist,
                                                uint64_t total_accesses,
                                                uint64_t max_cache_size,
                                                uint64_t num_points = 100);

// SHARDS (Spatially Hashed Approximate Reuse Distance Sampling)
// Fixed-rate spatial sampling: an access is sampled iff
// hash(key) % (1/rate) == 0.  Sampled accesses are fed into an
// exact stack-distance tracker, then distances are scaled by 1/rate.
class SHARDS {
    double rate_;
    uint64_t threshold_;  // hash(key) must be < threshold to be sampled
    uint64_t modulus_;

    // Stack-distance tracking for sampled accesses (ordered set approach)
    // last_access_[key] = logical time of last sampled access
    std::unordered_map<std::string, uint64_t> last_access_;
    // Ordered set of access times for O(log n) distance queries
    std::set<uint64_t> access_times_;
    uint64_t logical_time_ = 0;

    // Histogram of sampled stack distances
    std::map<uint64_t, uint64_t> sd_hist_;
    uint64_t total_sampled_ = 0;

    uint64_t hash_key(const std::string& key) const;

public:
    // rate: sampling rate in [0,1], e.g. 0.01 for 1%
    explicit SHARDS(double rate);

    // Process one access
    void access(const std::string& key);

    // Process an entire trace
    void process(const std::vector<TraceEntry>& trace);

    // Get the (scaled) stack-distance histogram
    std::map<uint64_t, uint64_t> stack_distance_histogram() const;

    // Build an approximate MRC from sampled data
    std::vector<MRCPoint> build_mrc(uint64_t max_cache_size, uint64_t num_points = 100) const;

    double sampling_rate() const { return rate_; }
    uint64_t total_sampled() const { return total_sampled_; }
};
