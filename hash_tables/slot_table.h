#pragma once

#include <array>
#include <atomic>
#include <cstddef>

namespace sas {

template <typename T, size_t ChunkSize = 64> class slot_table {
  public:
    slot_table() = default;
    slot_table(const slot_table&) = delete;
    slot_table& operator=(const slot_table&) = delete;

    ~slot_table() {
        chunk* c = head_.load(std::memory_order_relaxed);
        while (c) {
            chunk* next = c->next.load(std::memory_order_relaxed);
            delete c;
            c = next;
        }
    }

    T* acquire() {
        for (chunk* c = head_.load(std::memory_order_acquire); c;
             c = c->next.load(std::memory_order_acquire)) {
            size_t used = c->used.load(std::memory_order_acquire);
            for (size_t i = 0; i < used; ++i) {
                if (!c->slots[i].active.load(std::memory_order_relaxed)) {
                    bool expected = false;
                    if (c->slots[i].active.compare_exchange_strong(
                            expected, true, std::memory_order_acquire)) {
                        return &c->slots[i];
                    }
                }
            }
        }
        return append_new();
    }

    void release(T* slot) noexcept {
        slot->active.store(false, std::memory_order_release);
    }

    template <typename Fn> void for_each(Fn&& fn) {
        for (chunk* c = head_.load(std::memory_order_acquire); c;
             c = c->next.load(std::memory_order_acquire)) {
            size_t used = c->used.load(std::memory_order_acquire);
            for (size_t i = 0; i < used; ++i) {
                fn(c->slots[i]);
            }
        }
    }

    template <typename Fn> void for_each_active(Fn&& fn) {
        for (chunk* c = head_.load(std::memory_order_acquire); c;
             c = c->next.load(std::memory_order_acquire)) {
            size_t used = c->used.load(std::memory_order_acquire);
            for (size_t i = 0; i < used; ++i) {
                if (c->slots[i].active.load(std::memory_order_acquire)) {
                    fn(c->slots[i]);
                }
            }
        }
    }

  private:
    struct chunk {
        std::array<T, ChunkSize> slots;
        std::atomic<size_t> used{0};
        std::atomic<chunk*> next{nullptr};
    };

    T* append_new() {
        while (true) {
            chunk* head = head_.load(std::memory_order_acquire);
            if (head) {
                size_t i = head->used.load(std::memory_order_acquire);
                while (i < ChunkSize) {
                    head->slots[i].active.store(true,
                                                std::memory_order_relaxed);
                    size_t expected = i;
                    if (head->used.compare_exchange_weak(
                            expected, i + 1, std::memory_order_release,
                            std::memory_order_acquire)) {
                        return &head->slots[i];
                    }
                    i = expected;
                }
            }
            chunk* fresh = new chunk();
            fresh->slots[0].active.store(true, std::memory_order_relaxed);
            fresh->used.store(1, std::memory_order_relaxed);
            fresh->next.store(head, std::memory_order_relaxed);
            if (head_.compare_exchange_strong(head, fresh,
                                              std::memory_order_release,
                                              std::memory_order_acquire)) {
                return &fresh->slots[0];
            }
            delete fresh;
        }
    }

    std::atomic<chunk*> head_{nullptr};
};

} // namespace sas
