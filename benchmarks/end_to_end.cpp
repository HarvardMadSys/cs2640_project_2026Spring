#include "benchmark.h"
#include "client.h"

extern "C" void entry(int) {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);

    sas::bench::register_benchmarks(
        cfg, "end_to_end",
        [](std::string_view key) { return sas::get<int>(key); },
        [](std::string_view key, int* value) {
            sas::put<int, nullptr>(key, value);
        });

    sas::bench::run_benchmarks();
}
