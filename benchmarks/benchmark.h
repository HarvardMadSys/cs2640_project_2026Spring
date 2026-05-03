#pragma once

#include <algorithm>
#include <array>
#include <benchmark/benchmark.h>
#include <cstdint>
#include <hdr/hdr_histogram.h>
#include <memory>
#include <string>
#include <utility>

#include "timing.h"
#include "workload.h"

namespace sas::bench {

namespace {

constexpr int BATCH_OPS = 128;
constexpr int WARMUP_OPS = 10000;

template <typename GetFn, typename PutFn>
void warmup(steady_workload& workload, steady_rng& rng, GetFn get, PutFn put) {
    for (int i = 0; i < WARMUP_OPS; ++i) {
        int k = rng.next_key();
        if (rng.is_read()) {
            auto _ = get(workload.sv[k]);
        } else {
            put(workload.sv[k], &workload.values[k]);
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
    }
    workload.sync.arrive_and_wait();

    steady_rng rng(cfg, state.thread_index());
    hdr_histogram* local_read = new_local_hist();
    hdr_histogram* local_write = new_local_hist();

    warmup(workload, rng, get, put);

    std::array<int, BATCH_OPS> idx;
    int64_t total_ops = 0;
    for (auto _ : state) {
        for (int j = 0; j < BATCH_OPS; ++j) {
            idx[j] = rng.next_key();
        }
        if (!rng.is_read()) {
            uint64_t t0 = rdtsc_start();
            for (int j = 0; j < BATCH_OPS; ++j) {
                put(workload.sv[idx[j]], &workload.values[idx[j]]);
            }
            uint64_t t1 = rdtsc_end();
            insert_local_hist(local_write, (t1 - t0) / BATCH_OPS);
        } else {
            uint64_t t0 = rdtsc_start();
            for (int j = 0; j < BATCH_OPS; ++j) {
                auto _ = get(workload.sv[idx[j]]);
            }
            uint64_t t1 = rdtsc_end();
            insert_local_hist(local_read, (t1 - t0) / BATCH_OPS);
        }
        total_ops += BATCH_OPS;
    }

    read_hist.merge(local_read);
    write_hist.merge(local_write);
    workload.sync.arrive_and_wait();

    read_hist.report(state, "rd_");
    write_hist.report(state, "wr_");
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
    }
    keys.sync.arrive_and_wait();

    fill_partition p(keys.sv.size(), state.thread_index(), cfg.num_threads);
    hdr_histogram* local_write = new_local_hist();
    int64_t total_ops = 0;

    for (auto _ : state) {
        state.PauseTiming();
        if (state.thread_index() == 0) {
            create(initial_capacity);
        }
        keys.sync.arrive_and_wait();
        state.ResumeTiming();

        for (size_t i = p.start; i < p.end; i += BATCH_OPS) {
            size_t e = std::min(i + BATCH_OPS, p.end);
            size_t bs = e - i;
            uint64_t t0 = rdtsc_start();
            for (size_t j = i; j < e; ++j) {
                put(keys.sv[j], &keys.values[j]);
            }
            uint64_t t1 = rdtsc_end();
            insert_local_hist(local_write, (t1 - t0) / bs);
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
    auto keys = std::make_shared<fill_keys>(num_inserts, cfg.num_threads);
    auto write_hist = std::make_shared<latency_hist>();
    register_threaded(label, cfg,
                      [cfg, initial_capacity, keys, write_hist, create, destroy,
                       put](benchmark::State& state) {
                          fill_benchmark(state, cfg, initial_capacity, *keys,
                                         *write_hist, create, destroy, put);
                      });
}

inline void run_benchmarks() {
    static char prog[] = "bench";
    std::vector<char*> argv = {
        prog,
        const_cast<char*>("--benchmark_counters_tabular=true"),
        const_cast<char*>("--benchmark_time_unit=ns"),
        const_cast<char*>("--benchmark_min_time=5s"),
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
