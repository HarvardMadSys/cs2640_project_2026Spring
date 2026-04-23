#pragma once

#include <array>
#include <atomic>
#include <boost/lockfree/queue.hpp>
#include <semaphore>
#include <thread>
#include <vector>

#include "handle.h"

namespace sas {

struct retired_batch {
    static constexpr size_t CAPACITY = 64;
    size_t size{0};
    std::array<object_handle*, CAPACITY> handles;
    std::atomic<bool> in_flight{false};
};

class hazard_domain {
  public:
    struct alignas(64) hazard_node {
        std::atomic<object_handle*> ptr{nullptr};
        std::atomic<bool> active{true};
        hazard_node* next{nullptr};
        std::atomic<retired_batch*> pending{nullptr};
    };

    explicit hazard_domain();
    ~hazard_domain();
    hazard_node* acquire_node();
    object_handle* protect(const std::atomic<object_handle*>& shared_ptr,
                           hazard_node* node);
    void unprotect(hazard_node* node);
    void orphan_retired(object_handle* handle);
    void notify_work();

  private:
    void gc_loop();
    void scan_and_reclaim(std::vector<object_handle*>& retired);

    std::atomic<hazard_node*> head_{nullptr};
    boost::lockfree::queue<object_handle*> orphan_q_;
    std::counting_semaphore<(1 << 20)> work_sem_{0};
    std::atomic<bool> shutdown_{false};
    std::thread gc_thread_;
};

class hazard_thread_state {
  public:
    hazard_thread_state();
    ~hazard_thread_state();
    void retire(object_handle* handle);
    hazard_domain::hazard_node* node();
    static hazard_thread_state& get();

  private:
    hazard_domain& domain_;
    hazard_domain::hazard_node* node_;
    std::array<retired_batch, 2> buffers_;
    size_t cur_{0};
};

extern std::unique_ptr<hazard_domain> g_domain;

} // namespace sas
