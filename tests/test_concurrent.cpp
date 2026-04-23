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
        std::println("Concurrent test passed!");
    }
} guard;

static void drain(std::string_view key) {
    sas::put<void, nullptr>(key, nullptr);
}

extern "C" void entry(int) {
    {
        sas::put<void, tfree>("stable", talloc());

        std::vector<std::thread> readers;
        readers.reserve(16);
        for (int i = 0; i < 16; ++i) {
            readers.emplace_back([] {
                for (int j = 0; j < 5'000; ++j) {
                    auto h = sas::get<void>("stable");
                    assert(h);
                    assert(h.get() != nullptr);
                }
            });
        }
        for (auto& t : readers) {
            t.join();
        }
        drain("stable");
    }

    {
        sas::put<void, tfree>("rw", talloc());
        std::atomic<bool> stop{false};

        std::thread writer([&] {
            while (!stop.load(std::memory_order_relaxed)) {
                sas::put<void, tfree>("rw", talloc());
            }
        });

        std::vector<std::thread> readers;
        readers.reserve(8);
        for (int i = 0; i < 8; ++i) {
            readers.emplace_back([] {
                for (int j = 0; j < 5'000; ++j) {
                    auto h = sas::get<void>("rw");
                    (void)h;
                }
            });
        }
        for (auto& t : readers) {
            t.join();
        }
        stop.store(true, std::memory_order_relaxed);
        writer.join();
        drain("rw");
    }

    {
        constexpr int K = 16;
        char keys[K][32];
        for (int i = 0; i < K; ++i) {
            std::snprintf(keys[i], sizeof(keys[i]), "ind:%d", i);
            sas::put<void, tfree>(keys[i], talloc());
        }

        std::vector<std::thread> threads;
        threads.reserve(K);
        for (int i = 0; i < K; ++i) {
            threads.emplace_back([i, &keys] {
                for (int j = 0; j < 2'000; ++j) {
                    if (j % 4 == 0) {
                        sas::put<void, tfree>(keys[i], talloc());
                    } else {
                        auto h = sas::get<void>(keys[i]);
                        assert(h);
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

    {
        constexpr int NWRITERS = 4;
        constexpr int NREADERS = 12;
        sas::put<void, tfree>("shared", talloc());
        std::atomic<bool> stop{false};

        std::vector<std::thread> writers;
        writers.reserve(NWRITERS);
        for (int i = 0; i < NWRITERS; ++i) {
            writers.emplace_back([&] {
                while (!stop.load(std::memory_order_relaxed)) {
                    sas::put<void, tfree>("shared", talloc());
                }
            });
        }

        std::vector<std::thread> readers;
        readers.reserve(NREADERS);
        for (int i = 0; i < NREADERS; ++i) {
            readers.emplace_back([&] {
                for (int j = 0; j < 10'000; ++j) {
                    auto h = sas::get<void>("shared");
                    (void)h;
                }
            });
        }

        for (auto& t : readers) {
            t.join();
        }
        stop.store(true, std::memory_order_relaxed);
        for (auto& t : writers) {
            t.join();
        }
        drain("shared");
    }
}
