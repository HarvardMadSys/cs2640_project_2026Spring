#pragma once

#include <benchmark/benchmark.h>
#include <chrono>
#include <cstdint>
#include <hdr/hdr_histogram.h>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <x86intrin.h>

namespace sas::bench {

inline uint64_t rdtsc_start() {
    _mm_lfence();
    return __rdtsc();
}

inline uint64_t rdtsc_end() {
    uint64_t t = __rdtsc();
    _mm_lfence();
    return t;
}

inline double cycles_per_ns() {
    static const double cached = [] {
        using clock = std::chrono::high_resolution_clock;
        auto t0 = clock::now();
        uint64_t c0 = rdtsc_start();
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        auto t1 = clock::now();
        uint64_t c1 = rdtsc_end();
        double ns = std::chrono::duration<double, std::nano>(t1 - t0).count();
        return (c1 - c0) / ns;
    }();
    return cached;
}

inline hdr_histogram* new_local_hist() {
    hdr_histogram* h;
    int64_t max_cycles =
        static_cast<int64_t>(1'000'000'000ULL * cycles_per_ns());
    hdr_init(1, max_cycles, 3, &h);
    return h;
}

inline void insert_local_hist(hdr_histogram* hist, uint64_t cycles) {
    hdr_record_value(hist, cycles);
}

struct latency_hist {
    hdr_histogram* hist{nullptr};
    std::mutex mtx;

    latency_hist() { hist = new_local_hist(); }
    ~latency_hist() {
        if (hist) {
            hdr_close(hist);
        }
    }

    void reset_locked() {
        std::unique_lock lock(mtx);
        hdr_reset(hist);
    }

    void merge(hdr_histogram* local) {
        {
            std::unique_lock lock(mtx);
            hdr_add(hist, local);
        }
        hdr_close(local);
    }

    benchmark::Counter pct(double p) const {
        double v = hist->total_count == 0
                       ? 0.0
                       : hdr_value_at_percentile(hist, p) / cycles_per_ns();
        return benchmark::Counter(v, benchmark::Counter::kAvgThreads);
    }

    void report(benchmark::State& state, std::string_view prefix) const {
        std::string pre(prefix);
        state.counters[pre + "p50"] = pct(50.0);
        state.counters[pre + "p99"] = pct(99.0);
        state.counters[pre + "p999"] = pct(99.9);
    }
};

} // namespace sas::bench
