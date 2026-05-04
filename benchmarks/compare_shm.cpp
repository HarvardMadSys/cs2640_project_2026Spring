#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <memory>
#include <mimalloc.h>
#include <new>
#include <print>
#include <string_view>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>

#include "arch_workload.h"
#include "hazard.h"
#include "store.h"

namespace {

void* const SHM_ADDR = reinterpret_cast<void*>(0x700000000000ULL);
constexpr std::size_t SHM_SIZE = 16ULL << 30;
constexpr const char* SHM_NAME = "/sas_compare_shm";
constexpr std::size_t ARENA_BLOCK = 64ULL << 20;
constexpr std::size_t LEADER_ARENA = 4ULL << 30;
constexpr std::size_t WORKER_ARENA = 2 * ARENA_BLOCK;
static_assert(LEADER_ARENA + ARENA_BLOCK +
                  (sas::bench::ARCH_MAX_WORKERS - 1) * WORKER_ARENA <=
              SHM_SIZE);

struct shared_state {
    std::atomic<bool> setup_done{false};
    std::atomic<int> worker_counter{1};
    std::atomic<int> barrier_arrived{0};
    std::atomic<int> done_counter{0};
    std::atomic<int64_t> warmup_until_ns{0};
    std::atomic<int64_t> measure_until_ns{0};
    sas::hazard_domain* domain_ptr{nullptr};
    sas::object_store* store_ptr{nullptr};
    sas::bench::steady_workload* work_ptr{nullptr};
    std::atomic<int64_t> ops[sas::bench::ARCH_MAX_WORKERS]{};
    std::atomic<int64_t> tlb_misses[sas::bench::ARCH_MAX_WORKERS]{};
};
static_assert(sizeof(shared_state) <= ARENA_BLOCK);

bool register_slice(int slice_idx) {
    std::size_t slice_size = (slice_idx == 0) ? LEADER_ARENA : WORKER_ARENA;
    std::size_t off =
        ARENA_BLOCK +
        (slice_idx == 0 ? 0 : LEADER_ARENA + (slice_idx - 1) * WORKER_ARENA);
    void* slice = reinterpret_cast<char*>(SHM_ADDR) + off;
    mi_arena_id_t arena_id = 0;
    if (!mi_manage_os_memory_ex(slice, slice_size, true, false, true, -1, true,
                                &arena_id)) {
        std::println(std::cerr, "[slice {}] mi_manage_os_memory_ex failed",
                     slice_idx);
        return false;
    }
    mi_heap_t* h = mi_heap_new_in_arena(arena_id);
    if (!h) {
        std::println(std::cerr, "[slice {}] mi_heap_new_in_arena failed",
                     slice_idx);
        return false;
    }
    mi_heap_set_default(h);
    return true;
}

} // namespace

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);
    int n = cfg.num_threads;

    bool is_leader = false;
    int fd = shm_open(SHM_NAME, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd >= 0) {
        is_leader = true;
        if (ftruncate(fd, SHM_SIZE) != 0) {
            std::perror("ftruncate");
            close(fd);
            return 1;
        }
    } else if (errno == EEXIST) {
        fd = shm_open(SHM_NAME, O_RDWR, 0600);
        if (fd < 0) {
            std::perror("shm_open");
            return 1;
        }
    } else {
        std::perror("shm_open");
        return 1;
    }

    void* base = mmap(SHM_ADDR, SHM_SIZE, PROT_READ | PROT_WRITE,
                      MAP_SHARED | MAP_FIXED, fd, 0);
    close(fd);
    if (base != SHM_ADDR) {
        std::println(std::cerr, "mmap got {}, wanted {}", base, SHM_ADDR);
        return 1;
    }

    madvise(base, SHM_SIZE, MADV_HUGEPAGE);

    shared_state* shared;
    int my_idx;

    if (is_leader) {
        shared = new (base) shared_state();
        if (!register_slice(0)) {
            return 1;
        }
        sas::g_domain = std::make_unique<sas::hazard_domain>();
        sas::g_store =
            std::make_unique<sas::object_store>(std::size_t(cfg.num_keys) * 2);

        auto* work = new sas::bench::steady_workload(
            cfg, [](std::string_view k, int* v) {
                sas::g_store->put(k, v, nullptr);
            });

        shared->domain_ptr = sas::g_domain.get();
        shared->store_ptr = sas::g_store.get();
        shared->work_ptr = work;
        my_idx = 0;
        shared->setup_done.store(true, std::memory_order_release);
    } else {
        shared = static_cast<shared_state*>(base);
        while (!shared->setup_done.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        my_idx = shared->worker_counter.fetch_add(1, std::memory_order_acq_rel);
        if (!register_slice(my_idx)) {
            return 1;
        }

        sas::g_domain.reset(shared->domain_ptr);
        sas::g_store.reset(shared->store_ptr);
    }

    int arrived =
        shared->barrier_arrived.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (arrived == n) {
        auto warmup_until =
            std::chrono::steady_clock::now() + sas::bench::ARCH_WARMUP;
        auto measure_until = warmup_until + sas::bench::ARCH_DURATION;
        shared->warmup_until_ns.store(warmup_until.time_since_epoch().count(),
                                      std::memory_order_release);
        shared->measure_until_ns.store(measure_until.time_since_epoch().count(),
                                       std::memory_order_release);
    } else {
        while (shared->measure_until_ns.load(std::memory_order_acquire) == 0) {
            std::this_thread::yield();
        }
    }
    auto warmup_until = std::chrono::steady_clock::time_point(
        std::chrono::steady_clock::duration(
            shared->warmup_until_ns.load(std::memory_order_relaxed)));
    auto measure_until = std::chrono::steady_clock::time_point(
        std::chrono::steady_clock::duration(
            shared->measure_until_ns.load(std::memory_order_relaxed)));

    sas::bench::arch_worker_loop(my_idx, cfg, *shared->work_ptr,
                                 shared->store_ptr, shared->ops,
                                 shared->tlb_misses, warmup_until,
                                 measure_until);

    int done = shared->done_counter.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (done == n) {
        int64_t total_ops = 0;
        int64_t total_tlb = 0;
        for (int i = 0; i < n; ++i) {
            total_ops += shared->ops[i].load(std::memory_order_relaxed);
            total_tlb += shared->tlb_misses[i].load(std::memory_order_relaxed);
        }
        double secs =
            std::chrono::duration<double>(sas::bench::ARCH_DURATION).count();
        sas::bench::arch_emit_throughput_json("shm", n,
                                              double(total_ops) / secs,
                                              double(total_tlb) / secs);
    }

    if (is_leader) {
        while (shared->done_counter.load(std::memory_order_acquire) < n) {
            std::this_thread::yield();
        }
        shm_unlink(SHM_NAME);
    }

    sas::g_store.release();
    sas::g_domain.release();
    return 0;
}
