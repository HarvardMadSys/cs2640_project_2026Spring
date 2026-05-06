#include "benchmark.h"
#include "client.h"
#include "hp_store.h"

extern "C" void entry(int) {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    sas::g_store = std::make_unique<sas::object_store>(
        static_cast<size_t>(cfg.num_keys) * 2);

    sas::bench::register_mixed(
        cfg, "end_to_end",
        [](std::string_view key) { return sas::get<int>(key); },
        [](std::string_view key, int* value) {
            sas::put<int, nullptr>(key, value);
        });

    sas::bench::run_benchmarks(cfg);
}
