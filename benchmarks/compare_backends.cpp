#include <memory>
#include <string_view>

#include "benchmark.h"
#include "handle.h"
#include "hazard.h"
#include "sharded_store.h"
#include "store.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;
static std::unique_ptr<sas::bench::sharded_store> g_sharded;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    const size_t mixed_capacity = static_cast<size_t>(cfg.num_keys) * 2;
    const size_t fill_inserts = static_cast<size_t>(cfg.num_keys);
    const size_t fill_presized_cap = fill_inserts * 2;
    constexpr size_t fill_resize_cap = 1024;

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::object_store>(mixed_capacity);
    g_sharded = std::make_unique<sas::bench::sharded_store>(mixed_capacity);

    auto get_lockfree = [](std::string_view key) {
        auto* h = sas::g_store->get(key);
        if (h) {
            sas::g_store->close(h);
        }
        return h;
    };
    auto put_lockfree = [](std::string_view key, int* value) {
        sas::g_store->put(key, value, nullptr);
    };
    auto create_lockfree = [](size_t cap) {
        sas::g_store = std::make_unique<sas::object_store>(cap);
    };
    auto destroy_lockfree = []() { sas::g_store.reset(); };

    auto get_sharded = [](std::string_view key) {
        auto* h = g_sharded->get(key);
        if (h) {
            g_sharded->close(h);
        }
        return h;
    };
    auto put_sharded = [](std::string_view key, int* value) {
        g_sharded->put(key, value, nullptr);
    };
    auto create_sharded = [](size_t cap) {
        g_sharded = std::make_unique<sas::bench::sharded_store>(cap);
    };
    auto destroy_sharded = []() { g_sharded.reset(); };

    // Bench-name convention: "<backend>" for the mixed scenario, and
    // "<backend>_<scenario>" for the others. The plotting script groups by
    // scenario suffix and uses the backend prefix as the line label.
    sas::bench::register_mixed(cfg, "lockfree", get_lockfree, put_lockfree);
    sas::bench::register_fill(cfg, "lockfree_fill_presized", fill_presized_cap,
                              fill_inserts, create_lockfree, destroy_lockfree,
                              put_lockfree);
    sas::bench::register_fill(cfg, "lockfree_fill_resize", fill_resize_cap,
                              fill_inserts, create_lockfree, destroy_lockfree,
                              put_lockfree);

    sas::bench::register_mixed(cfg, "sharded", get_sharded, put_sharded);
    sas::bench::register_fill(cfg, "sharded_fill_presized", fill_presized_cap,
                              fill_inserts, create_sharded, destroy_sharded,
                              put_sharded);
    sas::bench::register_fill(cfg, "sharded_fill_resize", fill_resize_cap,
                              fill_inserts, create_sharded, destroy_sharded,
                              put_sharded);

    sas::bench::run_benchmarks();
}
