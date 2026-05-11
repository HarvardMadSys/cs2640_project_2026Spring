#pragma once

#include <dlfcn.h>
#include <print>
#include <thread>
#include <utility>
#include <vector>

namespace sas::host {

template <typename Setup, typename Teardown>
inline int run(int argc, char* argv[], Setup&& setup, Teardown&& teardown) {
    if (argc < 2) {
        std::println(stderr, "usage: {} client.so [client.so ...]", argv[0]);
        return 1;
    }

    using entry_fn = void (*)(int);
    std::vector<std::pair<void*, entry_fn>> clients;

    for (int i = 1; i < argc; ++i) {
        void* handle = dlopen(argv[i], RTLD_NOW | RTLD_GLOBAL);
        if (!handle) {
            std::println(stderr, "dlopen {}: {}", argv[i], dlerror());
            continue;
        }
        auto fn = reinterpret_cast<entry_fn>(dlsym(handle, "entry"));
        if (!fn) {
            std::println(stderr, "dlsym entry in {}: {}", argv[i], dlerror());
            dlclose(handle);
            continue;
        }
        clients.push_back({handle, fn});
    }

    setup();

    {
        std::vector<std::thread> threads;
        threads.reserve(clients.size());
        for (auto& [handle, fn] : clients) {
            threads.emplace_back(fn, threads.size());
        }
        for (auto& t : threads) {
            t.join();
        }
    }

    teardown();

    for (auto& [handle, fn] : clients) {
        dlclose(handle);
    }

    return 0;
}

} // namespace sas::host
