#include <memory>
#include <string_view>

#include "benchmark.h"
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

    g_sharded = std::make_unique<sas::bench::sharded_store>(mixed_capacity);

    auto get = [](std::string_view key) {
        auto* h = g_sharded->get(key);
        if (h) {
            g_sharded->close(h);
        }
        return h;
    };
    auto put = [](std::string_view key, int* value) {
        g_sharded->put(key, value, nullptr);
    };
    auto create = [](size_t cap) {
        g_sharded = std::make_unique<sas::bench::sharded_store>(cap);
    };
    auto destroy = []() { g_sharded.reset(); };

    sas::bench::register_mixed(cfg, "sharded", get, put);
    sas::bench::register_fill(cfg, "sharded_fill_presized", fill_presized_cap,
                              fill_inserts, create, destroy, put);
    sas::bench::register_fill(cfg, "sharded_fill_resize", fill_resize_cap,
                              fill_inserts, create, destroy, put);

    sas::bench::run_benchmarks();
}
