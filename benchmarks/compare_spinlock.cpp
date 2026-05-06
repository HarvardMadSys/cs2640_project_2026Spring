#include <memory>
#include <string_view>

#include "benchmark.h"
#include "hazard.h"
#include "hp_store.h"
#include "spinlock.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;
static std::unique_ptr<sas::bench::spinlock_store> g_spinlock;

int main() {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    g_spinlock = std::make_unique<sas::bench::spinlock_store>();

    auto get = [](std::string_view key) {
        auto* h = g_spinlock->get(key);
        if (h) {
            g_spinlock->close(h);
        }
        return h;
    };
    auto put = [](std::string_view key, int* value) {
        g_spinlock->put(key, value, nullptr);
    };

    sas::bench::run_benchmarks(cfg, "spinlock", get, put);
}
