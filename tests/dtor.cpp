#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <thread>

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

static void flush_gc() {
    static int dummy;
    for (int i = 0; i < 65; ++i) {
        sas::put<int, nullptr>("__flush_dtor__", &dummy);
    }
}

static void wait_frees(int expected) {
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (frees.load(std::memory_order_acquire) < expected) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}

extern "C" void entry(int) {
    {
        static int dummy;
        sas::put<int, nullptr>("nd", &dummy);
        sas::put<int, nullptr>("nd", &dummy);
        sas::put<int, nullptr>("nd", &dummy);
    }

    {
        int base = frees.load();
        sas::put<void, tfree>("ow", talloc());
        sas::put<void, tfree>("ow", talloc());
        flush_gc();
        wait_frees(base + 1);
    }

    {
        int base = frees.load();
        sas::put<void, tfree>("rep", talloc());
        static int dummy;
        sas::put<int, nullptr>("rep", &dummy);
        flush_gc();
        wait_frees(base + 1);
    }

    {
        sas::put("uptr", std::make_unique<int>(123));
        {
            auto h = sas::get<int>("uptr");
            assert(h);
            assert(*h == 123);
        }
        static int dummy;
        sas::put<int, nullptr>("uptr", &dummy);
    }

    {
        constexpr int N = 200;
        int base_allocs = allocs.load();
        int base_frees = frees.load();
        for (int i = 0; i < N; ++i) {
            sas::put<void, tfree>("many", talloc());
        }
        flush_gc();
        wait_frees(base_frees + N - 1);
        assert(allocs.load() == base_allocs + N);
    }

    {
        int base = frees.load();
        sas::put<void, tfree>("hazard", talloc());
        {
            auto h = sas::get<void>("hazard");
            assert(h);
            sas::put<void, tfree>("hazard", talloc());
            flush_gc();
            assert(frees.load() == base);
        }
        flush_gc();
        wait_frees(base + 1);
    }
}
