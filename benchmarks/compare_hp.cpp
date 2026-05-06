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

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::object_store>();

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

    sas::bench::run_benchmarks(cfg, "hp", get, put);
}
