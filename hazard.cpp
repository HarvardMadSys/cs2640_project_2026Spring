#include <algorithm>
#include <cassert>
#include <vector>

#include "hazard.h"

namespace sas {

hazard_domain::hazard_domain() : orphan_q_(1024) {}

hazard_domain::~hazard_domain() {
    auto* curr = head_.load();
    while (curr) {
        auto* next = curr->next;
        delete curr;
        curr = next;
    }

    object_handle* handle;
    while (orphan_q_.pop(handle)) {
        impl::free_handle(handle);
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

void hazard_domain::scan_and_reclaim(std::vector<object_handle*>& retired) {
    constexpr int MAX_ABSORBED = 32;
    object_handle* handle;
    int absorbed = 0;
    while (absorbed < MAX_ABSORBED && orphan_q_.pop(handle)) {
        retired.push_back(handle);
        absorbed++;
    }

    std::vector<object_handle*> active;
    auto* curr = head_.load(std::memory_order_seq_cst);
    while (curr) {
        if (curr->active.load(std::memory_order_acquire)) {
            auto* hp = curr->ptr.load(std::memory_order_seq_cst);
            if (hp) {
                active.push_back(hp);
            }
        }
        curr = curr->next;
    }

    std::sort(active.begin(), active.end());
    std::vector<object_handle*> survivors;
    survivors.reserve(retired.size());
    for (auto* handle : retired) {
        if (std::binary_search(active.begin(), active.end(), handle)) {
            survivors.push_back(handle);
        } else {
            impl::drop_handle(handle);
        }
    }

    retired = std::move(survivors);
}

void hazard_domain::orphan_retired(
    const std::vector<object_handle*>& remaining) {
    for (auto* ptr : remaining) {
        orphan_q_.push(ptr);
    }
}

hazard_thread_state::hazard_thread_state() : domain_(*g_domain) {
    node_ = domain_.acquire_node();
}

hazard_thread_state::~hazard_thread_state() {
    node_->ptr.store(nullptr, std::memory_order_release);
    node_->active.store(false, std::memory_order_release);

    if (!retired_.empty()) {
        domain_.scan_and_reclaim(retired_);
        if (!retired_.empty()) {
            domain_.orphan_retired(retired_);
        }
    }
}

void hazard_thread_state::retire(object_handle* handle) {
    constexpr size_t MAX_RETIRED = 64;
    retired_.push_back(handle);
    if (retired_.size() >= MAX_RETIRED) {
        domain_.scan_and_reclaim(retired_);
    }
}

hazard_domain::hazard_node* hazard_thread_state::node() { return node_; }

std::vector<object_handle*>& hazard_thread_state::retired() { return retired_; }

hazard_thread_state& hazard_thread_state::get() {
    thread_local hazard_thread_state state;
    return state;
}

} // namespace sas
