#include <algorithm>
#include <cassert>
#include <vector>

#include "hazard.h"

namespace sas {

hazard_domain::hazard_domain() : orphan_q_(1024) {
    gc_thread_ = std::thread(&hazard_domain::gc_loop, this);
}

hazard_domain::~hazard_domain() {
    shutdown_.store(true, std::memory_order_release);
    work_sem_.release();
    gc_thread_.join();

    auto* curr = head_.load();
    while (curr) {
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

void hazard_domain::scan_and_reclaim(std::vector<object_handle*>& retired) {
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

void hazard_domain::orphan_retired(object_handle* handle) {
    orphan_q_.push(handle);
}

void hazard_domain::notify_work() { work_sem_.release(); }

void hazard_domain::gc_loop() {
    std::vector<object_handle*> local_retired;
    local_retired.reserve(256);

    auto drain = [&]() {
        auto* curr = head_.load(std::memory_order_acquire);
        while (curr) {
            auto* batch =
                curr->pending.exchange(nullptr, std::memory_order_acq_rel);
            if (batch) {
                local_retired.insert(local_retired.end(),
                                     batch->handles.begin(),
                                     batch->handles.begin() + batch->size);
                batch->size = 0;
                batch->in_flight.store(false, std::memory_order_release);
            }
            curr = curr->next;
        }
        object_handle* h;
        while (orphan_q_.pop(h)) {
            local_retired.push_back(h);
        }
        if (!local_retired.empty()) {
            scan_and_reclaim(local_retired);
        }
    };

    while (!shutdown_.load(std::memory_order_acquire)) {
        work_sem_.acquire();
        drain();
    }

    drain();
    for (auto* handle : local_retired) {
        impl::free_handle(handle);
    }
    local_retired.clear();
}

hazard_thread_state::hazard_thread_state() : domain_(*g_domain) {
    node_ = domain_.acquire_node();
}

hazard_thread_state::~hazard_thread_state() {
    node_->ptr.store(nullptr, std::memory_order_release);
    for (auto& buf : buffers_) {
        while (buf.in_flight.load(std::memory_order_acquire)) {
            domain_.notify_work();
            while (buf.in_flight.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
        }
    }

    auto& buf = buffers_[cur_];
    for (size_t i = 0; i < buf.size; ++i) {
        domain_.orphan_retired(buf.handles[i]);
    }

    if (buf.size > 0) {
        domain_.notify_work();
    }

    node_->active.store(false, std::memory_order_release);
}

void hazard_thread_state::retire(object_handle* handle) {
    auto& buf = buffers_[cur_];
    buf.handles[buf.size++] = handle;
    if (buf.size == retired_batch::CAPACITY) {
        if (buffers_[cur_ ^ 1].in_flight.load(std::memory_order_acquire)) {
            domain_.notify_work();
            while (
                buffers_[cur_ ^ 1].in_flight.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
        }

        buf.in_flight.store(true, std::memory_order_release);
        node_->pending.store(&buf, std::memory_order_release);
        domain_.notify_work();
        cur_ ^= 1;
        buffers_[cur_].size = 0;
    }
}

hazard_domain::hazard_node* hazard_thread_state::node() { return node_; }

hazard_thread_state& hazard_thread_state::get() {
    thread_local hazard_thread_state state;
    return state;
}

} // namespace sas
