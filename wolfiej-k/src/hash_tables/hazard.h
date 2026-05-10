#pragma once

#include <array>
#include <atomic>
#include <boost/unordered/unordered_flat_set.hpp>
#include <memory>
#include <vector>

#include "common.h"
#include "handle.h"
#include "hash_table.h"
#include "slot_table.h"
#include "tagged_ptr.h"

namespace sas::hp {

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
                ::sas::impl::drop_handle(static_cast<object_handle*>(ptr()));
            } else {
                ::sas::impl::free_table(static_cast<hash_table*>(ptr()));
            }
        }
    };

    struct alignas(64) hazard_node {
        std::array<std::atomic<void*>, 2> ptrs{{nullptr, nullptr}};
        std::atomic<bool> active{false};
        std::vector<retired_entry> orphaned;
    };

    explicit hazard_domain();
    ~hazard_domain();
    hazard_node* acquire_node();
    void release_node(hazard_node* node) noexcept { slots_.release(node); }

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

    void scan_and_reclaim(std::vector<retired_entry>& retired,
                          boost::unordered_flat_set<void*>& active);

  private:
    slot_table<hazard_node> slots_;
};

class hazard_thread_state {
  public:
    static constexpr size_t SCAN_THRESHOLD = 256;

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
    std::vector<hazard_domain::retired_entry> retired_;
    boost::unordered_flat_set<void*> active_;
    size_t retire_counter_{0};

    void push_retire(tagged_ptr<void> ptr);
};

} // namespace sas::hp

namespace sas {
using hazard_domain = hp::hazard_domain;
extern std::unique_ptr<hazard_domain> g_domain;
} // namespace sas
