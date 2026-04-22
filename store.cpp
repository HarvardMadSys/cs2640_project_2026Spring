#include "store.h"

#include <cassert>

#include <mimalloc-new-delete.h>

namespace sas {

object_store::~object_store() {
    map_.visit_all([this](auto& e) {
        assert(e.second->refcount.load(std::memory_order_relaxed) == 1);
        close(e.second);
    });
}

object_handle* object_store::get(std::string_view key) {
    object_handle* result = nullptr;
    map_.cvisit(key, [&](const auto& e) {
        result = e.second;
        result->refcount.fetch_add(1, std::memory_order_relaxed);
    });
    return result;
}

void object_store::close(object_handle* handle) {
    assert(handle);
    assert(handle->refcount.load(std::memory_order_relaxed) > 0);
    if (handle->refcount.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        if (handle->dtor) {
            handle->dtor(handle->value);
        }
        delete handle;
    }
}

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto* new_handle = new object_handle{value, dtor};
    object_handle* old_handle = nullptr;

    map_.insert_or_visit({std::string(key), new_handle}, [&](auto& e) {
        old_handle = e.second;
        e.second = new_handle;
    });

    if (old_handle) {
        close(old_handle);
    }
}

} // namespace sas
