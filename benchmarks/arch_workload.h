#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <linux/perf_event.h>
#include <mutex>
#include <print>
#include <string_view>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "store.h"
#include "workload.h"

namespace sas::bench {

inline constexpr std::chrono::seconds ARCH_DURATION{10};
inline constexpr std::chrono::seconds ARCH_WARMUP{5};
inline constexpr int ARCH_MAX_WORKERS = 64;
inline constexpr int ARCH_CLOCK_CHECK_BATCH = 1024;

class perf_counter {
  public:
    perf_counter(uint32_t type, uint64_t config) {
        perf_event_attr attr{};
        attr.type = type;
        attr.size = sizeof(attr);
        attr.config = config;
        attr.disabled = 1;
        attr.exclude_kernel = 1;
        attr.exclude_hv = 1;
        fd_ = static_cast<int>(
            syscall(SYS_perf_event_open, &attr, 0, -1, -1, 0UL));
        if (fd_ < 0) {
            static std::once_flag warn_once;
            std::call_once(warn_once, [] {
                std::println(std::cerr,
                             "[arch] perf_event_open unavailable (errno={}); "
                             "TLB counters disabled. Try: sudo sysctl -w "
                             "kernel.perf_event_paranoid=1",
                             errno);
            });
        }
    }
    ~perf_counter() {
        if (fd_ >= 0) {
            close(fd_);
        }
    }
    perf_counter(const perf_counter&) = delete;
    perf_counter& operator=(const perf_counter&) = delete;

    bool valid() const { return fd_ >= 0; }
    void reset() {
        if (fd_ >= 0) {
            ioctl(fd_, PERF_EVENT_IOC_RESET, 0);
        }
    }
    void enable() {
        if (fd_ >= 0) {
            ioctl(fd_, PERF_EVENT_IOC_ENABLE, 0);
        }
    }
    void disable() {
        if (fd_ >= 0) {
            ioctl(fd_, PERF_EVENT_IOC_DISABLE, 0);
        }
    }
    uint64_t read_count() {
        if (fd_ < 0) {
            return 0;
        }
        uint64_t v = 0;
        ssize_t r = read(fd_, &v, sizeof(v));
        return r == sizeof(v) ? v : 0;
    }

  private:
    int fd_ = -1;
};

inline constexpr uint64_t DTLB_LOAD_MISS_CONFIG =
    PERF_COUNT_HW_CACHE_DTLB | (PERF_COUNT_HW_CACHE_OP_READ << 8) |
    (PERF_COUNT_HW_CACHE_RESULT_MISS << 16);

inline void
arch_worker_loop(int idx, const bench_config& cfg, const steady_workload& work,
                 sas::object_store* store, std::atomic<int64_t>* op_counters,
                 std::atomic<int64_t>* tlb_counters,
                 std::chrono::steady_clock::time_point warmup_until,
                 std::chrono::steady_clock::time_point measure_until) {
    bench_config local_cfg = cfg;
    local_cfg.read_ratio = 1.0;
    steady_rng rng(local_cfg, idx);

    auto run_until = [&](auto deadline) {
        int64_t ops = 0;
        while (std::chrono::steady_clock::now() < deadline) {
            for (int j = 0; j < ARCH_CLOCK_CHECK_BATCH; ++j) {
                int k = rng.next_key();
                auto* h = store->get(work.sv[k]);
                if (h) {
                    store->close(h);
                }
            }
            ops += ARCH_CLOCK_CHECK_BATCH;
        }
        return ops;
    };

    run_until(warmup_until);

    perf_counter tlb(PERF_TYPE_HW_CACHE, DTLB_LOAD_MISS_CONFIG);
    tlb.reset();
    tlb.enable();
    int64_t ops = run_until(measure_until);
    tlb.disable();
    uint64_t misses = tlb.read_count();

    op_counters[idx].fetch_add(ops, std::memory_order_relaxed);
    tlb_counters[idx].fetch_add(static_cast<int64_t>(misses),
                                std::memory_order_relaxed);
}

inline void arch_emit_throughput_json(const char* mode, int n, double ips,
                                      double tlb_per_op) {
    std::println(std::cout,
                 "{{\n  \"context\": {{}},\n  \"benchmarks\": [\n"
                 "    {{\"name\":\"{}/real_time/threads:{}\","
                 "\"run_type\":\"iteration\",\"iterations\":1,"
                 "\"items_per_second\":{},"
                 "\"tlb_misses_per_op\":{}}}\n"
                 "  ]\n}}",
                 mode, n, ips, tlb_per_op);
}

} // namespace sas::bench
