#pragma once

#include <atomic>
#include <barrier>
#include <cmath>
#include <format>
#include <iostream>
#include <print>
#include <string>
#include <thread>
#include <vector>

#include "client.h"
#include "nanobench.h"
#include "zipfian_int_distribution.h"

namespace sas::bench {

struct bench_config {
    int num_threads = 8;
    int num_keys = 128;
    int ops_per_thread = 1'000'000;
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
        return std::format_to(
            ctx.out(),
            "num_threads={} num_keys={} ops_per_thread={} "
            "read_ratio={} zipf_theta={} num_epochs={} seed={}",
            cfg.num_threads, cfg.num_keys, cfg.ops_per_thread, cfg.read_ratio,
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
        } else if (key == "ops_per_thread") {
            cfg.ops_per_thread = std::stoi(val);
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

constexpr int WARMUP_OPS = 10'000;

inline ankerl::nanobench::Bench make_bench(const bench_config& cfg) {
    ankerl::nanobench::Bench bench;
    bench.title("benchmark")
        .unit("op")
        .epochs(cfg.num_epochs)
        .performanceCounters(true)
        .output(nullptr)
        .context("threads", std::to_string(cfg.num_threads))
        .context("keys", std::to_string(cfg.num_keys))
        .context("zipf_theta", std::to_string(cfg.zipf_theta))
        .context("read_ratio", std::to_string(cfg.read_ratio));
    return bench;
}

template <typename GetFn, typename PutFn>
inline void run_benchmark(const bench_config& cfg, GetFn get, PutFn put,
                          ankerl::nanobench::Bench& bench,
                          const std::string& label) {
    std::vector<std::string> keys(cfg.num_keys);
    std::vector<int> values(cfg.num_keys);
    for (int i = 0; i < cfg.num_keys; ++i) {
        keys[i] = "bench:" + std::to_string(i);
        put(keys[i], &values[i]);
    }

    zipfian_int_distribution<int>::param_type z_param(0, cfg.num_keys - 1,
                                                      cfg.zipf_theta);

    {
        std::barrier<> sync(cfg.num_threads + 1);
        std::atomic<bool> shutdown{false};

        auto worker = [&](int tid) {
            zipfian_int_distribution<int> zipf(z_param);
            ankerl::nanobench::Rng rng(cfg.seed + tid);

            auto get_or_put = [&]() {
                int k = zipf(rng);
                if (rng.uniform01() < cfg.read_ratio) {
                    auto h = get(keys[k]);
                    ankerl::nanobench::doNotOptimizeAway(h);
                } else {
                    put(keys[k], &values[k]);
                }
            };

            for (int i = 0; i < WARMUP_OPS; ++i) {
                get_or_put();
            }

            while (true) {
                sync.arrive_and_wait();
                if (shutdown.load(std::memory_order_acquire)) {
                    break;
                }
                for (int i = 0; i < cfg.ops_per_thread; ++i) {
                    get_or_put();
                }
                sync.arrive_and_wait();
            }
        };

        std::vector<std::thread> threads;
        threads.reserve(cfg.num_threads);
        for (int i = 0; i < cfg.num_threads; ++i) {
            threads.emplace_back(worker, i);
        }

        bench.batch(static_cast<double>(cfg.num_threads) * cfg.ops_per_thread)
            .epochIterations(1)
            .minEpochIterations(1);

        bench.run(label, [&] {
            sync.arrive_and_wait();
            sync.arrive_and_wait();
        });

        shutdown.store(true, std::memory_order_release);
        sync.arrive_and_wait();
        for (auto& t : threads) {
            t.join();
        }
    }

    {
        std::atomic<bool> shutdown{false};

        auto bg_worker = [&](int tid) {
            zipfian_int_distribution<int> zipf(z_param);
            ankerl::nanobench::Rng rng(cfg.seed + tid + 1);

            for (int i = 0; i < WARMUP_OPS; ++i) {
                int k = zipf(rng);
                if (rng.uniform01() < cfg.read_ratio) {
                    auto h = get(keys[k]);
                    ankerl::nanobench::doNotOptimizeAway(h);
                } else {
                    put(keys[k], &values[k]);
                }
            }

            while (!shutdown.load(std::memory_order_relaxed)) {
                int k = zipf(rng);
                if (rng.uniform01() < cfg.read_ratio) {
                    auto h = get(keys[k]);
                    ankerl::nanobench::doNotOptimizeAway(h);
                } else {
                    put(keys[k], &values[k]);
                }
            }
        };

        std::vector<std::thread> bg_threads;
        bg_threads.reserve(cfg.num_threads - 1);
        for (int i = 0; i < cfg.num_threads - 1; ++i) {
            bg_threads.emplace_back(bg_worker, i);
        }

        zipfian_int_distribution<int> zipf(z_param);
        ankerl::nanobench::Rng rng(cfg.seed);
        int value = 0;

        for (int i = 0; i < WARMUP_OPS; ++i) {
            int k = zipf(rng);
            if (rng.uniform01() < cfg.read_ratio) {
                auto h = get(keys[k]);
                ankerl::nanobench::doNotOptimizeAway(h);
            } else {
                put(keys[k], &value);
            }
        }

        bench.batch(cfg.ops_per_thread)
            .epochIterations(0)
            .minEpochIterations(10);

        bench.run(label + "/get", [&] {
            for (int i = 0; i < cfg.ops_per_thread; ++i) {
                int k = zipf(rng);
                auto h = get(keys[k]);
                ankerl::nanobench::doNotOptimizeAway(h);
            }
        });

        bench.run(label + "/put", [&] {
            for (int i = 0; i < cfg.ops_per_thread; ++i) {
                int k = zipf(rng);
                put(keys[k], &value);
                ankerl::nanobench::doNotOptimizeAway(value);
            }
        });

        shutdown.store(true, std::memory_order_release);
        for (auto& t : bg_threads) {
            t.join();
        }
    }
}

} // namespace sas::bench
