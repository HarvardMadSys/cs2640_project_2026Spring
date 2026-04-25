#pragma once

#include <barrier>
#include <benchmark/benchmark.h>
#include <chrono>
#include <cmath>
#include <format>
#include <hdr/hdr_histogram.h>
#include <iostream>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>
#include <x86intrin.h>

#include "zipfian_int_distribution.h"

namespace sas::bench {

struct bench_config {
    int num_threads = 8;
    int num_keys = 128;
    double read_ratio = 0.8;
    double zipf_theta = 0.99;
    int num_epochs = 5;
    int seed = 2640;
};

} // namespace sas::bench

template <> struct std::formatter<sas::bench::bench_config> {
    constexpr auto parse(std::format_parse_context& ctx) { return ctx.begin(); }
    auto format(const sas::bench::bench_config& cfg,
                std::format_context& ctx) const {
        return std::format_to(ctx.out(),
                              "num_threads={} num_keys={} read_ratio={} "
                              "zipf_theta={} num_epochs={} seed={}",
                              cfg.num_threads, cfg.num_keys, cfg.read_ratio,
                              cfg.zipf_theta, cfg.num_epochs, cfg.seed);
    }
};

namespace sas::bench {

inline bench_config load_config(std::istream& is = std::cin) {
    bench_config cfg;
    std::string line;
    while (std::getline(is, line)) {
        auto pos = line.find('=');
        if (pos == std::string::npos || line.empty() || line[0] == '#') {
            continue;
        }
        std::string key = line.substr(0, pos);
        std::string val = line.substr(pos + 1);
        if (key == "num_threads") {
            cfg.num_threads = std::stoi(val);
        } else if (key == "num_keys") {
            cfg.num_keys = std::stoi(val);
        } else if (key == "read_ratio") {
            cfg.read_ratio = std::stod(val);
        } else if (key == "zipf_theta") {
            cfg.zipf_theta = std::stod(val);
        } else if (key == "num_epochs") {
            cfg.num_epochs = std::stoi(val);
        } else if (key == "seed") {
            cfg.seed = std::stoi(val);
        }
    }
    return cfg;
}

namespace {

inline uint64_t rdtsc_start() { return __rdtsc(); }

inline uint64_t rdtsc_end() {
    unsigned aux;
    return __rdtscp(&aux);
}

inline double calibrate_cycles() {
    using clock = std::chrono::high_resolution_clock;
    constexpr int CALIBRATE_MS = 200;

    auto t0 = clock::now();
    uint64_t c0 = rdtsc_start();
    std::this_thread::sleep_for(std::chrono::milliseconds(CALIBRATE_MS));
    auto t1 = clock::now();
    uint64_t c1 = rdtsc_end();

    double ns = std::chrono::duration<double, std::nano>(t1 - t0).count();
    return (c1 - c0) / ns;
}

template <typename GetFn, typename PutFn>
void throughput_workload(benchmark::State& state, const bench_config& cfg,
                         GetFn get, PutFn put) {
    static std::vector<std::array<char, 16>> raw_keys;
    static std::vector<std::string_view> sv_keys;
    static std::vector<int> values;
    static std::once_flag init_flag;
    static std::unique_ptr<std::barrier<>> sync;

    std::call_once(init_flag, [&]() {
        raw_keys.resize(cfg.num_keys);
        sv_keys.resize(cfg.num_keys);
        values.resize(cfg.num_keys);
        for (int i = 0; i < cfg.num_keys; ++i) {
            int len = std::snprintf(raw_keys[i].data(), 16, "bench:%d", i);
            sv_keys[i] = std::string_view(raw_keys[i].data(), len);
            put(sv_keys[i], &values[i]);
        }
        sync = std::make_unique<std::barrier<>>(cfg.num_threads);
    });

    uint32_t rng =
        static_cast<uint32_t>(state.thread_index() + 1) * 2654435761u;
    auto next_rng = [&]() -> uint32_t {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        return rng;
    };

    uint32_t read_thresh = static_cast<uint32_t>(cfg.read_ratio * 0xffffffffu);

    std::mt19937 rng_mt(cfg.seed + state.thread_index());
    zipfian_int_distribution<int> zipf(0, cfg.num_keys - 1, cfg.zipf_theta);

    for (int i = 0; i < 10000; ++i) {
        int k = zipf(rng_mt);
        if (next_rng() < read_thresh) {
            auto _ = get(sv_keys[k]);
        } else {
            put(sv_keys[k], &values[k]);
        }
    }

    constexpr int PRESAMPLE_SIZE = 1 << 16;
    std::vector<std::pair<int, bool>> presample(PRESAMPLE_SIZE);
    for (auto& [k, r] : presample) {
        k = zipf(rng_mt);
        r = (next_rng() < read_thresh);
    }

    int64_t local_reads = 0;
    int64_t local_writes = 0;
    uint32_t presample_idx = 0;

    for (auto _ : state) {
        const auto& [k, r] = presample[presample_idx++ & (PRESAMPLE_SIZE - 1)];
        if (r) {
            ++local_reads;
            auto _ = get(sv_keys[k]);
        } else {
            ++local_writes;
            put(sv_keys[k], &values[k]);
        }
    }

    state.counters["rd_ops"] = benchmark::Counter(
        static_cast<double>(local_reads), benchmark::Counter::kIsRate);
    state.counters["wr_ops"] = benchmark::Counter(
        static_cast<double>(local_writes), benchmark::Counter::kIsRate);
    state.SetItemsProcessed(state.iterations());
}

template <typename GetFn, typename PutFn>
void latency_workload(benchmark::State& state, const bench_config& cfg,
                      GetFn get, PutFn put) {
    static std::vector<std::array<char, 16>> raw_keys;
    static std::vector<std::string_view> sv_keys;
    static std::vector<int> values;
    static std::once_flag init_flag;
    static double cycles_per_ns;
    static hdr_histogram* global_read_hist = nullptr;
    static hdr_histogram* global_write_hist = nullptr;
    static std::mutex hist_mutex;
    static std::unique_ptr<std::barrier<>> sync;

    std::call_once(init_flag, [&]() {
        raw_keys.resize(cfg.num_keys);
        sv_keys.resize(cfg.num_keys);
        values.resize(cfg.num_keys);
        for (int i = 0; i < cfg.num_keys; ++i) {
            int len = std::snprintf(raw_keys[i].data(), 16, "bench:%d", i);
            sv_keys[i] = std::string_view(raw_keys[i].data(), len);
            put(sv_keys[i], &values[i]);
        }

        cycles_per_ns = calibrate_cycles();
        int64_t max_cycles =
            static_cast<int64_t>(1'000'000'000ULL * cycles_per_ns);
        hdr_init(1, max_cycles, 3, &global_read_hist);
        hdr_init(1, max_cycles, 3, &global_write_hist);
        sync = std::make_unique<std::barrier<>>(cfg.num_threads);
    });

    if (state.thread_index() == 0) {
        std::lock_guard<std::mutex> lock(hist_mutex);
        if (global_read_hist) {
            hdr_reset(global_read_hist);
        }
        if (global_write_hist) {
            hdr_reset(global_write_hist);
        }
    }
    sync->arrive_and_wait();

    uint32_t rng =
        static_cast<uint32_t>(state.thread_index() + 1) * 2654435761u;
    auto next_rng = [&]() -> uint32_t {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        return rng;
    };

    uint32_t read_thresh = static_cast<uint32_t>(cfg.read_ratio * 0xffffffffu);

    std::mt19937 rng_mt(cfg.seed + state.thread_index());
    zipfian_int_distribution<int> zipf(0, cfg.num_keys - 1, cfg.zipf_theta);

    hdr_histogram* local_read_hist;
    hdr_histogram* local_write_hist;
    int64_t max_cycles = static_cast<int64_t>(1'000'000'000ULL * cycles_per_ns);
    hdr_init(1, max_cycles, 3, &local_read_hist);
    hdr_init(1, max_cycles, 3, &local_write_hist);

    for (int i = 0; i < 10000; ++i) {
        int k = zipf(rng_mt);
        if (next_rng() < read_thresh) {
            auto _ = get(sv_keys[k]);
        } else {
            put(sv_keys[k], &values[k]);
        }
    }

    for (auto _ : state) {
        int k = zipf(rng_mt);
        bool is_write = (next_rng() >= read_thresh);
        if (is_write) {
            uint64_t t0 = rdtsc_start();
            put(sv_keys[k], &values[k]);
            uint64_t t1 = rdtsc_end();
            hdr_record_value(local_write_hist, t1 - t0);
        } else {
            uint64_t t0 = rdtsc_start();
            {
                auto _ = get(sv_keys[k]);
            }
            uint64_t t1 = rdtsc_end();
            hdr_record_value(local_read_hist, t1 - t0);
        }
    }

    {
        std::lock_guard<std::mutex> lock(hist_mutex);
        hdr_add(global_read_hist, local_read_hist);
        hdr_add(global_write_hist, local_write_hist);
    }
    hdr_close(local_read_hist);
    hdr_close(local_write_hist);
    sync->arrive_and_wait();

    auto rd_pct = [](double p) {
        if (global_read_hist->total_count == 0) {
            return benchmark::Counter(0);
        }
        double cycles = hdr_value_at_percentile(global_read_hist, p);
        return benchmark::Counter(cycles / cycles_per_ns,
                                  benchmark::Counter::kAvgThreads);
    };

    auto wr_pct = [](double p) {
        if (global_write_hist->total_count == 0) {
            return benchmark::Counter(0);
        }
        double cycles = hdr_value_at_percentile(global_write_hist, p);
        return benchmark::Counter(cycles / cycles_per_ns,
                                  benchmark::Counter::kAvgThreads);
    };

    state.counters["rd_p050_ns"] = rd_pct(50.0);
    state.counters["rd_p090_ns"] = rd_pct(90.0);
    state.counters["rd_p099_ns"] = rd_pct(99.0);
    state.counters["rd_p999_ns"] = rd_pct(99.9);
    state.counters["wr_p050_ns"] = wr_pct(50.0);
    state.counters["wr_p090_ns"] = wr_pct(90.0);
    state.counters["wr_p099_ns"] = wr_pct(99.0);
    state.counters["wr_p999_ns"] = wr_pct(99.9);

    sync->arrive_and_wait();
}

} // namespace

template <typename GetFn, typename PutFn>
void register_benchmarks(const bench_config& cfg, const std::string& label,
                         GetFn get, PutFn put) {
    benchmark::RegisterBenchmark(label + "_throughput",
                                 [cfg, get, put](benchmark::State& state) {
                                     throughput_workload(state, cfg, get, put);
                                 })
        ->Threads(cfg.num_threads)
        ->UseRealTime();
    benchmark::RegisterBenchmark(label + "_latency",
                                 [cfg, get, put](benchmark::State& state) {
                                     latency_workload(state, cfg, get, put);
                                 })
        ->Threads(cfg.num_threads)
        ->UseRealTime();
}

void run_benchmarks() {
    std::vector<const char*> argv = {
        "bench",
        "--benchmark_counters_tabular=true",
        "--benchmark_time_unit=ns",
    };
    int argc = static_cast<int>(argv.size());
    benchmark::Initialize(&argc, const_cast<char**>(argv.data()));
    benchmark::RunSpecifiedBenchmarks();
    benchmark::Shutdown();
}

} // namespace sas::bench
