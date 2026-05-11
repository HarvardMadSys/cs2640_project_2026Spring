#pragma once

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <string_view>

#define XXH_INLINE_ALL
#include "xxhash.h"

#include "handle.h"
#include "hash_table.h"
#include "memory_pool.h"
#include "tagged_ptr.h"

namespace sas::impl {

inline thread_local memory_pool<hash_node, NODE_POOL_CAPACITY> node_pool;

inline size_t next_power_of_two(size_t n) noexcept {
    size_t cap = 1;
    while (cap < n) {
        cap <<= 1;
    }
    return cap;
}

inline size_t hash(std::string_view s) noexcept {
    return XXH3_64bits(s.data(), s.size());
}

inline hash_node* make_node(std::string_view key, size_t hval,
                            object_handle* h) {
    if (auto* n = node_pool.acquire()) [[likely]] {
        n->key.assign(key.data(), key.size());
        n->hash = hval;
        n->handle.store(tagged_ptr<object_handle>(h),
                        std::memory_order_relaxed);
        n->next.store(tagged_ptr<hash_node>(), std::memory_order_relaxed);
        return n;
    }
    return new hash_node(key, hval, h);
}

inline void free_node(hash_node* n) noexcept { node_pool.release(n); }

inline void drain_bucket(atomic_tagged_ptr<hash_node>& bucket,
                         bool close_handles) {
    auto curr = bucket.load(std::memory_order_relaxed).ptr();
    while (curr) {
        if (close_handles) {
            auto h = curr->handle.load(std::memory_order_relaxed);
            if (h.ptr()) {
                drop_handle(h.ptr());
            }
        }
        auto* next = curr->next.load(std::memory_order_relaxed).ptr();
        free_node(curr);
        curr = next;
    }
}

inline void free_table(hash_table* table) {
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

inline void init_node_pool() noexcept {
    node_pool.prefill([] { return new hash_node({}, 0, nullptr); });
}

inline void migrate_bucket(hash_table* old, hash_table* next, size_t idx) {
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

inline void help_migrate_one(hash_table* old, hash_table* next) {
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

inline void trigger_resize(hash_table* old) {
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

template <typename Retire>
inline void try_complete_resize(std::atomic<hash_table*>& table_ptr,
                                hash_table* old, hash_table* next,
                                Retire retire) {
    if (old->migration_done.load(std::memory_order_acquire) < old->capacity) {
        return;
    }

    hash_table* expected = old;
    if (table_ptr.compare_exchange_strong(expected, next,
                                          std::memory_order_acq_rel,
                                          std::memory_order_acquire)) {
        retire(old);
    }
}

} // namespace sas::impl
