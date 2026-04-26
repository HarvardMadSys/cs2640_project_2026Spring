#include <atomic>
#include <cassert>
#include <cstddef>

#include "handle.h"
#include "memory_pool.h"

namespace sas::impl {

namespace {

constexpr size_t POOL_CAPACITY = 512;

thread_local memory_pool<object_handle, POOL_CAPACITY> pool;

} // namespace

void init_pool() noexcept { (void)pool; }

handle_owner make_handle(void* value, dtor_fn dtor) {
    if (auto* h = pool.acquire()) {
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
    if (handle->refcount.fetch_sub(1, std::memory_order_release) == 1) {
        std::atomic_thread_fence(std::memory_order_acquire);
        free_handle(handle);
    }
}

void free_handle(object_handle* handle) {
    assert(handle);
    if (handle->dtor) {
        handle->dtor(handle->value);
    }
    pool.release(handle);
}

} // namespace sas::impl
