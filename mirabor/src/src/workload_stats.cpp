#include "workload_stats.h"
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <numeric>
#include <cmath>
#include <vector>

// ==================== Zipf Alpha Estimation (MLE) ====================
// Clauset et al. (2009) MLE for discrete power-law distributions.
//
// Given observed frequencies f_1 >= f_2 >= ... >= f_n sorted by rank,
// we treat each rank-frequency pair as f_i observations of rank i.
// The discrete power-law probability is P(k) = k^{-alpha} / H(n, alpha)
// where H(n,alpha) = sum_{k=1}^{n} k^{-alpha} is the generalized
// harmonic number.
//
// Log-likelihood: L(alpha) = -alpha * sum_i(f_i * ln(i)) - N * ln(H(n,alpha))
// where N = sum(f_i).
//
// We find alpha* by Newton's method on dL/dalpha = 0.

static double generalized_harmonic(int n, double alpha) {
    double h = 0;
    for (int k = 1; k <= n; k++) h += std::pow(k, -alpha);
    return h;
}

static double generalized_harmonic_deriv(int n, double alpha) {
    // d/dalpha H(n, alpha) = -sum_{k=1}^{n} k^{-alpha} * ln(k)
    double d = 0;
    for (int k = 2; k <= n; k++) d -= std::pow(k, -alpha) * std::log(k);
    return d;
}

static double generalized_harmonic_deriv2(int n, double alpha) {
    // d^2/dalpha^2 H(n, alpha) = sum_{k=1}^{n} k^{-alpha} * (ln k)^2
    double d = 0;
    for (int k = 2; k <= n; k++) {
        double lk = std::log(k);
        d += std::pow(k, -alpha) * lk * lk;
    }
    return d;
}

double estimate_zipf_alpha(const std::vector<TraceEntry>& trace) {
    // Frequency count
    std::unordered_map<std::string, uint64_t> freq;
    for (auto& e : trace) freq[e.key]++;

    // Sort frequencies descending
    std::vector<uint64_t> freqs;
    for (auto& [k, v] : freq) freqs.push_back(v);
    std::sort(freqs.begin(), freqs.end(), std::greater<>());

    int n = (int)freqs.size();
    if (n < 2) return 0.0;

    // Use top ranks (cap at 2000 for speed)
    int n_fit = std::min(n, 2000);

    // Total observations across fitted ranks
    double N = 0;
    double sum_fi_lni = 0; // sum of f_i * ln(i)
    for (int i = 0; i < n_fit; i++) {
        N += freqs[i];
        if (i > 0) sum_fi_lni += freqs[i] * std::log(i + 1);
    }

    // Newton's method starting from alpha = 1.0
    double alpha = 1.0;
    for (int iter = 0; iter < 50; iter++) {
        double H  = generalized_harmonic(n_fit, alpha);
        double Hp = generalized_harmonic_deriv(n_fit, alpha);
        double Hpp = generalized_harmonic_deriv2(n_fit, alpha);

        // dL/dalpha = -sum_fi_lni - N * Hp / H
        double grad = -sum_fi_lni - N * Hp / H;

        // d2L/dalpha2 = -N * (Hpp * H - Hp^2) / H^2
        double hess = -N * (Hpp * H - Hp * Hp) / (H * H);

        if (std::abs(hess) < 1e-15) break;

        double step = grad / hess;
        alpha -= step;

        // Keep alpha in a reasonable range
        alpha = std::max(0.01, std::min(alpha, 5.0));

        if (std::abs(step) < 1e-8) break;
    }

    return alpha;
}

// ==================== One-Hit-Wonder Ratio ====================

double one_hit_wonder_ratio(const std::vector<TraceEntry>& trace, double window_frac) {
    uint64_t window = std::max((uint64_t)1, (uint64_t)(trace.size() * window_frac));
    uint64_t start = trace.size() > window ? trace.size() - window : 0;

    std::unordered_map<std::string, uint64_t> freq;
    for (uint64_t i = start; i < trace.size(); i++) {
        freq[trace[i].key]++;
    }
    uint64_t one_hits = 0;
    for (auto& [k, v] : freq) {
        if (v == 1) one_hits++;
    }
    return freq.empty() ? 0.0 : (double)one_hits / freq.size();
}

// ==================== Workload Characterization ====================

WorkloadStats characterize(const std::vector<TraceEntry>& trace) {
    WorkloadStats ws;
    ws.total_requests = trace.size();

    std::unordered_set<std::string> uniq;
    std::vector<uint64_t> sizes;
    for (auto& e : trace) {
        uniq.insert(e.key);
        sizes.push_back(e.size);
    }
    ws.unique_objects = uniq.size();
    ws.zipf_alpha = estimate_zipf_alpha(trace);
    ws.one_hit_wonder_ratio = one_hit_wonder_ratio(trace, 0.1);

    std::sort(sizes.begin(), sizes.end());
    double sum = std::accumulate(sizes.begin(), sizes.end(), 0.0);
    ws.mean_size = sum / sizes.size();
    ws.median_size = sizes[sizes.size() / 2];
    ws.p99_size = sizes[(uint64_t)(sizes.size() * 0.99)];

    return ws;
}
