#pragma once
#include <vector>
#include <string>
#include <cstdint>
#include "cache.h"

struct WorkloadStats {
    double zipf_alpha;
    double one_hit_wonder_ratio;
    uint64_t unique_objects;
    uint64_t total_requests;
    double mean_size;
    double median_size;
    double p99_size;
};

// Estimate Zipf alpha via MLE following Clauset et al. (2009).
// Uses Newton's method on the log-likelihood gradient of the discrete
// power-law model: P(k) ~ k^{-alpha}, for ranks 1..n.
double estimate_zipf_alpha(const std::vector<TraceEntry>& trace);

// Fraction of unique keys that appear exactly once in the trailing
// window_frac portion of the trace.
double one_hit_wonder_ratio(const std::vector<TraceEntry>& trace, double window_frac);

// Compute summary statistics for the trace
WorkloadStats characterize(const std::vector<TraceEntry>& trace);
