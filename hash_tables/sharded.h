#pragma once

#include <boost/unordered/concurrent_flat_map.hpp>
#include <cstddef>
#include <string>
#include <string_view>

#include "handle.h"

namespace sas::bench {

// Sharded baseline: boost::concurrent_flat_map. The map is partitioned into a
// fixed number of shards, each guarded independently; non-conflicting shards
// progress in parallel.
struct sharded_store {
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

    boost::concurrent_flat_map<std::string, sas::object_handle*, key_hash,
                               key_eq>
        map_;

    explicit sharded_store(size_t initial_capacity) : map_(initial_capacity) {}

    ~sharded_store() {
        map_.visit_all([](auto& e) { sas::impl::drop_handle(e.second); });
    }

    sas::object_handle* get(std::string_view key) {
        sas::object_handle* result = nullptr;
        map_.visit(key, [&](auto& e) {
            result = e.second;
            if (result) {
                result->refcount.fetch_add(1, std::memory_order_relaxed);
            }
        });
        return result;
    }

    void close(sas::object_handle* h) { sas::impl::drop_handle(h); }

    void put(std::string_view key, void* value, sas::dtor_fn dtor) {
        auto new_h = sas::impl::make_handle(value, dtor);
        sas::object_handle* old_h = nullptr;
        map_.emplace_or_visit(key, new_h.get(), [&](auto& e) {
            old_h = e.second;
            e.second = new_h.get();
        });
        new_h.release();
        if (old_h) {
            sas::impl::drop_handle(old_h);
        }
    }
};

} // namespace sas::bench
