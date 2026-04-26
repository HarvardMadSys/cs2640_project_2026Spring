#pragma once

#include <atomic>
#include <mutex>
#include <string>
#include <string_view>

#include "handle.h"
#include "tagged_ptr.h"

namespace sas {

struct alignas(64) hash_node {
    std::string key;
    size_t hash;
    atomic_tagged_ptr<object_handle> handle;
    atomic_tagged_ptr<hash_node> next;

    hash_node(std::string_view k, size_t kh, object_handle* h)
        : key(k), hash(kh), handle(h), next(nullptr) {}
};

struct hash_table {
    static constexpr size_t NUM_SHARDS = 32;
    static constexpr size_t SHARD_MASK = NUM_SHARDS - 1;

    struct alignas(64) count_shard {
        std::atomic<size_t> v{0};
    };

    size_t capacity;
    atomic_tagged_ptr<hash_node>* buckets;
    std::array<count_shard, NUM_SHARDS> shards;

    explicit hash_table(size_t c) : capacity(c) {
        buckets = new atomic_tagged_ptr<hash_node>[capacity] {};
    }
};

class object_store {
  public:
    explicit object_store(size_t initial_capacity = 1024);
    ~object_store();

    object_handle* get(std::string_view key);
    void close(object_handle* handle);
    void put(std::string_view key, void* value, dtor_fn dtor);

  private:
    void resize(hash_table* expected);

    std::atomic<hash_table*> table_;
    std::mutex resize_mutex_;
};

extern std::unique_ptr<object_store> g_store;

namespace impl {

void free_table(hash_table* table);

}

} // namespace sas
