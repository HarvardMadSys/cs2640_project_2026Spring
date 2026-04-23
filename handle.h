#pragma once

#include <atomic>
#include <cstdint>
#include <memory>

namespace sas {

using dtor_fn = void (*)(void* value);

struct object_handle {
    void* value;
    dtor_fn dtor;
    std::atomic<uint64_t> refcount{1};

    object_handle(void* v, dtor_fn d) noexcept : value(v), dtor(d) {}
};

static_assert(sizeof(object_handle) <= 64UL);

namespace impl {

std::unique_ptr<object_handle> make_handle(void* value, dtor_fn dtor);
void drop_handle(object_handle* handle);
void free_handle(object_handle* handle);

} // namespace impl

} // namespace sas
