#pragma once

#include <atomic>
#include <cstdint>
#include <type_traits>

namespace sas {

template <typename T> class tagged_ptr {
  public:
    constexpr tagged_ptr() noexcept : raw_(0) {}

    constexpr tagged_ptr(T* p, bool frozen = false) noexcept
        : raw_(reinterpret_cast<uintptr_t>(p) | (frozen ? 1 : 0)) {}

    T* ptr() const noexcept {
        return reinterpret_cast<T*>(raw_ & PTR_MASK);
    }

    bool is_frozen() const noexcept { return (raw_ & TAG_MASK) != 0; }

    tagged_ptr freeze() const noexcept {
        tagged_ptr copy = *this;
        copy.raw_ |= TAG_MASK;
        return copy;
    }

    tagged_ptr unfreeze() const noexcept {
        tagged_ptr copy = *this;
        copy.raw_ &= PTR_MASK;
        return copy;
    }

    T* operator->() const noexcept
        requires(!std::is_void_v<T>)
    {
        return ptr();
    }

    std::add_lvalue_reference_t<T> operator*() const noexcept
        requires(!std::is_void_v<T>)
    {
        return *ptr();
    }

    explicit operator bool() const noexcept { return ptr() != nullptr; }

    bool operator==(const tagged_ptr& other) const noexcept {
        return raw_ == other.raw_;
    }
    bool operator!=(const tagged_ptr& other) const noexcept {
        return raw_ != other.raw_;
    }

  private:
    uintptr_t raw_;

    static constexpr uintptr_t TAG_MASK = 1ULL;
    static constexpr uintptr_t PTR_MASK = ~TAG_MASK;
};

static_assert(std::is_trivially_copyable_v<tagged_ptr<int>>);
static_assert(sizeof(tagged_ptr<int>) == sizeof(void*));

template <typename T> using atomic_tagged_ptr = std::atomic<tagged_ptr<T>>;

} // namespace sas
