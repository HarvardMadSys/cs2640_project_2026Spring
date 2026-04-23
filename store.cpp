#include "store.h"
#include "hazard.h"

#include <cassert>

namespace sas {

object_store::~object_store() {
    map_.visit_all([this](auto& e) {
        object_handle* h = e.second.handle.load(std::memory_order_relaxed);
        assert(h);
        assert(h->refcount.load(std::memory_order_relaxed) == 1);
        close(h);
    });
}

object_handle* object_store::get(std::string_view key) {
    auto& state = hazard_thread_state::get();
    object_handle* result = nullptr;

    map_.cvisit(key, [&](const auto& e) {
        result = g_domain->protect(e.second.handle, state.node());
        assert(result);
        result->refcount.fetch_add(1, std::memory_order_relaxed);
        g_domain->unprotect(state.node());
    });

    return result;
}

void object_store::close(object_handle* handle) {
    impl::drop_handle(handle);
}

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto& state = hazard_thread_state::get();
    auto new_handle = impl::make_handle(value, dtor);
    object_handle* old_handle = nullptr;

    map_.emplace_or_cvisit(key, new_handle.get(), [&](const auto& e) {
        old_handle = e.second.handle.exchange(new_handle.get(),
                                              std::memory_order_acq_rel);
        assert(old_handle);
    });

    new_handle.release();

    if (old_handle) {
        state.retire(old_handle);
    }
}

} // namespace sas
