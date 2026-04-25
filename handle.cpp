#include <array>
#include <cassert>
#include <cstddef>

#include "handle.h"

namespace sas::impl {

namespace {

constexpr size_t POOL_CAPACITY = 512;

struct handle_pool {
    std::array<object_handle*, POOL_CAPACITY> storage;
    size_t size{0};

    ~handle_pool() {
        for (size_t i = 0; i < size; ++i) {
            delete storage[i];
        }
    }
};

thread_local handle_pool pool;

} // namespace

void init_pool() noexcept { (void)pool; }

handle_owner make_handle(void* value, dtor_fn dtor) {
    if (pool.size > 0) {
        auto* h = pool.storage[--pool.size];
        h->value = value;
        h->dtor = dtor;
        h->refcount.store(1, std::memory_order_relaxed);
        return handle_owner(h);
    }
    return handle_owner(new object_handle(value, dtor));
}

void drop_handle(object_handle* handle) {
    assert(handle);
    assert(handle->refcount.load(std::memory_order_relaxed) > 0);
    if (handle->refcount.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        free_handle(handle);
    }
}

void free_handle(object_handle* handle) {
    assert(handle);
    if (handle->dtor) {
        handle->dtor(handle->value);
    }
    if (pool.size < POOL_CAPACITY) {
        pool.storage[pool.size++] = handle;
    } else {
        delete handle;
    }
}

} // namespace sas::impl
