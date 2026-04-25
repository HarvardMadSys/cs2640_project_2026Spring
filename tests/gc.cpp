#include <atomic>
#include <cassert>
#include <chrono>
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
    ~leak_guard() {
        assert(frees.load() == allocs.load());
    }
} guard;

static void wait_frees(int expected) {
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (frees.load(std::memory_order_acquire) < expected) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}

static void flush_gc() {
    static int dummy;
    for (int i = 0; i < 257; ++i) {
        sas::put<int, nullptr>("__flush_gc__", &dummy);
    }
}

extern "C" void entry(int) {
    {
        constexpr int N = 2000;
        int base = frees.load();
        for (int i = 0; i < N; ++i) {
            sas::put<void, tfree>("stress", talloc());
        }
        flush_gc();
        wait_frees(base + N - 1);
    }

    {
        constexpr int K = 100;
        constexpr int OVERWRITES = 10;
        int base_frees = frees.load();
        int base_allocs = allocs.load();
        char key[32];
        for (int i = 0; i < K; ++i) {
            std::snprintf(key, sizeof(key), "multi:%d", i);
            for (int j = 0; j < OVERWRITES; ++j) {
                sas::put<void, tfree>(key, talloc());
            }
        }
        flush_gc();
        wait_frees(base_frees + K * (OVERWRITES - 1));
        assert(allocs.load() == base_allocs + K * OVERWRITES);
    }

    {
        int base_frees = frees.load();
        int base_allocs = allocs.load();

        std::thread t([&] {
            for (int i = 0; i < 30; ++i) {
                sas::put<void, tfree>("partial", talloc());
            }
        });
        t.join();

        flush_gc();
        wait_frees(base_frees + 29);
        assert(allocs.load() == base_allocs + 30);
    }

    {
        constexpr int NTHREADS = 8;
        constexpr int WRITES = 500;
        int base_frees = frees.load();
        int base_allocs = allocs.load();

        std::vector<std::thread> threads;
        threads.reserve(NTHREADS);
        for (int i = 0; i < NTHREADS; ++i) {
            threads.emplace_back([i] {
                char key[32];
                std::snprintf(key, sizeof(key), "tgc:%d", i);
                for (int j = 0; j < WRITES; ++j) {
                    sas::put<void, tfree>(key, talloc());
                }
            });
        }
        for (auto& t : threads) {
            t.join();
        }

        flush_gc();
        wait_frees(base_frees + NTHREADS * (WRITES - 1));
        assert(allocs.load() == base_allocs + NTHREADS * WRITES);
    }
}
