#pragma once

#include <atomic>
#include <memory>
#include <string_view>

#include "handle.h"
#include "hash_table.h"

namespace sas::hp {

class object_store {
  public:
    explicit object_store(size_t initial_capacity = 1024);
    ~object_store();

    object_handle* get(std::string_view key);
    void close(object_handle* handle);
    void put(std::string_view key, void* value, dtor_fn dtor);

  private:
    std::atomic<hash_table*> table_;
};

} // namespace sas::hp

namespace sas {
using object_store = hp::object_store;
extern std::unique_ptr<object_store> g_store;
} // namespace sas
