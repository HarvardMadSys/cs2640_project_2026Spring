// The shared key-value store implementation is the subject of this stress test.
#include "kvstore/kv_store.hpp"

// Atomics let the worker threads report failure without additional locking.
#include <atomic>
// A condition variable and mutex let all threads start at the same moment.
#include <condition_variable>
// Standard exit codes for the test executable.
#include <cstdlib>
// Console output identifies failures and success.
#include <iostream>
// Mutexes protect the start gate.
#include <mutex>
// Strings are used to build unique keys and values.
#include <string>
// Threads create concurrent pressure on the store.
#include <thread>
// Vectors manage the worker thread objects.
#include <vector>

// Local helper and synchronization types stay inside the test file.
namespace {

// Tiny assertion helper for a standalone executable.
int expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "test failed: " << message << '\n';
        return 1;
    }
    return 0;
}

// StartGate blocks all workers until the main thread releases them together.
struct StartGate {
    // Mutex protects the go flag.
    std::mutex mutex;
    // Condition variable wakes workers once go becomes true.
    std::condition_variable cv;
    // This flag tells workers when they may begin the workload.
    bool go = false;
};

}  // namespace

// Launch several threads that repeatedly set, read, and erase distinct keys.
int main() {
    // Use the store under concurrent access from many threads.
    kvstore::KeyValueStore store;
    // Keep the test small enough to run quickly but large enough to exercise contention.
    constexpr std::size_t thread_count = 8;
    // Each worker repeats the same set/get/erase pattern many times.
    constexpr std::size_t iterations = 500;

    // All workers wait here until the main thread flips the gate.
    StartGate gate;
    // If any worker detects a mismatch, it records the failure here.
    std::atomic<bool> failed{false};
    // Hold the worker thread objects so they can be joined at the end.
    std::vector<std::thread> threads;
    threads.reserve(thread_count);

    // Start one worker per thread id so each key namespace stays unique.
    for (std::size_t t = 0; t < thread_count; ++t) {
        threads.emplace_back([&, t]() {
            // Wait for the coordinated start signal.
            {
                std::unique_lock<std::mutex> lock(gate.mutex);
                gate.cv.wait(lock, [&] { return gate.go; });
            }

            // Repeatedly exercise the store with unique keys so threads do not overwrite one another.
            for (std::size_t i = 0; i < iterations; ++i) {
                const std::string key = "thread_" + std::to_string(t) + "_key_" + std::to_string(i);
                const std::string value = "value_" + std::to_string(t) + "_" + std::to_string(i);

                // SET should make the new value immediately readable from the same store.
                store.set(key, value);
                auto loaded = store.get(key);
                if (!loaded.has_value() || *loaded != value) {
                    failed.store(true, std::memory_order_relaxed);
                    return;
                }
                // ERASE should succeed for keys that were just written.
                if (!store.erase(key)) {
                    failed.store(true, std::memory_order_relaxed);
                    return;
                }
            }
        });
    }

    // Release all worker threads once they are ready.
    {
        std::lock_guard<std::mutex> lock(gate.mutex);
        gate.go = true;
    }
    gate.cv.notify_all();

    // Wait for every worker to finish before checking the final state.
    for (auto& thread : threads) {
        thread.join();
    }

    // If any thread observed inconsistent behavior, the test fails.
    if (failed.load(std::memory_order_relaxed)) {
        std::cerr << "test failed: concurrent read/write mismatch\n";
        return EXIT_FAILURE;
    }

    // Because every key is deleted after use, the store should end empty.
    if (int rc = expect(store.size() == 0, "store should end empty after concurrent operations"); rc != 0) {
        return rc;
    }

    // Print a success banner for manual test runs.
    std::cout << "kv_store_concurrency_test passed\n";
    return EXIT_SUCCESS;
}
/**
 * @file kv_store_concurrency_test.cpp
 * @brief Concurrent access test for the mutex-protected key-value store.
 * @ingroup tests
 */
