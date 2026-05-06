#include <atomic>
#include <cassert>

#include "common.h"
#include "ebr.h"
#include "ebr_store.h"
#include "hash_table.h"

namespace sas::ebr {

using ::sas::impl::drain_bucket;
using ::sas::impl::free_node;
using ::sas::impl::hash;
using ::sas::impl::help_migrate_one;
using ::sas::impl::make_node;
using ::sas::impl::next_power_of_two;
using ::sas::impl::trigger_resize;
using ::sas::impl::try_complete_resize;

object_store::object_store(size_t initial_capacity) {
    size_t cap = next_power_of_two(initial_capacity);
    table_.store(new hash_table(cap), std::memory_order_relaxed);
}

object_store::~object_store() {
    hash_table* t = table_.load(std::memory_order_relaxed);
    hash_table* next = t->next_table.load(std::memory_order_relaxed);

    for (size_t i = 0; i < t->capacity; ++i) {
        drain_bucket(t->buckets[i], /*close_handles*/ true);
    }
    delete[] t->buckets;
    delete t;

    if (next) {
        for (size_t i = 0; i < next->capacity; ++i) {
            drain_bucket(next->buckets[i], /*close_handles*/ false);
        }
        delete[] next->buckets;
        delete next;
    }
}

object_handle* object_store::get(std::string_view key) {
    auto& state = ebr_thread_state::get();
    state.enter();
    size_t hval = hash(key);

    object_handle* result = nullptr;
    while (true) {
        hash_table* table = state.acquire_table(table_);
        if (!table) [[unlikely]] {
            break;
        }

        if (auto* next = table->next_table.load(std::memory_order_acquire))
            [[unlikely]] {
            help_migrate_one(table, next);
            try_complete_resize(table_, table, next,
                                [&state](hash_table* t) { state.retire(t); });
            continue;
        }

        size_t idx = hval & (table->capacity - 1);
        auto curr = table->buckets[idx].load(std::memory_order_acquire);
        if (curr.is_frozen()) [[unlikely]] {
            continue;
        }

        while (curr) {
            if (curr->hash == hval && curr->key == key) {
                auto h = curr->handle.load(std::memory_order_acquire);
                if (!h.is_frozen() && h.ptr()) {
                    result = h.ptr();
                    result->refcount.fetch_add(1, std::memory_order_relaxed);
                }
                break;
            }
            curr = curr->next.load(std::memory_order_acquire);
        }
        break;
    }

    state.exit();
    return result;
}

void object_store::close(object_handle* handle) {
    ::sas::impl::drop_handle(handle);
}

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto& state = ebr_thread_state::get();
    auto new_handle = ::sas::impl::make_handle(value, dtor);
    size_t hval = hash(key);

    hash_node* new_node = state.take_cached_node();
    if (new_node) {
        new_node->key.assign(key.data(), key.size());
        new_node->hash = hval;
        new_node->handle.store(tagged_ptr<object_handle>(new_handle.get()),
                               std::memory_order_relaxed);
        new_node->next.store(tagged_ptr<hash_node>(),
                             std::memory_order_relaxed);
    } else {
        new_node = make_node(key, hval, new_handle.get());
    }
    bool consumed = false;

    state.enter();

    while (!consumed) {
        auto* table = state.acquire_table(table_);
        if (auto* next = table->next_table.load(std::memory_order_acquire))
            [[unlikely]] {
            help_migrate_one(table, next);
            try_complete_resize(table_, table, next,
                                [&state](hash_table* t) { state.retire(t); });
            continue;
        }

        size_t idx = hval & (table->capacity - 1);
        auto& bucket = table->buckets[idx];

        auto head = bucket.load(std::memory_order_acquire);
        if (head.is_frozen()) [[unlikely]] {
            continue;
        }

        hash_node* curr = head.ptr();
        bool retry_table = false;
        bool updated = false;

        while (curr) {
            if (curr->hash == hval && curr->key == key) {
                auto old_handle = curr->handle.load(std::memory_order_acquire);
                while (true) {
                    if (old_handle.is_frozen()) [[unlikely]] {
                        retry_table = true;
                        break;
                    }

                    if (curr->handle.compare_exchange_weak(
                            old_handle, new_handle.get(),
                            std::memory_order_acq_rel)) {
                        if (old_handle.ptr()) {
                            state.retire(old_handle.ptr());
                        }
                        new_handle.release();
                        updated = true;
                        break;
                    }
                }
                break;
            }

            curr = curr->next.load(std::memory_order_acquire).ptr();
        }

        if (retry_table) [[unlikely]] {
            continue;
        }
        if (updated) {
            break;
        }

        new_node->next.store(head.ptr(), std::memory_order_relaxed);
        if (bucket.compare_exchange_strong(head, new_node,
                                           std::memory_order_release,
                                           std::memory_order_acquire)) {
            new_handle.release();
            consumed = true;

            size_t shard_idx = hval & hash_table::SHARD_MASK;
            size_t shard_count = table->shards[shard_idx].v.fetch_add(
                                     1, std::memory_order_relaxed) +
                                 1;

            bool needs_resize = false;
            if (shard_count * hash_table::NUM_SHARDS >= table->capacity)
                [[unlikely]] {
                size_t total = 0;
                for (size_t i = 0; i < hash_table::NUM_SHARDS; ++i) {
                    total += table->shards[i].v.load(std::memory_order_relaxed);
                }
                needs_resize = total >= table->capacity;
            }

            if (needs_resize) [[unlikely]] {
                trigger_resize(table);
            }
        }
    }

    state.exit();
    if (!consumed) {
        state.put_cached_node(new_node);
    }
}

} // namespace sas::ebr
