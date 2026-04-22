#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <print>
#include <thread>
#include <vector>

#include "client.h"

extern "C" void entry(int cid) {
    constexpr int NUM_COUNTERS = 8;
    constexpr int ITERATIONS = 10'000;

    if (cid == 0) {
        static char keys[NUM_COUNTERS][32];

        for (int i = 0; i < NUM_COUNTERS; ++i) {
            std::snprintf(keys[i], sizeof(keys[i]), "tc:sync:%d", i);
            auto* counter = new std::atomic<int>{0};
            sas::put<std::atomic<int>>(keys[i], counter);
        }

        sas::publish("tc:sync:ready");
    } else {
        while (true) {
            if (auto h = sas::poll("tc:sync:ready")) {
                break;
            }
            std::this_thread::yield();
        }
    }

    std::vector<int> last_seen(NUM_COUNTERS, 0);
    for (int j = 0; j < ITERATIONS; ++j) {
        int idx = j % NUM_COUNTERS;

        char search_key[32];
        std::snprintf(search_key, sizeof(search_key), "tc:sync:%d", idx);

        auto h = sas::get<std::atomic<int>>(search_key);
        assert(h);

        auto* counter = h.get();
        assert(counter != nullptr);

        int prev = counter->fetch_add(1, std::memory_order_acq_rel);

        assert(prev >= last_seen[idx]);
        last_seen[idx] = prev + 1;
    }

    std::println("Counter test passed: {}!", cid);
}
