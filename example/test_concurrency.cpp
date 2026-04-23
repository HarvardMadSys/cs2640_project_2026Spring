#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <print>
#include <thread>
#include <vector>

#include "client.h"

static std::atomic<int> allocs{0};
static std::atomic<int> frees{0};

static void* tracked_alloc(size_t size = 8) {
    allocs.fetch_add(1, std::memory_order_relaxed);
    return std::malloc(size);
}

static void tracked_free(void* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    std::free(p);
}

static void drain(std::string_view key) {
    sas::put<void, nullptr>(key, nullptr);
}

struct leak_checker {
    ~leak_checker() {
        assert(frees.load() == allocs.load());
        std::println("Concurrency test passed!");
    }
} checker;

extern "C" void entry(int) {
    {
        sas::put<void, tracked_free>("tc:stable", tracked_alloc());
        std::vector<std::thread> threads;
        for (int i = 0; i < 16; ++i) {
            threads.emplace_back([] {
                for (int j = 0; j < 1'000; ++j) {
                    auto h = sas::get<void>("tc:stable");
                    assert(h);
                    assert(h.get() != nullptr);
                }
            });
        }
        for (auto& t : threads) {
            t.join();
        }
        drain("tc:stable");
    }
    assert(frees.load() <= allocs.load());

    {
        sas::put<void, tracked_free>("tc:rw", tracked_alloc());
        std::atomic<bool> stop{false};

        std::thread writer([&] {
            while (!stop.load(std::memory_order_relaxed)) {
                sas::put<void, tracked_free>("tc:rw", tracked_alloc());
            }
        });

        std::vector<std::thread> readers;
        for (int i = 0; i < 8; ++i) {
            readers.emplace_back([] {
                for (int j = 0; j < 1'000; ++j) {
                    auto h = sas::get<void>("tc:rw");
                    (void)h;
                }
            });
        }

        for (auto& t : readers) {
            t.join();
        }
        stop.store(true, std::memory_order_relaxed);
        writer.join();
        drain("tc:rw");
    }
    assert(frees.load() <= allocs.load());

    {
        sas::put<void, tracked_free>("tc:mw", tracked_alloc());

        std::vector<std::thread> threads;
        for (int i = 0; i < 8; ++i) {
            threads.emplace_back([] {
                for (int j = 0; j < 1'000; ++j) {
                    sas::put<void, tracked_free>("tc:mw", tracked_alloc());
                }
            });
        }
        for (auto& t : threads) {
            t.join();
        }
        drain("tc:mw");
    }
    assert(frees.load() <= allocs.load());

    {
        constexpr int K = 8;
        char keys[K][16];
        for (int i = 0; i < K; ++i) {
            std::snprintf(keys[i], sizeof(keys[i]), "tc:ind:%d", i);
            sas::put<void, tracked_free>(keys[i], tracked_alloc());
        }

        std::vector<std::thread> threads;
        for (int i = 0; i < K; ++i) {
            threads.emplace_back([i, &keys] {
                for (int j = 0; j < 1'000; ++j) {
                    if (j % 5 == 0) {
                        sas::put<void, tracked_free>(keys[i], tracked_alloc());
                    } else {
                        char key_buf[16];
                        std::snprintf(key_buf, sizeof(key_buf), "tc:ind:%d", i);
                        auto h = sas::get<void>(key_buf);
                        (void)h;
                    }
                }
            });
        }
        for (auto& t : threads) {
            t.join();
        }
        for (int i = 0; i < K; ++i) {
            drain(keys[i]);
        }
    }
    assert(frees.load() <= allocs.load());
}
