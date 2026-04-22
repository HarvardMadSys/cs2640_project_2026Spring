#include <cassert>
#include <cstdlib>
#include <print>

#include "client.h"

static int dtor_count = 0;

static void counting_dtor(void* p) {
    ++dtor_count;
    std::free(p);
}

static void* val() { return std::malloc(8); }

extern "C" void entry(int) {
    dtor_count = 0;
    sas::put<void, counting_dtor>("k", val());
    {
        auto h = sas::get<void>("k");
        assert(h);
        assert(dtor_count == 0);
    }
    assert(dtor_count == 0);

    {
        static int dummy;
        sas::put<int>("null", &dummy);
        auto h = sas::get<int>("null");
        assert(*h == dummy);
    }

    dtor_count = 0;
    sas::put<void, counting_dtor>("ow", val());
    sas::put<void, counting_dtor>("ow", val());
    assert(dtor_count == 1);

    dtor_count = 0;
    {
        static int dummy;
        sas::put<int>("nd", &dummy);
        sas::put<void, counting_dtor>("nd", val());
        assert(dtor_count == 0);
    }

    dtor_count = 0;
    sas::put<void, counting_dtor>("d", val());
    {
        auto h = sas::get<void>("d");
        sas::put<void, counting_dtor>("d", val());
        assert(dtor_count == 0);
    }
    assert(dtor_count == 1);

    dtor_count = 0;
    sas::put<void, counting_dtor>("m", val());
    {
        auto h1 = sas::poll("m");
        auto h2 = sas::poll("m");
        sas::put<void, counting_dtor>("m", val());
        assert(dtor_count == 0);
        {
            auto moved = std::move(h1);
        }
        assert(dtor_count == 0);
    }
    assert(dtor_count == 1);

    assert(!sas::poll("missing"));

    std::println("Free test passed!");
}
