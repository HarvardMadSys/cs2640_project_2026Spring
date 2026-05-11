#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <vector>

#include "handle.h"
#include "hash_table.h"
#include "slot_table.h"

namespace sas::ebr {

constexpr uint64_t INACTIVE_EPOCH = ~uint64_t(0);

class ebr_domain {
  public:
    using deleter_fn = void (*)(void*);

    struct retired_entry {
        void* ptr;
        deleter_fn deleter;
        uint64_t epoch;
    };

    struct retired_list {
        std::vector<retired_entry> entries;
        size_t front{0};

        size_t size() const noexcept { return entries.size() - front; }
        bool empty() const noexcept { return front >= entries.size(); }
        void reserve(size_t n) { entries.reserve(n); }

        auto begin() noexcept { return entries.begin() + front; }
        auto end() noexcept { return entries.end(); }
        auto begin() const noexcept { return entries.begin() + front; }
        auto end() const noexcept { return entries.end(); }

        void push(retired_entry e) {
            if (front > entries.size() / 2) {
                entries.erase(entries.begin(), entries.begin() + front);
                front = 0;
            }
            entries.push_back(e);
        }

        void pop_front_n(size_t n) noexcept { front += n; }
    };

    struct alignas(64) thread_state {
        std::atomic<uint64_t> announced{INACTIVE_EPOCH};
        std::atomic<bool> active{false};
        char pad_[64 - sizeof(std::atomic<uint64_t>) -
                  sizeof(std::atomic<bool>)];
        uint64_t cached_epoch{0};
        retired_list retired;
    };

    ebr_domain();
    ~ebr_domain();

    thread_state* acquire_state();
    void release_state(thread_state* ts) noexcept { slots_.release(ts); }

    uint64_t enter(thread_state& ts) noexcept;
    void exit(thread_state& ts) noexcept;

    void retire(thread_state& ts, void* ptr, deleter_fn deleter);

    void maybe_scan(thread_state& ts);
    void scan_and_reclaim(thread_state& ts);

    uint64_t global_epoch() const noexcept {
        return epoch_.load(std::memory_order_acquire);
    }

    static constexpr size_t RETIRE_BATCH = 256;

  private:
    alignas(64) std::atomic<uint64_t> epoch_{0};
    alignas(64) std::atomic<uint64_t> min_active_{0};
    alignas(64) slot_table<thread_state> slots_;
};

class ebr_thread_state {
  public:
    ebr_thread_state();
    ~ebr_thread_state();

    static ebr_thread_state& get();

    uint64_t enter() noexcept { return domain_.enter(*state_); }
    void exit() noexcept {
        domain_.exit(*state_);
        domain_.maybe_scan(*state_);
    }

    void retire(object_handle* h);
    void retire(hash_table* t);

    hash_table* acquire_table(const std::atomic<hash_table*>& src) noexcept {
        return src.load(std::memory_order_acquire);
    }

    hash_node* take_cached_node() noexcept {
        auto* n = cached_node_;
        cached_node_ = nullptr;
        return n;
    }
    void put_cached_node(hash_node* n) noexcept { cached_node_ = n; }

  private:
    ebr_domain& domain_;
    ebr_domain::thread_state* state_;
    hash_node* cached_node_{nullptr};
};

extern std::unique_ptr<ebr_domain> g_domain;

} // namespace sas::ebr
