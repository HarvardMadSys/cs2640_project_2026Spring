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
    return std::malloc(sizeof(int));
}

static void tfree(int* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    std::free(p);
}

struct leak_guard {
    ~leak_guard() { assert(frees.load() == allocs.load()); }
} guard;

static void flush_gc() {
    static int dummy;
    for (int i = 0; i < 512; ++i) {
        sas::put<int, nullptr>("__flush_gc__", &dummy);
    }
}

static void wait_frees(int expected) {
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (frees.load(std::memory_order_acquire) < expected) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}

extern "C" void entry(int) {
    {
        int base_frees = frees.load();
        sas::put<int, tfree>("retention", static_cast<int*>(talloc()));

        {
            auto h1 = sas::get<int>("retention");
            assert(h1);
            sas::put<int, tfree>("retention", static_cast<int*>(talloc()));
            auto h2 = sas::get<int>("retention");
            assert(h2);
            assert(h1.get() != h2.get());
            sas::put<int, nullptr>("retention", nullptr);
            flush_gc();
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            assert(frees.load() == base_frees);
        }

        wait_frees(base_frees + 2);
    }

    {
        constexpr int N_THREADS = 16;
        constexpr int N_WRITES = 1000;

        int base_frees = frees.load();
        std::vector<std::thread> herd;
        std::atomic<bool> start_flag{false};

        for (int i = 0; i < N_THREADS; ++i) {
            herd.emplace_back([&start_flag] {
                while (!start_flag.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                for (int j = 0; j < N_WRITES; ++j) {
                    sas::put<int, tfree>("stampede",
                                         static_cast<int*>(talloc()));
                }
            });
        }

        start_flag.store(true, std::memory_order_release);
        for (auto& t : herd) {
            t.join();
        }

        flush_gc();

        int expected_frees = (N_THREADS * N_WRITES) - 1;
        wait_frees(base_frees + expected_frees);

        sas::put<int, nullptr>("stampede", nullptr);
        flush_gc();
        wait_frees(base_frees + expected_frees + 1);
    }

    {
        constexpr int N_ITERS = 50000;
        std::atomic<bool> stop{false};
        int local_allocs = 0;

        std::thread toggler([&] {
            for (int i = 0; i < N_ITERS; ++i) {
                if (i % 2 == 0) {
                    sas::put<int, tfree>("toggle", static_cast<int*>(talloc()));
                    local_allocs++;
                } else {
                    sas::put<int, nullptr>("toggle", nullptr);
                }
            }
            stop.store(true, std::memory_order_relaxed);
        });

        std::thread reader([&] {
            long successful_reads = 0;
            long null_reads = 0;
            while (!stop.load(std::memory_order_relaxed)) {
                auto h = sas::poll("toggle");
                if (h) {
                    successful_reads++;
                } else {
                    null_reads++;
                }
            }
            volatile long total = successful_reads + null_reads;
            (void)total;
        });

        toggler.join();
        reader.join();

        sas::put<int, nullptr>("toggle", nullptr);
    }

    flush_gc();
}
