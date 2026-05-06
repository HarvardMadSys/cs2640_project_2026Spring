#include <memory>
#include <string_view>

#include "benchmark.h"
#include "hazard.h"
#include "hp_store.h"
#include "hybrid.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;
static std::unique_ptr<sas::bench::hybrid_store> g_hybrid;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    g_hybrid = std::make_unique<sas::bench::hybrid_store>();

    auto get = [](std::string_view key) {
        auto* h = g_hybrid->get(key);
        if (h) {
            g_hybrid->close(h);
        }
        return h;
    };
    auto put = [](std::string_view key, int* value) {
        g_hybrid->put(key, value, nullptr);
    };

    sas::bench::run_benchmarks(cfg, "hybrid", get, put);
}
