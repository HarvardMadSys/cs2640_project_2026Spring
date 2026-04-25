#define ANKERL_NANOBENCH_IMPLEMENT
#include <boost/unordered/concurrent_flat_map.hpp>
#include <memory>
#include <mimalloc-new-delete.h>
#include <string>
#include <string_view>

#include "benchmark.h"
#include "handle.h"
#include "hazard.h"
#include "store.h"

struct rwlock_store {
    struct key_hash {
        using is_transparent = void;
        size_t operator()(std::string_view sv) const noexcept {
            return std::hash<std::string_view>{}(sv);
        }
    };
    struct key_eq {
        using is_transparent = void;
        bool operator()(std::string_view a, std::string_view b) const noexcept {
            return a == b;
        }
    };

    boost::concurrent_flat_map<std::string, sas::object_handle*, key_hash,
                               key_eq>
        map_;

    rwlock_store() : map_(1024) {}

    ~rwlock_store() {
        map_.visit_all([](auto& e) { sas::impl::drop_handle(e.second); });
    }

    sas::object_handle* get(std::string_view key) {
        sas::object_handle* result = nullptr;
        map_.visit(key, [&](auto& e) {
            result = e.second;
            if (result) {
                result->refcount.fetch_add(1, std::memory_order_relaxed);
            }
        });
        return result;
    }

    void close(sas::object_handle* h) { sas::impl::drop_handle(h); }

    void put(std::string_view key, void* value, sas::dtor_fn dtor) {
        auto new_h = sas::impl::make_handle(value, dtor);
        sas::object_handle* old_h = nullptr;
        map_.emplace_or_visit(key, new_h.get(), [&](auto& e) {
            old_h = e.second;
            e.second = new_h.get();
        });
        new_h.release();
        if (old_h) {
            sas::impl::drop_handle(old_h);
        }
    }
};

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;
static std::unique_ptr<rwlock_store> g_rwlock;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::object_store>();
    g_rwlock = std::make_unique<rwlock_store>();

    sas::bench::register_benchmarks(
        cfg, "hazard_ptr",
        [](std::string_view key) {
            auto* h = sas::g_store->get(key);
            if (h) {
                sas::g_store->close(h);
            }
            return h;
        },
        [](std::string_view key, int* value) {
            sas::g_store->put(key, value, nullptr);
        });

    sas::bench::register_benchmarks(
        cfg, "rw_lock",
        [](std::string_view key) {
            auto* h = g_rwlock->get(key);
            if (h) {
                g_rwlock->close(h);
            }
            return h;
        },
        [](std::string_view key, int* value) {
            g_rwlock->put(key, value, nullptr);
        });

    sas::bench::run_benchmarks();
}
