#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>

typedef struct sas_handle sas_handle_t;

sas_handle_t* sas_get(const char* key, size_t key_len);
void* sas_deref(sas_handle_t* handle);
void sas_close(sas_handle_t* handle);
void sas_put(const char* key, size_t key_len, void* value,
             void (*dtor)(void* value));

void entry(int cid);

#ifdef __cplusplus
}

#include <iterator>
#include <memory>
#include <string_view>
#include <type_traits>
#include <utility>

namespace sas {

template <typename T> struct ref {
    using value_type = T;
    explicit ref(sas_handle_t* h) noexcept : h_(h) {}
    ~ref() {
        if (h_) {
            ::sas_close(h_);
        }
    }
    ref(ref&& o) noexcept : h_(std::exchange(o.h_, nullptr)) {}
    ref& operator=(ref&& o) noexcept {
        if (this != &o) {
            if (h_) {
                ::sas_close(h_);
            }
            h_ = std::exchange(o.h_, nullptr);
        }
        return *this;
    }
    ref(const ref&) = delete;
    ref& operator=(const ref&) = delete;
    T* get() const noexcept { return static_cast<T*>(::sas_deref(h_)); }
    T* operator->() const noexcept
        requires(!std::is_void_v<T>)
    {
        return get();
    }
    std::add_lvalue_reference_t<T> operator*() const noexcept
        requires(!std::is_void_v<T>)
    {
        return *get();
    }
    explicit operator bool() const noexcept { return h_ != nullptr; }

  private:
    sas_handle_t* h_;
};

static_assert(std::indirectly_readable<ref<char>>);
static_assert(!std::indirectly_readable<ref<void>>);

template <typename T> inline ref<T> get(std::string_view key) {
    return ref<T>{::sas_get(key.data(), key.size())};
}

template <typename T, void (*Dtor)(T*)>
inline void put(std::string_view key, T* value) {
    if constexpr (Dtor == nullptr) {
        ::sas_put(key.data(), key.size(), value, nullptr);
    } else {
        ::sas_put(key.data(), key.size(), value,
                  [](void* p) { Dtor(static_cast<T*>(p)); });
    }
}

template <typename T> inline void put(std::string_view key, T* value) {
    ::sas_put(key.data(), key.size(), value,
              [](void* p) { delete static_cast<T*>(p); });
}

template <typename T, typename D>
    requires std::is_empty_v<D> && std::is_default_constructible_v<D>
inline void put(std::string_view key, std::unique_ptr<T, D> value) {
    auto* raw = value.release();
    ::sas_put(key.data(), key.size(), raw, [](void* p) {
        D d{};
        d(static_cast<T*>(p));
    });
}

inline void publish(std::string_view key) {
    ::sas_put(key.data(), key.size(), nullptr, nullptr);
}

inline ref<void> poll(std::string_view key) {
    return ref<void>{::sas_get(key.data(), key.size())};
}

} // namespace sas
#endif
