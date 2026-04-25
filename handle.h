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

void drop_handle(object_handle* handle);
void free_handle(object_handle* handle);
void init_pool() noexcept;

struct handle_deleter {
    void operator()(object_handle* h) const noexcept { free_handle(h); }
};

using handle_owner = std::unique_ptr<object_handle, handle_deleter>;

handle_owner make_handle(void* value, dtor_fn dtor);

} // namespace impl

} // namespace sas
