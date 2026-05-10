#include <atomic>
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>

#include "client.h"

extern "C" void entry(int) {
    {
        constexpr int N_CONSUMERS = 8;
        std::atomic<bool> stop{false};

        std::vector<std::thread> consumers;
        std::vector<long> counts(N_CONSUMERS, 0);
        consumers.reserve(N_CONSUMERS);
        for (int i = 0; i < N_CONSUMERS; ++i) {
            consumers.emplace_back([i, &stop, &counts] {
                long c = 0;
                while (!stop.load(std::memory_order_acquire)) {
                    if (sas::poll("pp:flag")) {
                        ++c;
                    }
                }
                counts[i] = c;
            });
        }

        for (int i = 0; i < 1000; ++i) {
            sas::publish("pp:flag");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        stop.store(true, std::memory_order_release);
        for (auto& t : consumers) {
            t.join();
        }
        for (long c : counts) {
            assert(c > 0);
        }
    }

    {
        constexpr int N_KEYS = 64;
        std::vector<std::thread> producers;
        producers.reserve(N_KEYS);
        for (int i = 0; i < N_KEYS; ++i) {
            producers.emplace_back([i] {
                char key[32];
                std::snprintf(key, sizeof(key), "pp:k:%d", i);
                sas::publish(key);
            });
        }
        for (auto& t : producers) {
            t.join();
        }

        for (int i = 0; i < N_KEYS; ++i) {
            char key[32];
            std::snprintf(key, sizeof(key), "pp:k:%d", i);
            auto h = sas::poll(key);
            assert(h);
            assert(h.get() == nullptr);
        }
    }

    {
        constexpr int N_READERS = 12;
        std::atomic<bool> stop{false};
        std::atomic<int> seen_published{0};
        std::atomic<int> seen_unpublished{0};

        std::vector<std::thread> readers;
        readers.reserve(N_READERS);
        for (int i = 0; i < N_READERS; ++i) {
            readers.emplace_back([&] {
                while (!stop.load(std::memory_order_acquire)) {
                    auto h = sas::poll("pp:toggle");
                    if (h) {
                        seen_published.fetch_add(1,
                                                 std::memory_order_relaxed);
                    } else {
                        seen_unpublished.fetch_add(1,
                                                   std::memory_order_relaxed);
                    }
                }
            });
        }

        for (int i = 0; i < 200; ++i) {
            sas::publish("pp:toggle");
            std::this_thread::yield();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        stop.store(true, std::memory_order_release);

        for (auto& t : readers) {
            t.join();
        }
        assert(seen_published.load() > 0);
    }
}
