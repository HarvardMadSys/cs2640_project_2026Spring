#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include "client.h"

static std::atomic<int> allocs{0};
static std::atomic<int> frees{0};

static void* talloc() {
    allocs.fetch_add(1, std::memory_order_relaxed);
    return std::malloc(8);
}

static void tfree(void* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    std::free(p);
}

struct leak_guard {
    ~leak_guard() { assert(frees.load() == allocs.load()); }
} guard;

extern "C" void entry(int) {
    constexpr int HW = 16;
    constexpr int N_THREADS = HW * 4;
    constexpr int OPS_PER_THREAD = 4'000;
    constexpr int N_KEYS = 32;

    char keys[N_KEYS][16];
    for (int i = 0; i < N_KEYS; ++i) {
        std::snprintf(keys[i], sizeof(keys[i]), "s:%d", i);
        sas::put<void, tfree>(keys[i], talloc());
    }

    std::atomic<bool> start{false};
    std::vector<std::thread> threads;
    threads.reserve(N_THREADS);

    for (int t = 0; t < N_THREADS; ++t) {
        threads.emplace_back([t, &keys, &start] {
            while (!start.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
            unsigned r = static_cast<unsigned>(t * 2654435761u);
            for (int j = 0; j < OPS_PER_THREAD; ++j) {
                r = r * 1664525u + 1013904223u;
                int k = (r >> 8) & (N_KEYS - 1);
                if ((r & 7u) == 0) {
                    sas::put<void, tfree>(keys[k], talloc());
                } else {
                    auto h = sas::get<void>(keys[k]);
                    if (h) {
                        assert(h.get() != nullptr);
                    }
                }
            }
        });
    }

    start.store(true, std::memory_order_release);
    for (auto& th : threads) {
        th.join();
    }

    for (int i = 0; i < N_KEYS; ++i) {
        sas::put<void, nullptr>(keys[i], nullptr);
    }
}
