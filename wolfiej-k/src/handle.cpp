#include <atomic>
#include <cassert>

#include "handle.h"
#include "memory_pool.h"

namespace sas::impl {

namespace {

thread_local memory_pool<object_handle, HANDLE_POOL_CAPACITY> pool;

} // namespace

void init_pool() noexcept {
    pool.prefill([] { return new object_handle(nullptr, nullptr); });
}

handle_owner make_handle(void* value, dtor_fn dtor) {
    if (auto* h = pool.acquire()) [[likely]] {
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
