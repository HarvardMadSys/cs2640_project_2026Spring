#define ANKERL_NANOBENCH_IMPLEMENT
#include "benchmark.h"
#include "client.h"

extern "C" void entry(int) {
    auto cfg = sas::bench::load_config();
    std::println(std::cerr, "Loaded config: {}", cfg);
    auto bench = sas::bench::make_bench(cfg);
    bench.output(&std::cerr);

    sas::bench::run_benchmark(
        cfg, [](std::string_view key) { return sas::get<int>(key); },
        [](std::string_view key, int* value) {
            sas::put<int, nullptr>(key, value);
        },
        bench, "end_to_end");

    bench.render(ankerl::nanobench::templates::json(), std::cout);
}
