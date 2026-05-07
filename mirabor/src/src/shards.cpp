#include "shards.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include "hash_util.h"

// ==================== Exact Stack Distances ====================
// O(n * unique_keys) — only use on small traces for validation.

std::map<uint64_t, uint64_t> exact_stack_distances(const std::vector<TraceEntry>& trace) {
    std::map<uint64_t, uint64_t> histogram;
    // last_seen[key] = index of last access
    std::unordered_map<std::string, uint64_t> last_seen;

    for (uint64_t i = 0; i < trace.size(); i++) {
        const auto& key = trace[i].key;
        auto it = last_seen.find(key);
        if (it == last_seen.end()) {
            // Cold miss: infinite stack distance
            histogram[UINT64_MAX]++;
        } else {
            // Count distinct keys accessed between last_seen and now
            uint64_t prev = it->second;
            std::unordered_set<std::string> between;
            for (uint64_t j = prev + 1; j < i; j++) {
                between.insert(trace[j].key);
            }
            histogram[between.size()]++;
        }
        last_seen[key] = i;
    }
    return histogram;
}

// ==================== MRC from Stack Distances ====================

std::vector<MRCPoint> mrc_from_stack_distances(const std::map<uint64_t, uint64_t>& sd_hist,
                                                uint64_t total_accesses,
                                                uint64_t max_cache_size,
                                                uint64_t num_points) {
    // Build CDF of stack distances: for cache size C, a hit occurs when
    // stack distance < C. Miss ratio = 1 - P(distance < C).
    //
    // Collect all finite distances and their counts, sorted by distance.
    std::vector<std::pair<uint64_t, uint64_t>> finite_entries;
    for (auto& [dist, count] : sd_hist) {
        if (dist == UINT64_MAX) {
            // Cold misses are included in the miss ratio (not cached at any size)
        } else {
            finite_entries.push_back({dist, count});
        }
    }
    // Already sorted by map ordering

    std::vector<MRCPoint> mrc;
    uint64_t step = std::max((uint64_t)1, max_cache_size / num_points);

    for (uint64_t cache_size = step; cache_size <= max_cache_size; cache_size += step) {
        // Hits = accesses with stack distance < cache_size (and not cold misses)
        uint64_t hits = 0;
        for (auto& [dist, count] : finite_entries) {
            if (dist < cache_size) {
                hits += count;
            } else {
                break; // sorted, no more hits
            }
        }
        double miss_ratio = 1.0 - (double)hits / total_accesses;
        mrc.push_back({cache_size, miss_ratio});
    }
    return mrc;
}

// ==================== SHARDS Implementation ====================

SHARDS::SHARDS(double rate) : rate_(rate) {
    // We sample when hash(key) % modulus < threshold.
    // For simplicity, use modulus = 10000 and threshold = rate * modulus.
    modulus_ = 10000;
    threshold_ = (uint64_t)(rate * modulus_);
    if (threshold_ == 0) threshold_ = 1; // at least 1
}

uint64_t SHARDS::hash_key(const std::string& key) const {
    return fnv1a_64(key);
}

void SHARDS::access(const std::string& key) {
    // Deterministic spatial sampling: only process if hash falls in sample
    uint64_t h = hash_key(key);
    if (h % modulus_ >= threshold_) return;

    total_sampled_++;
    logical_time_++;

    auto it = last_access_.find(key);
    if (it != last_access_.end()) {
        uint64_t prev_time = it->second;

        // Stack distance = number of distinct sampled keys accessed since prev_time
        // Using the ordered set: count elements in (prev_time, logical_time)
        auto lo = access_times_.upper_bound(prev_time);
        auto hi = access_times_.lower_bound(logical_time_);
        uint64_t sampled_distance = (uint64_t)std::distance(lo, hi);

        // Scale distance by 1/rate to estimate true stack distance
        uint64_t estimated_distance = (uint64_t)(sampled_distance / rate_);
        sd_hist_[estimated_distance]++;

        // Remove old time, insert new
        access_times_.erase(prev_time);
    } else {
        // Cold miss
        sd_hist_[UINT64_MAX]++;
    }

    last_access_[key] = logical_time_;
    access_times_.insert(logical_time_);
}

void SHARDS::process(const std::vector<TraceEntry>& trace) {
    for (auto& e : trace) {
        access(e.key);
    }
}

std::map<uint64_t, uint64_t> SHARDS::stack_distance_histogram() const {
    return sd_hist_;
}

std::vector<MRCPoint> SHARDS::build_mrc(uint64_t max_cache_size, uint64_t num_points) const {
    // Distances in sd_hist_ are already scaled by 1/rate (estimating true
    // stack distances). The counts are raw sampled counts. The miss ratio
    // is the fraction of sampled accesses that miss, which approximates
    // the true miss ratio — no need to scale the total.
    return mrc_from_stack_distances(sd_hist_, total_sampled_, max_cache_size, num_points);
}
