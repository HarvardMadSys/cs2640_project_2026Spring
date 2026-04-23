#pragma once

#include <atomic>
#include <boost/lockfree/queue.hpp>

#include "handle.h"

namespace sas {

class hazard_domain {
  public:
    struct alignas(64) hazard_node {
        std::atomic<object_handle*> ptr{nullptr};
        std::atomic<bool> active{true};
        hazard_node* next{nullptr};
    };

    explicit hazard_domain();
    ~hazard_domain();
    hazard_node* acquire_node();
    object_handle* protect(const std::atomic<object_handle*>& shared_ptr,
                           hazard_node* node);
    void unprotect(hazard_node* node);
    void scan_and_reclaim(std::vector<object_handle*>& retired);
    void orphan_retired(const std::vector<object_handle*>& remaining);

  private:
    std::atomic<hazard_node*> head_{nullptr};
    boost::lockfree::queue<object_handle*> orphan_q_;
};

class hazard_thread_state {
  public:
    hazard_thread_state();
    ~hazard_thread_state();
    void retire(object_handle* handle);
    hazard_domain::hazard_node* node();
    std::vector<object_handle*>& retired();
    static hazard_thread_state& get();

  private:
    hazard_domain& domain_;
    hazard_domain::hazard_node* node_;
    std::vector<object_handle*> retired_;
};

extern std::unique_ptr<hazard_domain> g_domain;

} // namespace sas
