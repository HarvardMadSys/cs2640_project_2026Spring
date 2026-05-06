#include <memory>
#include <string_view>

#include "benchmark.h"
#include "hazard.h"
#include "hp_store.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    const size_t mixed_capacity = static_cast<size_t>(cfg.num_keys) * 2;
    const size_t fill_inserts = static_cast<size_t>(cfg.num_keys);
    const size_t fill_presized_cap = fill_inserts * 2;
    constexpr size_t fill_resize_cap = 1024;

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::object_store>(mixed_capacity);

    auto get = [](std::string_view key) {
        auto* h = sas::g_store->get(key);
        if (h) {
            sas::g_store->close(h);
        }
        return h;
    };
    auto put = [](std::string_view key, int* value) {
        sas::g_store->put(key, value, nullptr);
    };
    auto create = [](size_t cap) {
        sas::g_store = std::make_unique<sas::object_store>(cap);
    };
    auto destroy = []() { sas::g_store.reset(); };

    sas::bench::register_mixed(cfg, "hp", get, put);
    sas::bench::register_fill(cfg, "hp_fill_presized", fill_presized_cap,
                              fill_inserts, create, destroy, put);
    sas::bench::register_fill(cfg, "hp_fill_resize", fill_resize_cap,
                              fill_inserts, create, destroy, put);

    sas::bench::run_benchmarks(cfg);
}
