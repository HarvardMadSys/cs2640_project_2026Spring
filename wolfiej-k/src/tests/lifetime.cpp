#include <atomic>
#include <cassert>
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
    {
        constexpr int N_OVERWRITES = 50'000;

        sas::put<int, tfree>("anchor", talloc(7));
        auto h = sas::get<int>("anchor");
        assert(h);
        int* observed = h.get();
        assert(*observed == 7);

        std::thread writer([] {
            for (int i = 0; i < N_OVERWRITES; ++i) {
                sas::put<int, tfree>("anchor", talloc(i));
            }
        });
        writer.join();

        assert(h);
        assert(h.get() == observed);
        assert(*observed == 7);
    }

    {
        constexpr int N_READERS = 16;
        constexpr int N_WRITES = 5'000;

        sas::put<int, tfree>("multiref", talloc(100));

        std::vector<std::thread> readers;
        readers.reserve(N_READERS);
        std::atomic<bool> ready{false};
        std::atomic<int> ready_count{0};

        for (int i = 0; i < N_READERS; ++i) {
            readers.emplace_back([&] {
                auto h = sas::get<int>("multiref");
                assert(h);
                int observed = *h;
                ready_count.fetch_add(1, std::memory_order_release);
                while (!ready.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                for (int j = 0; j < 1000; ++j) {
                    assert(*h == observed);
                }
            });
        }

        while (ready_count.load(std::memory_order_acquire) < N_READERS) {
            std::this_thread::yield();
        }

        std::thread writer([] {
            for (int i = 0; i < N_WRITES; ++i) {
                sas::put<int, tfree>("multiref", talloc(i));
            }
        });
        ready.store(true, std::memory_order_release);

        writer.join();
        for (auto& t : readers) {
            t.join();
        }
    }

    {
        constexpr int DEPTH = 64;
        sas::put<int, tfree>("nested", talloc(-1));

        auto base = sas::get<int>("nested");
        assert(base);
        assert(*base == -1);

        std::vector<sas::ref<int>> stack;
        stack.reserve(DEPTH);
        for (int i = 0; i < DEPTH; ++i) {
            sas::put<int, tfree>("nested", talloc(i));
            stack.push_back(sas::get<int>("nested"));
            assert(stack.back());
            assert(*stack.back() == i);
            assert(*base == -1);
        }

        for (size_t i = 0; i < stack.size(); ++i) {
            assert(stack[i]);
            assert(*stack[i] == static_cast<int>(i));
        }
        stack.clear();
        assert(*base == -1);
    }

    sas::put<int, nullptr>("anchor", nullptr);
    sas::put<int, nullptr>("multiref", nullptr);
    sas::put<int, nullptr>("nested", nullptr);
}
