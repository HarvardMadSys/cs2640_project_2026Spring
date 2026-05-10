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

template <typename Op> inline void time_op(hdr_histogram* hist, Op&& op) {
    uint64_t t0 = rdtsc_start();
    op();
    uint64_t t1 = rdtsc_end();
    insert_local_hist(hist, t1 - t0);
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

    warmup(cfg.warmup_secs, workload, rng, get, put);

    int64_t total_ops = 0;
    for (auto _ : state) {
        int key = rng.next_key();
        if (!rng.is_read()) {
            time_op(local_write,
                    [&] { put(workload.sv[key], &workload.values[key]); });
        } else {
            time_op(local_read, [&] { auto _ = get(workload.sv[key]); });
        }
        total_ops++;
    }

    read_hist.merge(local_read);
    write_hist.merge(local_write);
    workload.sync.arrive_and_wait();

    read_hist.report(state, "rd_");
    write_hist.report(state, "wr_");
    state.counters["peak_rss_mb"] =
        benchmark::Counter(peak_rss_mb(), benchmark::Counter::kAvgThreads);
    state.SetItemsProcessed(total_ops);
    workload.sync.arrive_and_wait();
}

template <typename PutFn>
void fill_benchmark(benchmark::State& state, const bench_config& cfg,
                    steady_workload& keys, latency_hist& write_hist,
                    PutFn put) {
    if (state.thread_index() == 0) {
        write_hist.reset_locked();
        reset_peak_rss();
    }
    keys.sync.arrive_and_wait();

    fill_partition p(keys.sv.size(), state.thread_index(), cfg.num_threads);
    hdr_histogram* local_write = new_local_hist();
    int64_t total_ops = 0;

    for (auto _ : state) {
        for (size_t i = p.start; i < p.end; ++i) {
            time_op(local_write, [&] { put(keys.sv[i], &keys.values[i]); });
        }
        total_ops += static_cast<int64_t>(p.end - p.start);
    }

    write_hist.merge(local_write);
    keys.sync.arrive_and_wait();

    write_hist.report(state, "wr_");
    state.counters["peak_rss_mb"] =
        benchmark::Counter(peak_rss_mb(), benchmark::Counter::kAvgThreads);
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

template <typename PutFn>
void register_fill(const bench_config& cfg, const std::string& label,
                   std::shared_ptr<steady_workload> keys, PutFn put) {
    auto write_hist = std::make_shared<latency_hist>();
    benchmark::RegisterBenchmark(
        label,
        [cfg, keys, write_hist, put](benchmark::State& state) {
            fill_benchmark(state, cfg, *keys, *write_hist, put);
        })
        ->Threads(cfg.num_threads)
        ->UseRealTime()
        ->Iterations(1);
}

template <typename GetFn, typename PutFn>
void register_mixed(const bench_config& cfg, const std::string& label,
                    std::shared_ptr<steady_workload> keys, GetFn get,
                    PutFn put) {
    auto read_hist = std::make_shared<latency_hist>();
    auto write_hist = std::make_shared<latency_hist>();
    register_threaded(label, cfg,
                      [cfg, keys, read_hist, write_hist, get,
                       put](benchmark::State& state) {
                          mixed_benchmark(state, cfg, *keys, *read_hist,
                                          *write_hist, get, put);
                      });
}

} // namespace

template <typename GetFn, typename PutFn>
void run_benchmarks(const bench_config& cfg, const std::string& label,
                    GetFn get, PutFn put) {
    auto keys = std::make_shared<steady_workload>(cfg.num_keys,
                                                  cfg.num_threads);
    register_fill(cfg, label + "_fill", keys, put);
    register_mixed(cfg, label, keys, get, put);

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
