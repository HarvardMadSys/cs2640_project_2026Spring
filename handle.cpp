#include <cassert>

#include "handle.h"

namespace sas::impl {

std::unique_ptr<object_handle> make_handle(void* value, dtor_fn dtor) {
    return std::make_unique<object_handle>(value, dtor);
}

void drop_handle(object_handle* handle) {
    assert(handle);
    assert(handle->refcount.load(std::memory_order_relaxed) > 0);
    if (handle->refcount.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        impl::free_handle(handle);
    }
}

void free_handle(object_handle* handle) {
    assert(handle);
    if (handle->dtor) {
        handle->dtor(handle->value);
    }
    delete handle;
}

} // namespace sas::impl
