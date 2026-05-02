#include <atomic>
#include <cassert>

#define XXH_INLINE_ALL
#include "xxhash.h"

#include "hash_table.h"
#include "hazard.h"
#include "memory_pool.h"
#include "store.h"

namespace sas {

namespace {

thread_local memory_pool<hash_node, NODE_POOL_CAPACITY> node_pool_;

size_t next_power_of_two(size_t n) {
    size_t cap = 1;
    while (cap < n) {
        cap <<= 1;
    }
    return cap;
}

size_t hash(std::string_view s) { return XXH3_64bits(s.data(), s.size()); }

hash_node* make_node(std::string_view key, size_t hval, object_handle* h) {
    if (auto* n = node_pool_.acquire()) [[likely]] {
        n->key.assign(key.data(), key.size());
        n->hash = hval;
        n->handle.store(tagged_ptr<object_handle>(h),
                        std::memory_order_relaxed);
        n->next.store(tagged_ptr<hash_node>(), std::memory_order_relaxed);
        return n;
    }
    return new hash_node(key, hval, h);
}

void free_node(hash_node* n) noexcept { node_pool_.release(n); }

void drain_bucket(atomic_tagged_ptr<hash_node>& bucket, bool close_handles) {
    auto curr = bucket.load(std::memory_order_relaxed).ptr();
    while (curr) {
        if (close_handles) {
            auto h = curr->handle.load(std::memory_order_relaxed);
            if (h.ptr()) {
                impl::drop_handle(h.ptr());
            }
        }
        auto* next = curr->next.load(std::memory_order_relaxed).ptr();
        free_node(curr);
        curr = next;
    }
}

} // namespace

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
    auto& state = hazard_thread_state::get();
    size_t hval = hash(key);

    while (true) {
        hash_table* table = state.acquire_table(table_);
        if (!table) [[unlikely]] {
            return nullptr;
        }

        if (auto* next = table->next_table.load(std::memory_order_acquire))
            [[unlikely]] {
            help_migrate_one(table, next);
            try_complete_resize(table, next);
            continue;
        }

        size_t idx = hval & (table->capacity - 1);
        auto curr = table->buckets[idx].load(std::memory_order_acquire);
        if (curr.is_frozen()) [[unlikely]] {
            continue;
        }

        object_handle* result = nullptr;
        while (curr) {
            if (curr->hash == hval && curr->key == key) {
                result = g_domain->protect(curr->handle, state.node());
                if (result) {
                    result->refcount.fetch_add(1, std::memory_order_relaxed);
                }
                break;
            }
            curr = curr->next.load(std::memory_order_acquire);
        }

        g_domain->unprotect<object_handle>(state.node());
        return result;
    }
}

void object_store::close(object_handle* handle) { impl::drop_handle(handle); }

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto& state = hazard_thread_state::get();
    auto new_handle = impl::make_handle(value, dtor);
    size_t hval = hash(key);

    while (true) {
        auto* table = state.acquire_table(table_);
        if (auto* next = table->next_table.load(std::memory_order_acquire))
            [[unlikely]] {
            help_migrate_one(table, next);
            try_complete_resize(table, next);
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
                        return;
                    }
                }
                break;
            }

            curr = curr->next.load(std::memory_order_acquire).ptr();
        }

        if (retry_table) [[unlikely]] {
            continue;
        }

        hash_node* new_node = make_node(key, hval, new_handle.get());
        new_node->next.store(head.ptr(), std::memory_order_relaxed);

        if (bucket.compare_exchange_strong(head, new_node,
                                           std::memory_order_release,
                                           std::memory_order_acquire)) {
            new_handle.release();

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
            return;
        }

        free_node(new_node);
    }
}

void object_store::trigger_resize(hash_table* old) {
    if (old->next_table.load(std::memory_order_acquire) != nullptr) {
        return;
    }

    auto* candidate = new hash_table(old->capacity * 2);

    hash_table* expected = nullptr;
    if (!old->next_table.compare_exchange_strong(expected, candidate,
                                                 std::memory_order_acq_rel,
                                                 std::memory_order_acquire)) {
        delete[] candidate->buckets;
        delete candidate;
    }
}

void object_store::help_migrate_one(hash_table* old, hash_table* next) {
    constexpr size_t CHUNK = 64;
    size_t start =
        old->migration_cursor.fetch_add(CHUNK, std::memory_order_acq_rel);
    if (start >= old->capacity) {
        return;
    }
    size_t end = std::min(start + CHUNK, old->capacity);
    for (size_t i = start; i < end; ++i) {
        migrate_bucket(old, next, i);
    }
    old->migration_done.fetch_add(end - start, std::memory_order_acq_rel);
}

void object_store::migrate_bucket(hash_table* old, hash_table* next,
                                  size_t idx) {
    auto& src = old->buckets[idx];
    auto head = src.load(std::memory_order_acquire);
    while (!head.is_frozen()) {
        if (src.compare_exchange_weak(head, head.freeze(),
                                      std::memory_order_acq_rel,
                                      std::memory_order_acquire)) {
            break;
        }
    }

    hash_node* curr = head.ptr();
    while (curr) {
        auto h = curr->handle.load(std::memory_order_acquire);
        while (!h.is_frozen()) {
            if (curr->handle.compare_exchange_weak(h, h.freeze(),
                                                   std::memory_order_acq_rel,
                                                   std::memory_order_acquire)) {
                break;
            }
        }

        if (h.ptr()) {
            size_t new_idx = curr->hash & (next->capacity - 1);
            auto* copy = make_node(curr->key, curr->hash, h.ptr());

            while (true) {
                auto next_head =
                    next->buckets[new_idx].load(std::memory_order_acquire);
                copy->next.store(next_head, std::memory_order_relaxed);
                if (next->buckets[new_idx].compare_exchange_weak(
                        next_head, copy, std::memory_order_release,
                        std::memory_order_acquire)) {
                    break;
                }
            }

            size_t shard_idx = curr->hash & hash_table::SHARD_MASK;
            next->shards[shard_idx].v.fetch_add(1, std::memory_order_relaxed);
        }

        curr = curr->next.load(std::memory_order_relaxed).ptr();
    }
}

void object_store::try_complete_resize(hash_table* old, hash_table* next) {
    if (old->migration_done.load(std::memory_order_acquire) < old->capacity) {
        return;
    }

    hash_table* expected = old;
    if (table_.compare_exchange_strong(expected, next,
                                       std::memory_order_acq_rel,
                                       std::memory_order_acquire)) {
        auto& state = hazard_thread_state::get();
        state.retire(old);
    }
}

namespace impl {

void free_table(hash_table* table) {
    for (size_t i = 0; i < table->capacity; ++i) {
        hash_node* curr =
            table->buckets[i].load(std::memory_order_relaxed).ptr();
        while (curr) {
            hash_node* next = curr->next.load(std::memory_order_relaxed).ptr();
            free_node(curr);
            curr = next;
        }
    }
    delete[] table->buckets;
    delete table;
}

void init_node_pool() noexcept {
    node_pool_.prefill([] { return new hash_node({}, 0, nullptr); });
}

} // namespace impl

} // namespace sas
