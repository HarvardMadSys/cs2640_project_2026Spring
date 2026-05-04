#pragma once

#include <atomic>
#include <cstddef>
#include <string>
#include <string_view>
#include <unordered_map>

#include "handle.h"

namespace sas::bench {

struct spinlock_store {
    struct key_hash {
        using is_transparent = void;
        size_t operator()(std::string_view sv) const noexcept {
            return std::hash<std::string_view>{}(sv);
        }
        size_t operator()(const std::string& s) const noexcept {
            return std::hash<std::string_view>{}(s);
        }
    };
    struct key_eq {
        using is_transparent = void;
        bool operator()(std::string_view a, std::string_view b) const noexcept {
            return a == b;
        }
    };

    std::atomic_flag lock_ = ATOMIC_FLAG_INIT;
    std::unordered_map<std::string, sas::object_handle*, key_hash, key_eq> map_;

    explicit spinlock_store(size_t initial_capacity) {
        map_.reserve(initial_capacity);
    }

    ~spinlock_store() {
        for (auto& [k, h] : map_) {
            if (h) {
                sas::impl::drop_handle(h);
            }
        }
    }

    void acquire() noexcept {
        while (lock_.test_and_set(std::memory_order_acquire)) {
        }
    }
    void release() noexcept { lock_.clear(std::memory_order_release); }

    sas::object_handle* get(std::string_view key) {
        sas::object_handle* result = nullptr;
        acquire();
        auto it = map_.find(key);
        if (it != map_.end()) {
            result = it->second;
            result->refcount.fetch_add(1, std::memory_order_relaxed);
        }
        release();
        return result;
    }

    void close(sas::object_handle* h) { sas::impl::drop_handle(h); }

    void put(std::string_view key, void* value, sas::dtor_fn dtor) {
        auto new_h = sas::impl::make_handle(value, dtor);
        sas::object_handle* old_h = nullptr;

        acquire();
        auto it = map_.find(key);
        if (it == map_.end()) {
            map_.emplace(std::string(key), new_h.get());
        } else {
            old_h = it->second;
            it->second = new_h.get();
        }
        release();

        new_h.release();
        if (old_h) {
            sas::impl::drop_handle(old_h);
        }
    }
};

} // namespace sas::bench
