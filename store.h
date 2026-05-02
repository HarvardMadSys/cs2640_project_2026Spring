#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <string_view>

#include "handle.h"

namespace sas {

struct hash_table;

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
