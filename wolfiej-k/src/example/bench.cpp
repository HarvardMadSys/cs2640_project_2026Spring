#include <algorithm>
#include <barrier>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <print>
#include <thread>
#include <vector>
#include <x86intrin.h>

#include "client.h"

static constexpr int NUM_THREADS = 8;
static constexpr int NUM_KEYS = 128;
static constexpr int WARMUP_OPS = 10'000;
static constexpr int OPS_PER_THREAD = 1'000'000;
static constexpr double READ_RATIO = 0.8;

static inline uint64_t rdtsc() {
    _mm_lfence();
    return __builtin_ia32_rdtsc();
}

static uint64_t clockns() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL +
           static_cast<uint64_t>(ts.tv_nsec);
}

static double calibrate_tsc() {
    uint64_t ns0 = clockns();
    uint64_t cy0 = rdtsc();
    while (clockns() - ns0 < 50'000'000ULL) {
    }
    uint64_t ns1 = clockns();
    uint64_t cy1 = rdtsc();
    return static_cast<double>(cy1 - cy0) / static_cast<double>(ns1 - ns0);
}

static double pct(std::vector<uint64_t>& v, double p) {
    return static_cast<double>(v[static_cast<size_t>((v.size() - 1) * p)]);
}

static double mean(std::vector<uint64_t>& v) {
    double s = 0;
    for (uint64_t x : v) {
        s += static_cast<double>(x);
    }
    return s / static_cast<double>(v.size());
}

struct alignas(64) thread_stats {
    std::vector<uint64_t> get_cy;
    std::vector<uint64_t> close_cy;
    std::vector<uint64_t> write_cy;
    uint64_t wall_cy{0};
};

extern "C" void entry(int) {
    double cy_per_ns = calibrate_tsc();

    static int dummy[NUM_KEYS];
    static char raw_keys[NUM_KEYS][16];
    static std::string_view sv_keys[NUM_KEYS];

    for (int i = 0; i < NUM_KEYS; ++i) {
        std::snprintf(raw_keys[i], sizeof(raw_keys[i]), "bench:%d", i);
        sv_keys[i] = raw_keys[i];
        sas::put<int, nullptr>(sv_keys[i], &dummy[i]);
    }

    std::barrier<> sync(NUM_THREADS + 1);
    std::vector<thread_stats> stats(NUM_THREADS);

    auto worker = [&](int tid) {
        auto& s = stats[tid];
        s.get_cy.reserve(OPS_PER_THREAD);
        s.close_cy.reserve(OPS_PER_THREAD);
        s.write_cy.reserve(OPS_PER_THREAD);

        uint32_t rng = static_cast<uint32_t>(tid + 1) * 2654435761u;
        auto next = [&] {
            rng ^= rng << 13;
            rng ^= rng >> 17;
            rng ^= rng << 5;
            return rng;
        };

        static constexpr uint32_t read_thresh =
            static_cast<uint32_t>(READ_RATIO * 0xffffffffu);

        for (int i = 0; i < WARMUP_OPS; ++i) {
            uint32_t r = next();
            int k = static_cast<int>(r % NUM_KEYS);
            if (r < read_thresh) {
                auto h = sas::get<int>(sv_keys[k]);
                (void)h;
            } else {
                sas::put<int, nullptr>(sv_keys[k], &dummy[k]);
            }
        }

        sync.arrive_and_wait();
        uint64_t t_start = rdtsc();

        for (int i = 0; i < OPS_PER_THREAD; ++i) {
            uint32_t r = next();
            int k = static_cast<int>(r % NUM_KEYS);
            if (r < read_thresh) {
                uint64_t t0, t1;
                {
                    t0 = rdtsc();
                    auto h = sas::get<int>(sv_keys[k]);
                    t1 = rdtsc();
                }
                uint64_t t2 = rdtsc();
                s.get_cy.push_back(t1 - t0);
                s.close_cy.push_back(t2 - t1);
            } else {
                uint64_t t0 = rdtsc();
                sas::put<int, nullptr>(sv_keys[k], &dummy[k]);
                s.write_cy.push_back(rdtsc() - t0);
            }
        }

        s.wall_cy = rdtsc() - t_start;
    };

    std::vector<std::thread> threads;
    threads.reserve(NUM_THREADS);
    for (int i = 0; i < NUM_THREADS; ++i) {
        threads.emplace_back(worker, i);
    }

    sync.arrive_and_wait();

    for (auto& t : threads) {
        t.join();
    }

    auto cy_to_ns = [&](std::vector<uint64_t>& cy) {
        std::vector<uint64_t> ns;
        ns.reserve(cy.size());
        for (uint64_t c : cy) {
            ns.push_back(
                static_cast<uint64_t>(static_cast<double>(c) / cy_per_ns));
        }
        return ns;
    };

    std::vector<uint64_t> all_ns, get_ns, close_ns, write_ns;
    uint64_t wall_cy = 0;

    for (auto& s : stats) {
        auto g = cy_to_ns(s.get_cy);
        auto c = cy_to_ns(s.close_cy);
        auto w = cy_to_ns(s.write_cy);
        get_ns.insert(get_ns.end(), g.begin(), g.end());
        close_ns.insert(close_ns.end(), c.begin(), c.end());
        write_ns.insert(write_ns.end(), w.begin(), w.end());
        wall_cy = std::max(wall_cy, s.wall_cy);
    }

    std::vector<uint64_t> read_ns;
    read_ns.reserve(get_ns.size());
    for (size_t i = 0; i < get_ns.size(); ++i) {
        read_ns.push_back(get_ns[i] + close_ns[i]);
    }

    all_ns = read_ns;
    all_ns.insert(all_ns.end(), write_ns.begin(), write_ns.end());

    for (auto* v : {&all_ns, &read_ns, &get_ns, &close_ns, &write_ns}) {
        std::sort(v->begin(), v->end());
    }

    double wall_s = static_cast<double>(wall_cy) / cy_per_ns / 1e9;
    double ops_per_s = static_cast<double>(all_ns.size()) / wall_s;

    auto print_lat = [](const char* label, std::vector<uint64_t>& v) {
        if (v.empty()) {
            return;
        }
        std::println("  {:<10} avg {:7.3f}   p90 {:7.3f}   p99 {:7.3f}  (µs)",
                     label, mean(v) / 1e3, pct(v, 0.90) / 1e3,
                     pct(v, 0.99) / 1e3);
    };

    std::println("--- bench: {} threads, {}% reads, {} keys ---", NUM_THREADS,
                 static_cast<int>(READ_RATIO * 100), NUM_KEYS);
    std::println("  ops:        {}", all_ns.size());
    std::println("  wall time:  {:.3f} s", wall_s);
    std::println("  throughput: {:.0f} ops/s", ops_per_s);
    std::println("  latency:");
    print_lat("all", all_ns);
    print_lat("reads", read_ns);
    print_lat("  get", get_ns);
    print_lat("  close", close_ns);
    print_lat("writes", write_ns);
}
