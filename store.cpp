#include "store.h"
#include "hazard.h"
#include <cassert>
#include <cstdint>
#include <functional>
#include <thread>

namespace sas {

namespace {

template <typename T> inline bool is_frozen(T* p) {
    return (reinterpret_cast<uintptr_t>(p) & 1) != 0;
}

template <typename T> inline T* clear_frozen(T* p) {
    return reinterpret_cast<T*>(reinterpret_cast<uintptr_t>(p) & ~1ULL);
}

template <typename T> inline T* mark_frozen(T* p) {
    return reinterpret_cast<T*>(reinterpret_cast<uintptr_t>(p) | 1);
}

size_t next_power_of_two(size_t n) {
    size_t cap = 1;
    while (cap < n) {
        cap <<= 1;
    }
    return cap;
}

} // namespace

object_store::object_store(size_t initial_capacity) {
    size_t cap = next_power_of_two(initial_capacity);
    table_.store(new hash_table(cap), std::memory_order_relaxed);
}

object_store::~object_store() {
    hash_table* t = table_.load(std::memory_order_relaxed);
    for (size_t i = 0; i < t->capacity; ++i) {
        hash_node* curr =
            clear_frozen(t->buckets[i].load(std::memory_order_relaxed));
        while (curr) {
            object_handle* h =
                clear_frozen(curr->handle.load(std::memory_order_relaxed));
            if (h) {
                assert(h->refcount.load(std::memory_order_relaxed) == 1);
                close(h);
            }
            hash_node* next = curr->next.load(std::memory_order_relaxed);
            delete curr;
            curr = next;
        }
    }
    delete[] t->buckets;
    delete t;
}

object_handle* object_store::get(std::string_view key) {
    auto& state = hazard_thread_state::get();
    hazard_domain::hazard_node* hp_node = state.node();
    object_handle* result = nullptr;

    hash_table* table = g_domain->protect(table_, hp_node);
    if (!table) {
        return nullptr;
    }

    size_t idx = std::hash<std::string_view>{}(key) & (table->capacity - 1);
    hash_node* curr =
        clear_frozen(table->buckets[idx].load(std::memory_order_acquire));

    while (curr) {
        if (curr->key == key) {
            result = clear_frozen(g_domain->protect(curr->handle, hp_node));
            if (result) {
                result->refcount.fetch_add(1, std::memory_order_relaxed);
            }
            break;
        }
        curr = curr->next.load(std::memory_order_acquire);
    }

    g_domain->unprotect<object_handle>(hp_node);
    g_domain->unprotect<hash_table>(hp_node);

    return result;
}

void object_store::close(object_handle* handle) { impl::drop_handle(handle); }

void object_store::put(std::string_view key, void* value, dtor_fn dtor) {
    auto& state = hazard_thread_state::get();
    auto new_handle = impl::make_handle(value, dtor);
    hazard_domain::hazard_node* hp_node = state.node();

    while (true) {
        hash_table* table = g_domain->protect(table_, hp_node);
        size_t idx = std::hash<std::string_view>{}(key) & (table->capacity - 1);
        std::atomic<hash_node*>& bucket = table->buckets[idx];

        hash_node* head = bucket.load(std::memory_order_acquire);

        if (is_frozen(head)) {
            g_domain->unprotect<hash_table>(hp_node);
            std::this_thread::yield();
            continue;
        }

        hash_node* curr = head;
        size_t bucket_depth = 0;
        bool retry_table = false;

        while (curr) {
            if (curr->key == key) {
                object_handle* old_handle =
                    curr->handle.load(std::memory_order_acquire);
                while (true) {
                    if (is_frozen(old_handle)) {
                        retry_table = true;
                        break;
                    }
                    if (curr->handle.compare_exchange_weak(
                            old_handle, new_handle.get(),
                            std::memory_order_acq_rel)) {
                        if (old_handle) {
                            state.retire(old_handle);
                        }
                        new_handle.release();
                        g_domain->unprotect<hash_table>(hp_node);
                        return;
                    }
                }
                break;
            }
            bucket_depth++;
            curr = curr->next.load(std::memory_order_acquire);
        }

        if (retry_table) {
            g_domain->unprotect<hash_table>(hp_node);
            std::this_thread::yield();
            continue;
        }

        hash_node* new_node = new hash_node(key, new_handle.get());
        new_node->next.store(head, std::memory_order_relaxed);

        if (bucket.compare_exchange_strong(head, new_node,
                                           std::memory_order_release,
                                           std::memory_order_acquire)) {
            new_handle.release();
            g_domain->unprotect<hash_table>(hp_node);
            if (bucket_depth >= 4) {
                resize(table);
            }
            return;
        }

        delete new_node;
        g_domain->unprotect<hash_table>(hp_node);
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
        hash_node* head = old_table->buckets[i].load(std::memory_order_acquire);
        while (!is_frozen(head)) {
            if (old_table->buckets[i].compare_exchange_weak(
                    head, mark_frozen(head))) {
                break;
            }
        }
        head = clear_frozen(head);

        hash_node* curr = head;
        while (curr) {
            object_handle* h = curr->handle.load(std::memory_order_acquire);
            while (!is_frozen(h)) {
                if (curr->handle.compare_exchange_weak(h, mark_frozen(h))) {
                    break;
                }
            }
            h = clear_frozen(h);

            hash_node* copy = new hash_node(curr->key, h);
            size_t new_idx =
                std::hash<std::string_view>{}(copy->key) & (new_cap - 1);
            copy->next.store(
                new_table->buckets[new_idx].load(std::memory_order_relaxed),
                std::memory_order_relaxed);
            new_table->buckets[new_idx].store(copy, std::memory_order_relaxed);

            curr = curr->next.load(std::memory_order_relaxed);
        }
    }

    table_.store(new_table, std::memory_order_release);

    auto& state = hazard_thread_state::get();
    state.retire(old_table, [](void* p) {
        auto* t = static_cast<hash_table*>(p);
        for (size_t i = 0; i < t->capacity; ++i) {
            hash_node* curr =
                clear_frozen(t->buckets[i].load(std::memory_order_relaxed));
            while (curr) {
                hash_node* next = curr->next.load(std::memory_order_relaxed);
                delete curr;
                curr = next;
            }
        }
        delete[] t->buckets;
        delete t;
    });
}

} // namespace sas
