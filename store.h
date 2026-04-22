#pragma once

#include <atomic>
#include <string>
#include <string_view>

#include <boost/unordered/concurrent_flat_map.hpp>

namespace sas {

using dtor_fn = void (*)(void* value);

struct object_handle {
    void* value;
    dtor_fn dtor;
    std::atomic<uint64_t> refcount{1};

    object_handle(void* v, dtor_fn d) noexcept : value(v), dtor(d) {}
};

static_assert(sizeof(object_handle) <= 64UL);

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

    struct key_equal {
        using is_transparent = void;
        bool operator()(std::string_view a, std::string_view b) const noexcept {
            return a == b;
        }
    };

    boost::concurrent_flat_map<std::string, object_handle*, key_hash, key_equal>
        map_;
};

} // namespace sas
