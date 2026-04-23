#include <cassert>
#include <cstdlib>
#include <print>

#include "client.h"

static void counting_dtor(void* p) { std::free(p); }

static void* val() { return std::malloc(8); }

extern "C" void entry(int) {
    sas::put<void, counting_dtor>("k", val());
    {
        auto h = sas::get<void>("k");
        assert(h);
    }

    {
        static int dummy;
        sas::put<int, nullptr>("null", &dummy);
        auto h = sas::get<int>("null");
        assert(*h == dummy);
    }

    sas::put<void, counting_dtor>("ow", val());
    sas::put<void, counting_dtor>("ow", val());

    {
        static int dummy;
        sas::put<int, nullptr>("nd", &dummy);
        sas::put<void, counting_dtor>("nd", val());
    }

    {
        auto h = sas::get<void>("d");
        sas::put<void, counting_dtor>("d", val());
        (void)h;
    }

    {
        auto h1 = sas::poll("m");
        auto h2 = sas::poll("m");
        sas::put<void, counting_dtor>("m", val());
        {
            auto moved = std::move(h1);
        }
        (void)h2;
    }

    assert(!sas::poll("missing"));

    std::println("Free test passed!");
}
