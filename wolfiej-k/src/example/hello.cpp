#include <cstdlib>
#include <print>

#include "client.h"

extern "C" void entry(int cid) {
    auto* msg = static_cast<char*>(std::malloc(32));
    std::snprintf(msg, 32, "hello");
    sas::put<char, [](char* p) { std::free(p); }>("greeting", msg);

    auto h = sas::get<char>("greeting");
    std::println("{}: {}", cid, h ? h.get() : "(null)");
}
