#include "trace_gen.h"
#include <fstream>
#include <sstream>
#include <iostream>
#include <unordered_map>

// ==================== Zipf Generator ====================

ZipfGenerator::ZipfGenerator(uint64_t n, double alpha, uint64_t seed) : rng_(seed) {
    cdf_.resize(n);
    double sum = 0;
    for (uint64_t i = 0; i < n; i++) {
        sum += 1.0 / std::pow(i + 1, alpha);
    }
    double cumulative = 0;
    for (uint64_t i = 0; i < n; i++) {
        cumulative += (1.0 / std::pow(i + 1, alpha)) / sum;
        cdf_[i] = cumulative;
    }
}

uint64_t ZipfGenerator::next() {
    double r = dist_(rng_);
    auto it = std::lower_bound(cdf_.begin(), cdf_.end(), r);
    if (it == cdf_.end()) return cdf_.size() - 1;
    return it - cdf_.begin();
}

// ==================== Trace I/O ====================

std::vector<TraceEntry> load_trace(const std::string& filename) {
    std::vector<TraceEntry> trace;
    std::ifstream file(filename);
    if (!file.is_open()) {
        std::cerr << "Error: cannot open " << filename << "\n";
        return trace;
    }
    std::string line;
    std::getline(file, line); // skip header
    while (std::getline(file, line)) {
        std::istringstream iss(line);
        TraceEntry e;
        char delim;
        iss >> e.timestamp >> delim;
        std::getline(iss, e.key, ',');
        iss >> e.size;
        trace.push_back(e);
    }
    return trace;
}

std::vector<TraceEntry> generate_zipf_trace(uint64_t num_requests, uint64_t num_objects,
                                             double alpha, uint64_t seed,
                                             double size_mu, double size_sigma) {
    ZipfGenerator zipf(num_objects, alpha, seed);
    std::mt19937_64 size_rng(seed + 1);
    // Log-normal size distribution. Default (8.3, 1.5) gives median ~4KB and
    // a max/median ratio comparable to Court (~300×). Phase-7 D-22: size_mu /
    // size_sigma threaded through CLI for the (α × σ_size) 2D sweep that
    // produces the continuous decision-map heatmap (§11).
    std::lognormal_distribution<double> size_dist(size_mu, size_sigma);

    // Pre-generate one size per object so repeated accesses see the same size
    std::vector<uint64_t> obj_sizes(num_objects);
    for (uint64_t i = 0; i < num_objects; i++) {
        uint64_t sz = std::max((uint64_t)64, (uint64_t)size_dist(size_rng));
        obj_sizes[i] = std::min(sz, (uint64_t)10'000'000);
    }

    std::vector<TraceEntry> trace;
    trace.reserve(num_requests);
    for (uint64_t i = 0; i < num_requests; i++) {
        uint64_t obj = zipf.next();
        trace.push_back({i, "obj_" + std::to_string(obj), obj_sizes[obj]});
    }
    return trace;
}

// ==================== Replay with Zipf Popularity ====================

std::vector<std::pair<std::string, uint64_t>> prepare_objects(
        const std::vector<TraceEntry>& raw_trace, uint64_t seed) {
    // Extract unique objects, keeping the first-seen size per key.
    std::unordered_map<std::string, uint64_t> seen;
    std::vector<std::pair<std::string, uint64_t>> objects;
    for (auto& e : raw_trace) {
        if (!seen.count(e.key)) {
            seen[e.key] = e.size;
            objects.push_back({e.key, e.size});
        }
    }
    // Shuffle object order so Zipf ranking isn't tied to collection order.
    // Uses seed (not seed+1) — seed+1 is reserved for the Zipf RNG (D-10).
    std::mt19937_64 shuffle_rng(seed);
    std::shuffle(objects.begin(), objects.end(), shuffle_rng);
    return objects;
}

std::vector<TraceEntry> generate_replay_trace(
        const std::vector<std::pair<std::string, uint64_t>>& objects,
        uint64_t num_requests, double alpha, uint64_t seed) {
    ZipfGenerator zipf(objects.size(), alpha, seed + 1);
    std::vector<TraceEntry> trace;
    trace.reserve(num_requests);
    for (uint64_t i = 0; i < num_requests; i++) {
        uint64_t rank = zipf.next();
        trace.push_back({i, objects[rank].first, objects[rank].second});
    }
    return trace;
}

std::vector<TraceEntry> replay_zipf(const std::vector<TraceEntry>& real_trace,
                                     uint64_t num_requests, double alpha,
                                     uint64_t seed) {
    // Thin wrapper (D-11): preserves pre-refactor behavior including the
    // informational stdout line. New code that wants to share prepared
    // objects across an alpha sweep should call prepare_objects +
    // generate_replay_trace directly (see src/main.cpp alpha-sweep loop).
    auto objects = prepare_objects(real_trace, seed);
    std::cout << "Replay-Zipf: " << objects.size() << " unique objects from real trace, "
              << "generating " << num_requests << " accesses with alpha=" << alpha << "\n";
    return generate_replay_trace(objects, num_requests, alpha, seed);
}
