#pragma once

#include <atomic>
#include <mutex>
#include <string>
#include <string_view>

#include "handle.h"

namespace sas {

struct hash_node {
    std::string key;
    std::atomic<object_handle*> handle;
    std::atomic<hash_node*> next;

    hash_node(std::string_view k, object_handle* h)
        : key(k), handle(h), next(nullptr) {}
};

struct hash_table {
    size_t capacity;
    std::atomic<hash_node*>* buckets;

    explicit hash_table(size_t c) : capacity(c) {
        buckets = new std::atomic<hash_node*>[capacity] {};
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

} // namespace sas
