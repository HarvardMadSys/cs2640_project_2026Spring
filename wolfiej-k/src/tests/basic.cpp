#include <cassert>
#include <string>

#include "client.h"

extern "C" void entry(int) {
    assert(!sas::get<int>("missing"));
    assert(!sas::poll("missing"));

    static int v1 = 42;
    sas::put<int, nullptr>("k1", &v1);
    {
        auto h = sas::get<int>("k1");
        assert(h);
        assert(h.get() == &v1);
        assert(*h == 42);
    }

    {
        auto h = sas::poll("k1");
        assert(h);
        assert(h.get() == &v1);
    }

    sas::publish("pub");
    {
        auto h = sas::poll("pub");
        assert(h);
        assert(h.get() == nullptr);
    }
    {
        auto h = sas::get<int>("pub");
        assert(h);
        assert(h.get() == nullptr);
    }

    static int v2 = 99;
    sas::put<int, nullptr>("k1", &v2);
    {
        auto h = sas::get<int>("k1");
        assert(h);
        assert(*h == 99);
        assert(h.get() == &v2);
    }

    static int a = 1, b = 2, c = 3;
    sas::put<int, nullptr>("ka", &a);
    sas::put<int, nullptr>("kb", &b);
    sas::put<int, nullptr>("kc", &c);
    assert(*sas::get<int>("ka") == 1);
    assert(*sas::get<int>("kb") == 2);
    assert(*sas::get<int>("kc") == 3);

    std::string long_key(512, 'z');
    static int lv = 7;
    sas::put<int, nullptr>(long_key, &lv);
    {
        auto h = sas::get<int>(long_key);
        assert(h);
        assert(*h == 7);
    }

    static int ev = 5;
    sas::put<int, nullptr>("", &ev);
    {
        auto h = sas::get<int>("");
        assert(h);
        assert(*h == 5);
    }
}
