#pragma once

#include <array>
#include <atomic>
#include <vector>

#include "handle.h"
#include "store.h"
#include "tagged_ptr.h"

namespace sas {

namespace impl {

template <typename T> inline size_t get_index() noexcept;
template <> inline size_t get_index<object_handle>() noexcept { return 0; }
template <> inline size_t get_index<hash_table>() noexcept { return 1; }

} // namespace impl

class hazard_domain {
  public:
    struct retired_entry : public tagged_ptr<void> {
        using tagged_ptr<void>::tagged_ptr;
        constexpr retired_entry(tagged_ptr<void> base) noexcept
            : tagged_ptr<void>(base) {}
        void free() {
            if (!is_frozen()) {
                impl::drop_handle(static_cast<object_handle*>(ptr()));
            } else {
                impl::free_table(static_cast<hash_table*>(ptr()));
            }
        }
    };

    struct retired_batch {
        static constexpr size_t CAPACITY = 256;
        std::array<retired_entry, CAPACITY> entries;
        size_t size{0};
    };

    struct alignas(64) hazard_node {
        std::array<std::atomic<void*>, 2> ptrs{{nullptr, nullptr}};
        std::atomic<bool> active{true};
        std::vector<retired_entry> orphaned;
        hazard_node* next{nullptr};
    };

    explicit hazard_domain();
    ~hazard_domain();
    hazard_node* acquire_node();

    template <typename T>
    T* protect(const std::atomic<T*>& shared_ptr, hazard_node* node) {
        T* ptr;
        while (true) {
            ptr = shared_ptr.load(std::memory_order_acquire);
            if (!ptr) {
                break;
            }

            node->ptrs[impl::get_index<T>()].store(ptr,
                                                   std::memory_order_seq_cst);
            if (shared_ptr.load(std::memory_order_acquire) == ptr) {
                break;
            }
        }
        return ptr;
    }

    template <typename T>
    T* protect(const atomic_tagged_ptr<T>& shared_ptr, hazard_node* node) {
        T* ptr;
        while (true) {
            auto tp = shared_ptr.load(std::memory_order_acquire);
            ptr = tp.ptr();
            if (!ptr) {
                break;
            }

            node->ptrs[impl::get_index<T>()].store(ptr,
                                                   std::memory_order_seq_cst);
            if (shared_ptr.load(std::memory_order_acquire) == tp) {
                break;
            }
        }
        return ptr;
    }

    template <typename T> void unprotect(hazard_node* node) {
        node->ptrs[impl::get_index<T>()].store(nullptr,
                                               std::memory_order_release);
    }

    void scan_and_reclaim(retired_batch& retired, std::vector<void*>& active);

  private:
    std::atomic<hazard_node*> head_{nullptr};
};

class hazard_thread_state {
  public:
    hazard_thread_state();
    ~hazard_thread_state();

    void retire(object_handle* handle);
    void retire(hash_table* handle);

    hash_table* acquire_table(const std::atomic<hash_table*>& src) noexcept;

    hazard_domain::hazard_node* node();
    static hazard_thread_state& get();

  private:
    hazard_domain& domain_;
    hazard_domain::hazard_node* node_;
    hazard_domain::retired_batch retired_;
    std::vector<void*> active_;

    void push_retire(tagged_ptr<void> ptr);
};

extern std::unique_ptr<hazard_domain> g_domain;

} // namespace sas
