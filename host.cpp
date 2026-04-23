#include <cstddef>
#include <dlfcn.h>
#include <memory>
#include <mimalloc-new-delete.h>
#include <print>
#include <thread>
#include <vector>

#include "hazard.h"
#include "store.h"

std::unique_ptr<sas::hazard_domain> sas::g_domain;
std::unique_ptr<sas::object_store> sas::g_store;

extern "C" {
sas::object_handle* sas_get(const char* key, size_t key_len) {
    return sas::g_store->get({key, key_len});
}
void* sas_deref(sas::object_handle* handle) { return handle->value; }
void sas_close(sas::object_handle* handle) { sas::g_store->close(handle); }
void sas_put(const char* key, size_t key_len, void* value, sas::dtor_fn dtor) {
    sas::g_store->put({key, key_len}, value, dtor);
}
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::println(stderr, "usage: {} client.so [client.so ...]", argv[0]);
        return 1;
    }

    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::object_store>();

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

    sas::g_store.reset();
    sas::g_domain.reset();

    for (auto& [handle, fn] : clients) {
        dlclose(handle);
    }

    return 0;
}
