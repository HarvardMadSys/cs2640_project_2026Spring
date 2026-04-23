#pragma once

#include <atomic>
#include <boost/unordered/concurrent_flat_map.hpp>
#include <string>
#include <string_view>

#include "handle.h"

namespace sas {

class object_store {
  public:
    explicit object_store(size_t initial_capacity = 1024)
        : map_(initial_capacity) {}
    ~object_store();

    object_handle* get(std::string_view key);
    void close(object_handle* handle);
    void put(std::string_view key, void* value, dtor_fn dtor);

  private:
    struct key_hash {
        using is_transparent = void;
        size_t operator()(std::string_view sv) const noexcept {
            return std::hash<std::string_view>{}(sv);
        }
    };

    struct key_pred {
        using is_transparent = void;
        bool operator()(std::string_view a, std::string_view b) const noexcept {
            return a == b;
        }
    };

    struct map_value {
        mutable std::atomic<object_handle*> handle;

        map_value(object_handle* handle) : handle(handle) {}
        map_value(map_value&& other) noexcept
            : handle(other.handle.load(std::memory_order_relaxed)) {}
        map_value& operator=(map_value&& other) noexcept {
            handle.store(other.handle.load(std::memory_order_relaxed),
                         std::memory_order_relaxed);
            return *this;
        }
        map_value(const map_value&) = delete;
        map_value& operator=(const map_value&) = delete;
    };

    boost::concurrent_flat_map<std::string, map_value, key_hash, key_pred> map_;
};

extern std::unique_ptr<object_store> g_store;

} // namespace sas
