#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include "client.h"

static std::atomic<int> allocs{0};
static std::atomic<int> frees{0};

static int* talloc(int v) {
    allocs.fetch_add(1, std::memory_order_relaxed);
    auto* p = static_cast<int*>(std::malloc(sizeof(int)));
    *p = v;
    return p;
}

static void tfree(int* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    std::free(p);
}

struct leak_guard {
    ~leak_guard() { assert(frees.load() == allocs.load()); }
} guard;

extern "C" void entry(int) {
    constexpr int N_THREADS = 16;
    constexpr int N_KEYS_PER_THREAD = 4'000;

    std::vector<std::thread> writers;
    writers.reserve(N_THREADS);
    for (int t = 0; t < N_THREADS; ++t) {
        writers.emplace_back([t] {
            char key[32];
            for (int i = 0; i < N_KEYS_PER_THREAD; ++i) {
                std::snprintf(key, sizeof(key), "dj:%d:%d", t, i);
                sas::put<int, tfree>(key, talloc(t * N_KEYS_PER_THREAD + i));
            }
        });
    }
    for (auto& th : writers) {
        th.join();
    }

    for (int t = 0; t < N_THREADS; ++t) {
        for (int i = 0; i < N_KEYS_PER_THREAD; ++i) {
            char key[32];
            std::snprintf(key, sizeof(key), "dj:%d:%d", t, i);
            auto h = sas::get<int>(key);
            assert(h);
            assert(*h == t * N_KEYS_PER_THREAD + i);
        }
    }

    std::atomic<bool> stop{false};
    std::vector<std::thread> overwriters;
    overwriters.reserve(N_THREADS);
    for (int t = 0; t < N_THREADS; ++t) {
        overwriters.emplace_back([t, &stop] {
            char key[32];
            int round = 0;
            while (!stop.load(std::memory_order_relaxed)) {
                int i = round % N_KEYS_PER_THREAD;
                std::snprintf(key, sizeof(key), "dj:%d:%d", t, i);
                int v = -(t * N_KEYS_PER_THREAD + i + round);
                sas::put<int, tfree>(key, talloc(v));
                ++round;
                if (round >= N_KEYS_PER_THREAD) {
                    break;
                }
            }
        });
    }
    for (auto& th : overwriters) {
        th.join();
    }
    stop.store(true, std::memory_order_relaxed);

    for (int t = 0; t < N_THREADS; ++t) {
        for (int i = 0; i < N_KEYS_PER_THREAD; ++i) {
            char key[32];
            std::snprintf(key, sizeof(key), "dj:%d:%d", t, i);
            auto h = sas::get<int>(key);
            assert(h);
            int round = i;
            int expected = -(t * N_KEYS_PER_THREAD + i + round);
            assert(*h == expected);
        }
    }

    for (int t = 0; t < N_THREADS; ++t) {
        for (int i = 0; i < N_KEYS_PER_THREAD; ++i) {
            char key[32];
            std::snprintf(key, sizeof(key), "dj:%d:%d", t, i);
            sas::put<int, nullptr>(key, nullptr);
        }
    }
}
