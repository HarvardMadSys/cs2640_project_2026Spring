#pragma once

#include <atomic>
#include <boost/unordered/concurrent_flat_map.hpp>
#include <cstddef>
#include <string>
#include <string_view>

#include "handle.h"
#include "hazard.h"

namespace sas::bench {

struct atomic_handle_slot {
    mutable std::atomic<sas::object_handle*> ptr;

    atomic_handle_slot() noexcept : ptr(nullptr) {}
    atomic_handle_slot(sas::object_handle* h) noexcept : ptr(h) {}
    atomic_handle_slot(const atomic_handle_slot& o) noexcept
        : ptr(o.ptr.load(std::memory_order_relaxed)) {}
    atomic_handle_slot& operator=(const atomic_handle_slot& o) noexcept {
        ptr.store(o.ptr.load(std::memory_order_relaxed),
                  std::memory_order_relaxed);
        return *this;
    }
};

struct hybrid_store {
    struct key_hash {
        using is_transparent = void;
        size_t operator()(std::string_view sv) const noexcept {
            return std::hash<std::string_view>{}(sv);
        }
    };
    struct key_eq {
        using is_transparent = void;
        bool operator()(std::string_view a, std::string_view b) const noexcept {
            return a == b;
        }
    };

    boost::concurrent_flat_map<std::string, atomic_handle_slot, key_hash, key_eq>
        map_;

    explicit hybrid_store(size_t initial_capacity = 1024)
        : map_(initial_capacity) {}

    ~hybrid_store() {
        map_.visit_all([](auto& e) {
            auto* h = e.second.ptr.load(std::memory_order_relaxed);
            if (h) {
                sas::impl::drop_handle(h);
            }
        });
    }

    sas::object_handle* get(std::string_view key) {
        auto& state = sas::hp::hazard_thread_state::get();
        sas::object_handle* result = nullptr;
        map_.cvisit(key, [&](const auto& e) {
            result = sas::g_domain->protect(e.second.ptr, state.node());
            if (result) {
                result->refcount.fetch_add(1, std::memory_order_relaxed);
            }
        });
        sas::g_domain->unprotect<sas::object_handle>(state.node());
        return result;
    }

    void close(sas::object_handle* h) { sas::impl::drop_handle(h); }

    void put(std::string_view key, void* value, sas::dtor_fn dtor) {
        auto& state = sas::hp::hazard_thread_state::get();
        auto new_h = sas::impl::make_handle(value, dtor);
        sas::object_handle* old_h = nullptr;
        map_.emplace_or_cvisit(key, new_h.get(), [&](const auto& e) {
            old_h = e.second.ptr.exchange(new_h.get(),
                                          std::memory_order_acq_rel);
        });
        new_h.release();
        if (old_h) {
            state.retire(old_h);
        }
    }
};

} // namespace sas::bench
