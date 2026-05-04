#pragma once

#include <array>
#include <barrier>
#include <cstddef>
#include <cstdio>
#include <format>
#include <iostream>
#include <random>
#include <sched.h>
#include <string>
#include <string_view>
#include <unistd.h>
#include <vector>

#include "zipfian_int_distribution.h"

namespace sas::bench {

inline void pin_to_cpu(int idx) {
    long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    if (ncpu <= 0) {
        return;
    }
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET((idx * 2) % int(ncpu), &set);
    sched_setaffinity(0, sizeof(set), &set);
}

struct bench_config {
    int num_threads = 8;
    int num_keys = 1 << 20;
    double read_ratio = 0.5;
    double zipf_theta = 0.99;
    int seed = 2640;
};

} // namespace sas::bench

template <> struct std::formatter<sas::bench::bench_config> {
    constexpr auto parse(std::format_parse_context& ctx) { return ctx.begin(); }
    auto format(const sas::bench::bench_config& cfg, auto& ctx) const {
        return std::format_to(ctx.out(),
                              "num_threads={} num_keys={} read_ratio={} "
                              "zipf_theta={} seed={}",
                              cfg.num_threads, cfg.num_keys, cfg.read_ratio,
                              cfg.zipf_theta, cfg.seed);
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
        } else if (key == "seed") {
            cfg.seed = std::stoi(val);
        }
    }
    return cfg;
}

struct steady_rng {
    uint32_t rng;
    uint32_t read_thresh;
    std::mt19937 mt;
    zipfian_int_distribution<int> zipf;

    steady_rng(const bench_config& cfg, int thread_index)
        : rng(static_cast<uint32_t>(thread_index + 1) * 2654435761u),
          read_thresh(static_cast<uint32_t>(cfg.read_ratio * 0xffffffffu)),
          mt(cfg.seed + thread_index),
          zipf(0, cfg.num_keys - 1, cfg.zipf_theta) {}

    uint32_t next_rng() {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        return rng;
    }

    bool is_read() { return next_rng() < read_thresh; }
    int next_key() { return zipf(mt); }
};

struct steady_workload {
    std::vector<std::array<char, 16>> raw;
    std::vector<std::string_view> sv;
    std::vector<int> values;
    std::barrier<> sync;

    template <typename PutFn>
    steady_workload(const bench_config& cfg, PutFn put)
        : raw(cfg.num_keys), sv(cfg.num_keys), values(cfg.num_keys),
          sync(cfg.num_threads) {
        for (int i = 0; i < cfg.num_keys; ++i) {
            int len = std::snprintf(raw[i].data(), 16, "k:%d", i);
            sv[i] = std::string_view(raw[i].data(), len);
            put(sv[i], &values[i]);
        }
    }
};

struct fill_partition {
    size_t start;
    size_t end;

    fill_partition(size_t total, int thread_index, int num_threads) {
        size_t per = total / num_threads;
        start = thread_index * per;
        end = (thread_index == num_threads - 1) ? total : start + per;
    }
};

struct fill_keys {
    std::vector<std::array<char, 16>> raw;
    std::vector<std::string_view> sv;
    std::vector<int> values;
    std::barrier<> sync;

    fill_keys(size_t n, int num_threads)
        : raw(n), sv(n), values(n), sync(num_threads) {
        for (size_t i = 0; i < n; ++i) {
            int len = std::snprintf(raw[i].data(), 16, "k:%zu", i);
            sv[i] = std::string_view(raw[i].data(), len);
        }
    }
};

} // namespace sas::bench
