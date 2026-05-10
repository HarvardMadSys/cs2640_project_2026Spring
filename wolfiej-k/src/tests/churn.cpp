#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

#include "client.h"

namespace {

constexpr uint32_t MAGIC = 0xCAFEBABE;

struct payload {
    uint32_t magic;
    uint32_t writer_id;
    uint64_t sequence;
};

std::atomic<int> allocs{0};
std::atomic<int> frees{0};

payload* talloc(uint32_t writer_id, uint64_t sequence) {
    allocs.fetch_add(1, std::memory_order_relaxed);
    auto* p = static_cast<payload*>(std::malloc(sizeof(payload)));
    p->magic = MAGIC;
    p->writer_id = writer_id;
    p->sequence = sequence;
    return p;
}

void tfree(payload* p) {
    frees.fetch_add(1, std::memory_order_relaxed);
    p->magic = 0xDEADBEEF;
    std::free(p);
}

struct leak_guard {
    ~leak_guard() { assert(frees.load() == allocs.load()); }
} guard;

} // namespace

extern "C" void entry(int) {
    constexpr int N_KEYS = 4;
    constexpr int N_WRITERS = 8;
    constexpr int N_READERS = 16;
    constexpr int WRITES_PER_WRITER = 20'000;

    char keys[N_KEYS][16];
    for (int i = 0; i < N_KEYS; ++i) {
        std::snprintf(keys[i], sizeof(keys[i]), "ch:%d", i);
        sas::put<payload, tfree>(keys[i], talloc(0xFFFF, 0));
    }

    std::atomic<bool> stop_readers{false};
    std::atomic<long> reads_observed{0};

    std::vector<std::thread> readers;
    readers.reserve(N_READERS);
    for (int i = 0; i < N_READERS; ++i) {
        readers.emplace_back([&keys, &stop_readers, &reads_observed] {
            long local = 0;
            while (!stop_readers.load(std::memory_order_acquire)) {
                for (int k = 0; k < N_KEYS; ++k) {
                    auto h = sas::get<payload>(keys[k]);
                    if (h) {
                        assert(h->magic == MAGIC);
                        ++local;
                    }
                }
            }
            reads_observed.fetch_add(local, std::memory_order_relaxed);
        });
    }

    std::vector<std::thread> writers;
    writers.reserve(N_WRITERS);
    for (int w = 0; w < N_WRITERS; ++w) {
        writers.emplace_back([w, &keys] {
            for (int i = 0; i < WRITES_PER_WRITER; ++i) {
                int k = (w + i) & (N_KEYS - 1);
                sas::put<payload, tfree>(
                    keys[k], talloc(static_cast<uint32_t>(w),
                                    static_cast<uint64_t>(i)));
            }
        });
    }
    for (auto& t : writers) {
        t.join();
    }
    stop_readers.store(true, std::memory_order_release);
    for (auto& t : readers) {
        t.join();
    }

    assert(reads_observed.load() > 0);

    for (int i = 0; i < N_KEYS; ++i) {
        auto h = sas::get<payload>(keys[i]);
        assert(h);
        assert(h->magic == MAGIC);
    }

    for (int i = 0; i < N_KEYS; ++i) {
        sas::put<payload, nullptr>(keys[i], nullptr);
    }
}
