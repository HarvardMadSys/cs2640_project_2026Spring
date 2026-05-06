#include <memory>
#include <string_view>

#include "benchmark.h"
#include "hazard.h"
#include "hp_store.h"
#include "sharded.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;
static std::unique_ptr<sas::bench::sharded_store> g_sharded;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    g_sharded = std::make_unique<sas::bench::sharded_store>();

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

    sas::bench::run_benchmarks(cfg, "sharded", get, put);
}
