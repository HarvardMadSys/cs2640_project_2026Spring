#include <atomic>
#include <cassert>
#include <functional>
#include <thread>

#include "hazard.h"
#include "store.h"

namespace sas {

namespace {

size_t next_power_of_two(size_t n) {
    size_t cap = 1;
    while (cap < n) {
        cap <<= 1;
    }
    return cap;
}

size_t hash(std::string_view s) { return std::hash<std::string_view>{}(s); }

} // namespace

object_store::object_store(size_t initial_capacity) {
    size_t cap = next_power_of_two(initial_capacity);
    table_.store(new hash_table(cap), std::memory_order_relaxed);
}

object_store::~object_store() {
    hash_table* t = table_.load(std::memory_order_relaxed);
    for (size_t i = 0; i < t->capacity; ++i) {
        auto curr = t->buckets[i].load(std::memory_order_relaxed);
        while (curr) {
            auto h = curr->handle.load(std::memory_order_relaxed);
            if (h) {
                assert(h->refcount.load(std::memory_order_relaxed) == 1);
                close(h.ptr());
            }
            auto next = curr->next.load(std::memory_order_relaxed);
            delete curr.ptr();
            curr = next;
        }
    }
    delete[] t->buckets;
    delete t;
}

object_handle* object_store::get(std::string_view key) {
    auto& state = hazard_thread_state::get();
    object_handle* result = nullptr;

    hash_table* table = g_domain->protect(table_, state.node());
    if (!table) {
        return nullptr;
    }

    size_t hval = hash(key);
    size_t idx = hval & (table->capacity - 1);
    auto curr = table->buckets[idx].load(std::memory_order_acquire);

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
    g_domain->unprotect<hash_table>(state.node());

    return result;
}

void object_store::close(object_handle* handle) { impl::drop_handle(handle); }

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto& state = hazard_thread_state::get();
    auto new_handle = impl::make_handle(value, dtor);

    while (true) {
        auto* table = g_domain->protect(table_, state.node());
        size_t hval = hash(key);
        size_t idx = hval & (table->capacity - 1);
        auto& bucket = table->buckets[idx];

        auto head = bucket.load(std::memory_order_acquire);
        if (head.is_frozen()) {
            g_domain->unprotect<hash_table>(state.node());
            std::this_thread::yield();
            continue;
        }

        hash_node* curr = head.ptr();
        size_t bucket_depth = 0;
        bool retry_table = false;

        while (curr) {
            if (curr->hash == hval && curr->key == key) {
                auto old_handle = curr->handle.load(std::memory_order_acquire);
                while (true) {
                    if (old_handle.is_frozen()) {
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
                        g_domain->unprotect<hash_table>(state.node());
                        return;
                    }
                }
                break;
            }

            bucket_depth++;
            curr = curr->next.load(std::memory_order_acquire).ptr();
        }

        if (retry_table) {
            g_domain->unprotect<hash_table>(state.node());
            std::this_thread::yield();
            continue;
        }

        hash_node* new_node = new hash_node(key, hval, new_handle.get());
        new_node->next.store(head.ptr(), std::memory_order_relaxed);

        if (bucket.compare_exchange_strong(head, new_node,
                                           std::memory_order_release,
                                           std::memory_order_acquire)) {
            new_handle.release();
            g_domain->unprotect<hash_table>(state.node());
            if (bucket_depth >= 4) {
                resize(table);
            }
            return;
        }

        delete new_node;
        g_domain->unprotect<hash_table>(state.node());
    }
}

void object_store::resize(hash_table* expected) {
    std::lock_guard<std::mutex> lock(resize_mutex_);
    hash_table* old_table = table_.load(std::memory_order_relaxed);
    if (old_table != expected) {
        return;
    }

    size_t new_cap = old_table->capacity * 2;
    hash_table* new_table = new hash_table(new_cap);

    for (size_t i = 0; i < old_table->capacity; ++i) {
        auto head = old_table->buckets[i].load(std::memory_order_acquire);
        while (!head.is_frozen()) {
            if (old_table->buckets[i].compare_exchange_weak(head,
                                                            head.freeze())) {
                break;
            }
        }

        hash_node* curr = head.ptr();
        while (curr) {
            auto h = curr->handle.load(std::memory_order_acquire);
            while (!h.is_frozen()) {
                if (curr->handle.compare_exchange_weak(h, h.freeze())) {
                    break;
                }
            }

            hash_node* copy = new hash_node(curr->key, curr->hash, h.ptr());
            size_t new_idx = copy->hash & (new_cap - 1);

            copy->next.store(
                new_table->buckets[new_idx].load(std::memory_order_relaxed),
                std::memory_order_relaxed);
            new_table->buckets[new_idx].store(copy, std::memory_order_relaxed);

            curr = curr->next.load(std::memory_order_relaxed).ptr();
        }
    }

    table_.store(new_table, std::memory_order_release);

    auto& state = hazard_thread_state::get();
    state.retire(old_table);
}

namespace impl {

void free_table(hash_table* table) {
    for (size_t i = 0; i < table->capacity; ++i) {
        hash_node* curr =
            table->buckets[i].load(std::memory_order_relaxed).ptr();
        while (curr) {
            hash_node* next = curr->next.load(std::memory_order_relaxed).ptr();
            delete curr;
            curr = next;
        }
    }
    delete[] table->buckets;
    delete table;
}

} // namespace impl

} // namespace sas
