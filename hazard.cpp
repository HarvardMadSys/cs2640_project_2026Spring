#include <algorithm>
#include <cassert>
#include <vector>

#include "hazard.h"
#include "store.h"

namespace sas {

hazard_domain::hazard_domain() {}

hazard_domain::~hazard_domain() {
    auto* curr = head_.load();
    while (curr) {
        for (auto& entry : curr->orphaned) {
            entry.free();
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

void hazard_domain::scan_and_reclaim(retired_batch& retired,
                                     std::vector<void*>& active) {
    active.clear();
    auto* curr = head_.load(std::memory_order_acquire);
    while (curr) {
        if (curr->active.load(std::memory_order_acquire)) {
            for (size_t i = 0; i < 2; ++i) {
                void* hp = curr->ptrs[i].load(std::memory_order_seq_cst);
                if (hp) {
                    hp = reinterpret_cast<void*>(
                        reinterpret_cast<uintptr_t>(hp) & ~1ULL);
                    active.push_back(hp);
                }
            }
        }
        curr = curr->next;
    }

    if (active.empty()) {
        for (size_t i = 0; i < retired.size; ++i) {
            retired.entries[i].free();
        }
        retired.size = 0;
        return;
    }

    std::sort(active.begin(), active.end());
    active.erase(std::unique(active.begin(), active.end()), active.end());

    size_t survivor_count = 0;
    for (size_t i = 0; i < retired.size; ++i) {
        auto& entry = retired.entries[i];
        if (std::binary_search(active.begin(), active.end(), entry.ptr())) {
            retired.entries[survivor_count++] = entry;
        } else {
            entry.free();
        }
    }

    retired.size = survivor_count;
}

template <> size_t hazard_domain::get_index<object_handle>() const noexcept {
    return 0;
}
template <> size_t hazard_domain::get_index<hash_table>() const noexcept {
    return 1;
}

hazard_thread_state::hazard_thread_state() : domain_(*g_domain) {
    impl::init_pool();
    node_ = domain_.acquire_node();
    for (auto& entry : node_->orphaned) {
        push_retire(entry);
    }
    node_->orphaned.clear();
}

hazard_thread_state::~hazard_thread_state() {
    for (size_t i = 0; i < node_->ptrs.size(); ++i) {
        node_->ptrs[i].store(nullptr, std::memory_order_release);
        node_->ptrs[i].store(nullptr, std::memory_order_release);
    }
    domain_.scan_and_reclaim(retired_, active_);
    node_->orphaned.insert(node_->orphaned.end(), retired_.entries.begin(),
                           retired_.entries.begin() + retired_.size);
    node_->active.store(false, std::memory_order_release);
}

void hazard_thread_state::retire(object_handle* ptr) {
    push_retire(tagged_ptr<void>(ptr, false));
}

void hazard_thread_state::retire(hash_table* ptr) {
    push_retire(tagged_ptr<void>(ptr, true));
}

void hazard_thread_state::push_retire(tagged_ptr<void> ptr) {
    retired_.entries[retired_.size++] = ptr;
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
