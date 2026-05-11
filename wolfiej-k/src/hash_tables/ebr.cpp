#include <algorithm>
#include <atomic>
#include <cassert>

#include "common.h"
#include "ebr.h"

namespace sas::ebr {

namespace {

void delete_handle(void* p) {
    ::sas::impl::drop_handle(static_cast<object_handle*>(p));
}

void delete_table(void* p) {
    ::sas::impl::free_table(static_cast<hash_table*>(p));
}

} // namespace

ebr_domain::ebr_domain() {}

ebr_domain::~ebr_domain() {
    slots_.for_each([](thread_state& ts) {
        for (auto& e : ts.retired) {
            e.deleter(e.ptr);
        }
    });
}

ebr_domain::thread_state* ebr_domain::acquire_state() {
    return slots_.acquire();
}

uint64_t ebr_domain::enter(thread_state& ts) noexcept {
    uint64_t e = epoch_.load(std::memory_order_acquire);
    ts.cached_epoch = e;
    ts.announced.exchange(e, std::memory_order_seq_cst);
    return e;
}

void ebr_domain::exit(thread_state& ts) noexcept {
    ts.announced.store(INACTIVE_EPOCH, std::memory_order_release);
}

void ebr_domain::retire(thread_state& ts, void* ptr, deleter_fn deleter) {
    ts.retired.push({ptr, deleter, ts.cached_epoch});
}

void ebr_domain::maybe_scan(thread_state& ts) {
    if (ts.retired.size() >= RETIRE_BATCH) {
        scan_and_reclaim(ts);
    }
}

void ebr_domain::scan_and_reclaim(thread_state& self) {
    uint64_t global = epoch_.load(std::memory_order_acquire);
    uint64_t hint = min_active_.load(std::memory_order_acquire);
    uint64_t cutoff;

    if (hint == global) {
        cutoff = hint;
        epoch_.compare_exchange_strong(global, global + 1,
                                       std::memory_order_acq_rel,
                                       std::memory_order_relaxed);
    } else {
        cutoff = global;
        slots_.for_each_active([&](thread_state& ts) {
            if (&ts == &self) return;
            uint64_t a = ts.announced.load(std::memory_order_acquire);
            if (a != INACTIVE_EPOCH && a < cutoff) {
                cutoff = a;
            }
        });

        if (cutoff == global) {
            epoch_.compare_exchange_strong(global, global + 1,
                                           std::memory_order_acq_rel,
                                           std::memory_order_relaxed);
        }

        uint64_t prev = hint;
        while (prev < cutoff) {
            if (min_active_.compare_exchange_weak(prev, cutoff,
                                                  std::memory_order_release,
                                                  std::memory_order_relaxed)) {
                break;
            }
        }
    }

    if (cutoff < 2) {
        return;
    }
    auto& list = self.retired;
    auto cut = std::lower_bound(
        list.begin(), list.end(), cutoff,
        [](const retired_entry& e, uint64_t c) { return e.epoch + 1 < c; });
    for (auto it = list.begin(); it != cut; ++it) {
        it->deleter(it->ptr);
    }
    list.pop_front_n(cut - list.begin());
}

ebr_thread_state::ebr_thread_state() : domain_(*g_domain) {
    ::sas::impl::init_pool();
    ::sas::impl::init_node_pool();
    state_ = domain_.acquire_state();
    state_->retired.reserve(ebr_domain::RETIRE_BATCH * 2);
}

ebr_thread_state::~ebr_thread_state() {
    delete cached_node_;
    state_->announced.store(INACTIVE_EPOCH, std::memory_order_release);
    domain_.scan_and_reclaim(*state_);
    domain_.release_state(state_);
}

void ebr_thread_state::retire(object_handle* h) {
    domain_.retire(*state_, h, &delete_handle);
}

void ebr_thread_state::retire(hash_table* t) {
    domain_.retire(*state_, t, &delete_table);
}

ebr_thread_state& ebr_thread_state::get() {
    thread_local ebr_thread_state state;
    return state;
}

} // namespace sas::ebr
