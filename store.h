#pragma once

#include <atomic>
#include <memory>
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
    void trigger_resize(hash_table* old);
    void help_migrate_one(hash_table* old, hash_table* next);
    void migrate_bucket(hash_table* old, hash_table* next, size_t idx);
    void try_complete_resize(hash_table* old, hash_table* next);

    std::atomic<hash_table*> table_;
};

extern std::unique_ptr<object_store> g_store;

} // namespace sas
