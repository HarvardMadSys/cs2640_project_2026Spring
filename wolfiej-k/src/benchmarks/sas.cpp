#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <print>
#include <string_view>
#include <thread>

#include "arch_workload.h"
#include "hp_store.h"

namespace {

std::atomic<bool> g_setup_done{false};
std::atomic<int> g_barrier_arrived{0};
std::atomic<int> g_done_counter{0};
std::atomic<int64_t> g_warmup_until_ns{0};
std::atomic<int64_t> g_measure_until_ns{0};

sas::bench::bench_config g_cfg;
std::unique_ptr<sas::bench::steady_workload> g_work;

std::array<std::atomic<int64_t>, sas::bench::ARCH_MAX_WORKERS> g_op_counters{};
std::array<std::atomic<int64_t>, sas::bench::ARCH_MAX_WORKERS> g_tlb_counters{};

} // namespace

extern "C" void entry(int idx) {
    auto* store = sas::hp::g_store.get();

    if (idx == 0) {
        g_cfg = sas::bench::load_config();
        std::println(std::cerr, "Loaded config: {}", g_cfg);

        g_work = std::make_unique<sas::bench::steady_workload>(
            g_cfg, [store](std::string_view k, int* v) {
                store->put(k, v, nullptr);
            });
        g_setup_done.store(true, std::memory_order_release);
    } else {
        while (!g_setup_done.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
    }

    int arrived = g_barrier_arrived.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (arrived == g_cfg.num_threads) {
        auto warmup_until = std::chrono::steady_clock::now() +
                            std::chrono::seconds(g_cfg.warmup_secs);
        auto measure_until =
            warmup_until + std::chrono::seconds(g_cfg.bench_secs);
        g_warmup_until_ns.store(warmup_until.time_since_epoch().count(),
                                std::memory_order_release);
        g_measure_until_ns.store(measure_until.time_since_epoch().count(),
                                 std::memory_order_release);
    } else {
        while (g_measure_until_ns.load(std::memory_order_acquire) == 0) {
            std::this_thread::yield();
        }
    }

    auto warmup_until = std::chrono::steady_clock::time_point(
        std::chrono::steady_clock::duration(
            g_warmup_until_ns.load(std::memory_order_relaxed)));
    auto measure_until = std::chrono::steady_clock::time_point(
        std::chrono::steady_clock::duration(
            g_measure_until_ns.load(std::memory_order_relaxed)));

    sas::bench::arch_worker_loop(idx, g_cfg, *g_work, store,
                                 g_op_counters.data(), g_tlb_counters.data(),
                                 warmup_until, measure_until);

    int done = g_done_counter.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (done == g_cfg.num_threads) {
        int64_t total_ops = 0;
        int64_t total_tlb = 0;
        for (int i = 0; i < g_cfg.num_threads; ++i) {
            total_ops += g_op_counters[i].load(std::memory_order_relaxed);
            total_tlb += g_tlb_counters[i].load(std::memory_order_relaxed);
        }
        double secs = std::chrono::duration<double>(
                          std::chrono::seconds(g_cfg.bench_secs))
                          .count();
        double tlb_per_op =
            total_ops > 0 ? double(total_tlb) / double(total_ops) : 0.0;
        sas::bench::arch_emit_throughput_json(
            "sas", g_cfg.num_threads, double(total_ops) / secs, tlb_per_op);
    }
}
