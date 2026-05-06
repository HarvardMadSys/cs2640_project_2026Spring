#pragma once

#include <benchmark/benchmark.h>
#include <chrono>
#include <cstdint>
#include <format>
#include <hdr/hdr_histogram.h>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "memory.h"
#include "timing.h"
#include "workload.h"

namespace sas::bench {

namespace {

constexpr int WARMUP_BATCH = 1024;
constexpr int LATENCY_SAMPLE_OPS = 64;

template <typename Op>
inline void maybe_time(int& counter, hdr_histogram* hist, Op&& op) {
    if (++counter >= LATENCY_SAMPLE_OPS) {
        counter = 0;
        uint64_t t0 = rdtsc_start();
        op();
        uint64_t t1 = rdtsc_end();
        insert_local_hist(hist, t1 - t0);
    } else {
        op();
    }
}

template <typename GetFn, typename PutFn>
void warmup(int warmup_secs, steady_workload& workload, steady_rng& rng,
            GetFn get, PutFn put) {
    auto deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(warmup_secs);
    while (std::chrono::steady_clock::now() < deadline) {
        for (int i = 0; i < WARMUP_BATCH; ++i) {
            int k = rng.next_key();
            if (rng.is_read()) {
                auto _ = get(workload.sv[k]);
            } else {
                put(workload.sv[k], &workload.values[k]);
            }
        }
    }
}

template <typename GetFn, typename PutFn>
void mixed_benchmark(benchmark::State& state, const bench_config& cfg,
                     steady_workload& workload, latency_hist& read_hist,
                     latency_hist& write_hist, GetFn get, PutFn put) {
    if (state.thread_index() == 0) {
        read_hist.reset_locked();
        write_hist.reset_locked();
        reset_peak_rss();
    }
    workload.sync.arrive_and_wait();

    steady_rng rng(cfg, state.thread_index());
    hdr_histogram* local_read = new_local_hist();
    hdr_histogram* local_write = new_local_hist();
    int read_sample = 0;
    int write_sample = 0;

    warmup(cfg.warmup_secs, workload, rng, get, put);

    int64_t total_ops = 0;
    for (auto _ : state) {
        int key = rng.next_key();
        if (!rng.is_read()) {
            maybe_time(write_sample, local_write,
                       [&] { put(workload.sv[key], &workload.values[key]); });
        } else {
            maybe_time(read_sample, local_read,
                       [&] { auto _ = get(workload.sv[key]); });
        }
        total_ops++;
    }

    read_hist.merge(local_read);
    write_hist.merge(local_write);
    workload.sync.arrive_and_wait();

    read_hist.report(state, "rd_");
    write_hist.report(state, "wr_");
    state.counters["peak_rss_mb"] = peak_rss_mb();
    state.SetItemsProcessed(total_ops);
    workload.sync.arrive_and_wait();
}

template <typename CreateFn, typename DestroyFn, typename PutFn>
void fill_benchmark(benchmark::State& state, const bench_config& cfg,
                    size_t initial_capacity, fill_keys& keys,
                    latency_hist& write_hist, CreateFn create,
                    DestroyFn destroy, PutFn put) {
    if (state.thread_index() == 0) {
        write_hist.reset_locked();
        reset_peak_rss();
    }
    keys.sync.arrive_and_wait();

    fill_partition p(keys.sv.size(), state.thread_index(), cfg.num_threads);
    hdr_histogram* local_write = new_local_hist();
    int write_sample = 0;
    int64_t total_ops = 0;

    for (auto _ : state) {
        state.PauseTiming();
        if (state.thread_index() == 0) {
            create(initial_capacity);
        }
        keys.sync.arrive_and_wait();
        state.ResumeTiming();

        for (size_t i = p.start; i < p.end; ++i) {
            maybe_time(write_sample, local_write,
                       [&] { put(keys.sv[i], &keys.values[i]); });
        }
        total_ops += static_cast<int64_t>(p.end - p.start);

        state.PauseTiming();
        keys.sync.arrive_and_wait();
        if (state.thread_index() == 0) {
            destroy();
        }
        keys.sync.arrive_and_wait();
        state.ResumeTiming();
    }

    write_hist.merge(local_write);
    keys.sync.arrive_and_wait();

    write_hist.report(state, "wr_");
    state.counters["peak_rss_mb"] = peak_rss_mb();
    state.SetItemsProcessed(total_ops);
    keys.sync.arrive_and_wait();
}

template <typename Workload>
void register_threaded(const std::string& name, const bench_config& cfg,
                       Workload&& workload) {
    benchmark::RegisterBenchmark(name, std::forward<Workload>(workload))
        ->Threads(cfg.num_threads)
        ->UseRealTime();
}

} // namespace

template <typename GetFn, typename PutFn>
void register_mixed(const bench_config& cfg, const std::string& label,
                    GetFn get, PutFn put) {
    auto workload = std::make_shared<steady_workload>(cfg, put);
    auto read_hist = std::make_shared<latency_hist>();
    auto write_hist = std::make_shared<latency_hist>();
    register_threaded(label, cfg,
                      [cfg, workload, read_hist, write_hist, get,
                       put](benchmark::State& state) {
                          mixed_benchmark(state, cfg, *workload, *read_hist,
                                          *write_hist, get, put);
                      });
}

template <typename CreateFn, typename DestroyFn, typename PutFn>
void register_fill(const bench_config& cfg, const std::string& label,
                   size_t initial_capacity, size_t num_inserts, CreateFn create,
                   DestroyFn destroy, PutFn put) {
    if (!cfg.run_fill) {
        return;
    }
    auto keys = std::make_shared<fill_keys>(num_inserts, cfg.num_threads);
    auto write_hist = std::make_shared<latency_hist>();
    register_threaded(label, cfg,
                      [cfg, initial_capacity, keys, write_hist, create, destroy,
                       put](benchmark::State& state) {
                          fill_benchmark(state, cfg, initial_capacity, *keys,
                                         *write_hist, create, destroy, put);
                      });
}

inline void run_benchmarks(const bench_config& cfg) {
    static char prog[] = "bench";
    std::string min_time_arg =
        std::format("--benchmark_min_time={}s", cfg.bench_secs);
    std::vector<char*> argv = {
        prog,
        const_cast<char*>("--benchmark_counters_tabular=true"),
        const_cast<char*>("--benchmark_time_unit=ns"),
        min_time_arg.data(),
        const_cast<char*>("--benchmark_out=/dev/stdout"),
        const_cast<char*>("--benchmark_out_format=json"),
    };
    int argc = static_cast<int>(argv.size());
    benchmark::Initialize(&argc, argv.data());

    benchmark::ConsoleReporter console;
    console.SetOutputStream(&std::cerr);

    benchmark::RunSpecifiedBenchmarks(&console, nullptr);
    benchmark::Shutdown();
}

} // namespace sas::bench
