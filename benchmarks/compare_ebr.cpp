#include <memory>
#include <string_view>

#include "benchmark.h"
#include "ebr.h"
#include "ebr_store.h"

std::unique_ptr<sas::ebr::ebr_domain> sas::ebr::g_domain;
std::unique_ptr<sas::ebr::object_store> sas::ebr::g_store;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    const size_t mixed_capacity = static_cast<size_t>(cfg.num_keys) * 2;
    const size_t fill_inserts = static_cast<size_t>(cfg.num_keys);
    const size_t fill_presized_cap = fill_inserts * 2;
    constexpr size_t fill_resize_cap = 1024;

    sas::ebr::g_domain = std::make_unique<sas::ebr::ebr_domain>();
    sas::ebr::g_store =
        std::make_unique<sas::ebr::object_store>(mixed_capacity);

    auto get = [](std::string_view key) {
        auto* h = sas::ebr::g_store->get(key);
        if (h) {
            sas::ebr::g_store->close(h);
        }
        return h;
    };
    auto put = [](std::string_view key, int* value) {
        sas::ebr::g_store->put(key, value, nullptr);
    };
    auto create = [](size_t cap) {
        sas::ebr::g_store = std::make_unique<sas::ebr::object_store>(cap);
    };
    auto destroy = []() { sas::ebr::g_store.reset(); };

    sas::bench::register_mixed(cfg, "ebr", get, put);
    sas::bench::register_fill(cfg, "ebr_fill_presized", fill_presized_cap,
                              fill_inserts, create, destroy, put);
    sas::bench::register_fill(cfg, "ebr_fill_resize", fill_resize_cap,
                              fill_inserts, create, destroy, put);

    sas::bench::run_benchmarks(cfg);
}
