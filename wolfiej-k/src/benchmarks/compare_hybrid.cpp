#include <memory>
#include <string_view>

#include "benchmark.h"
#include "hazard.h"
#include "hybrid.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::hybrid_store> sas::g_store;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::hybrid_store>();

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

    sas::bench::run_benchmarks(cfg, "hybrid", get, put);
}
