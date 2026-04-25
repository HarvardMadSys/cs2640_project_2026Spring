#pragma once

#include <array>
#include <atomic>
#include <boost/lockfree/queue.hpp>
#include <vector>

#include "handle.h"

namespace sas {

class hazard_domain {
  public:
    struct retired_batch {
        static constexpr size_t CAPACITY = 256;
        std::array<object_handle*, CAPACITY> handles;
        size_t size{0};
    };

    struct alignas(64) hazard_node {
        std::atomic<object_handle*> ptr{nullptr};
        std::atomic<bool> active{true};
        std::vector<object_handle*> orphaned;
        hazard_node* next{nullptr};
    };

    explicit hazard_domain();
    ~hazard_domain();
    hazard_node* acquire_node();
    object_handle* protect(const std::atomic<object_handle*>& shared_ptr,
                           hazard_node* node);
    void unprotect(hazard_node* node);
    void scan_and_reclaim(retired_batch& retired, std::vector<object_handle*>& active);

  private:
    std::atomic<hazard_node*> head_{nullptr};
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
    hazard_domain::retired_batch retired_;
    std::vector<object_handle*> active_;
};

extern std::unique_ptr<hazard_domain> g_domain;

} // namespace sas
