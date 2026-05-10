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

static void tfree(int* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    std::free(p);
}

struct leak_guard {
    ~leak_guard() { assert(frees.load() == allocs.load()); }
} guard;

static void drain(std::string_view key) {
    sas::put<void, nullptr>(key, nullptr);
}

extern "C" void entry(int) {
    constexpr int NUM_ITEMS = 100'000;
    constexpr int NUM_WRITERS = 8;
    constexpr int NUM_READERS = 8;
    constexpr int ITEMS_PER_WRITER = NUM_ITEMS / NUM_WRITERS;

    std::atomic<bool> stop_readers{false};

    {
        void* ptr = talloc();
        *static_cast<int*>(ptr) = 42;
        sas::put<int, tfree>("stable_key", static_cast<int*>(ptr));
    }

    std::vector<std::thread> readers;
    readers.reserve(NUM_READERS);
    for (int i = 0; i < NUM_READERS; ++i) {
        readers.emplace_back([&] {
            while (!stop_readers.load(std::memory_order_relaxed)) {
                auto h = sas::get<int>("stable_key");
                assert(h);
                assert(*h == 42);
            }
        });
    }

    std::vector<std::thread> writers;
    writers.reserve(NUM_WRITERS);
    for (int i = 0; i < NUM_WRITERS; ++i) {
        writers.emplace_back([i] {
            int start = i * ITEMS_PER_WRITER;
            int end = start + ITEMS_PER_WRITER;
            char key[32];
            for (int j = start; j < end; ++j) {
                std::snprintf(key, sizeof(key), "rz:%d", j);
                void* ptr = talloc();
                *static_cast<int*>(ptr) = j;
                sas::put<int, tfree>(key, static_cast<int*>(ptr));
            }
        });
    }

    for (auto& t : writers) {
        t.join();
    }

    stop_readers.store(true, std::memory_order_relaxed);
    for (auto& t : readers) {
        t.join();
    }

    for (int i = 0; i < NUM_ITEMS; ++i) {
        char key[32];
        std::snprintf(key, sizeof(key), "rz:%d", i);
        auto h = sas::get<int>(key);
        assert(h);
        assert(*h == i);
    }

    drain("stable_key");
    for (int i = 0; i < NUM_ITEMS; ++i) {
        char key[32];
        std::snprintf(key, sizeof(key), "rz:%d", i);
        drain(key);
    }
}
