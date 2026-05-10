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

    sas::ebr::g_domain = std::make_unique<sas::ebr::ebr_domain>();
    sas::ebr::g_store = std::make_unique<sas::ebr::object_store>();

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

    sas::bench::run_benchmarks(cfg, "ebr", get, put);
}
