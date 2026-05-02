#pragma once

#include <array>
#include <cstddef>

namespace sas {

template <typename T, size_t Capacity> class memory_pool {
  public:
    ~memory_pool() {
        for (size_t i = 0; i < size_; ++i) {
            delete storage_[i];
        }
    }

    T* acquire() noexcept { return size_ > 0 ? storage_[--size_] : nullptr; }

    void release(T* p) noexcept {
        if (size_ < Capacity) {
            storage_[size_++] = p;
        } else {
            delete p;
        }
    }

    template <typename Factory> void prefill(Factory&& f) {
        while (size_ < Capacity) {
            release(f());
        }
    }

  private:
    std::array<T*, Capacity> storage_;
    size_t size_{0};
};

} // namespace sas
