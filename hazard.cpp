#include <algorithm>
#include <cassert>
#include <vector>

#include "handle.h"
#include "hazard.h"

namespace sas {

hazard_domain::hazard_domain() {}

hazard_domain::~hazard_domain() {
    auto* curr = head_.load();
    while (curr) {
        for (auto* handle : curr->orphaned) {
            impl::free_handle(handle);
        }

        auto* next = curr->next;
        delete curr;
        curr = next;
    }
}

hazard_domain::hazard_node* hazard_domain::acquire_node() {
    auto* curr = head_.load(std::memory_order_acquire);
    while (curr) {
        if (!curr->active.load(std::memory_order_relaxed)) {
            bool expected = false;
            if (curr->active.compare_exchange_strong(
                    expected, true, std::memory_order_acquire)) {
                return curr;
            }
        }
        curr = curr->next;
    }

    auto* node = new hazard_node();
    node->next = head_.load(std::memory_order_relaxed);
    while (!head_.compare_exchange_weak(
        node->next, node, std::memory_order_release, std::memory_order_relaxed))
        ;
    return node;
}

object_handle*
hazard_domain::protect(const std::atomic<object_handle*>& shared_ptr,
                       hazard_node* node) {
    object_handle* ptr;
    while (true) {
        ptr = shared_ptr.load(std::memory_order_acquire);
        if (!ptr) {
            break;
        }

        node->ptr.store(ptr, std::memory_order_seq_cst);
        if (shared_ptr.load(std::memory_order_acquire) == ptr) {
            break;
        }
    }
    return ptr;
}

void hazard_domain::unprotect(hazard_node* node) {
    node->ptr.store(nullptr, std::memory_order_release);
}

void hazard_domain::scan_and_reclaim(retired_batch& retired,
                                     std::vector<object_handle*>& active) {
    active.clear();
    auto* curr = head_.load(std::memory_order_acquire);
    while (curr) {
        if (curr->active.load(std::memory_order_acquire)) {
            auto* hp = curr->ptr.load(std::memory_order_seq_cst);
            if (hp) {
                active.push_back(hp);
            }
        }
        curr = curr->next;
    }

    if (active.empty()) {
        for (size_t i = 0; i < retired.size; ++i) {
            impl::drop_handle(retired.handles[i]);
        }
        retired.size = 0;
        return;
    }

    std::sort(active.begin(), active.end());
    active.erase(std::unique(active.begin(), active.end()), active.end());

    size_t survivor_count = 0;
    for (size_t i = 0; i < retired.size; ++i) {
        auto* handle = retired.handles[i];
        if (std::binary_search(active.begin(), active.end(), handle)) {
            retired.handles[survivor_count++] = handle;
        } else {
            impl::drop_handle(handle);
        }
    }

    retired.size = survivor_count;
}

hazard_thread_state::hazard_thread_state() : domain_(*g_domain) {
    node_ = domain_.acquire_node();
    for (auto* handle : node_->orphaned) {
        retire(handle);
    }
    node_->orphaned.clear();
}

hazard_thread_state::~hazard_thread_state() {
    node_->ptr.store(nullptr, std::memory_order_release);
    domain_.scan_and_reclaim(retired_, active_);
    node_->orphaned.insert(node_->orphaned.end(), retired_.handles.begin(),
                           retired_.handles.begin() + retired_.size);
    node_->active.store(false, std::memory_order_release);
}

void hazard_thread_state::retire(object_handle* handle) {
    retired_.handles[retired_.size++] = handle;
    if (retired_.size == retired_.CAPACITY) {
        domain_.scan_and_reclaim(retired_, active_);
    }
}

hazard_domain::hazard_node* hazard_thread_state::node() { return node_; }

hazard_thread_state& hazard_thread_state::get() {
    thread_local hazard_thread_state state;
    return state;
}

} // namespace sas
