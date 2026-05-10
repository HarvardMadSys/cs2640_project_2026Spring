#include <cassert>
#include <type_traits>
#include <utility>

#include "client.h"

struct point {
    int x, y;
};

extern "C" void entry(int) {
    static point p{3, 4};
    sas::put<point, nullptr>("pt", &p);

    {
        auto h = sas::get<point>("pt");
        assert(h);
        assert(h->x == 3);
        assert(h->y == 4);
        assert((*h).x == 3);
        assert(h.get() == &p);
    }

    {
        auto h = sas::get<void>("pt");
        assert(h);
        assert(h.get() == &p);
    }

    sas::publish("exist");
    {
        auto h = sas::poll("exist");
        assert(h);
        assert(h.get() == nullptr);
    }

    {
        auto h1 = sas::get<point>("pt");
        assert(h1);
        auto h2 = std::move(h1);
        assert(!h1);
        assert(h2);
        assert(h2->x == 3);
    }

    {
        auto h1 = sas::get<point>("pt");
        auto h2 = sas::get<point>("pt");
        h2 = std::move(h1);
        assert(!h1);
        assert(h2);
        assert(h2->x == 3);
    }

    {
        auto h1 = sas::get<point>("pt");
        {
            auto h2 = std::move(h1);
            assert(!h1);
            assert(h2);
        }
        assert(!h1);
    }

    static_assert(!std::is_copy_constructible_v<sas::ref<int>>);
    static_assert(!std::is_copy_assignable_v<sas::ref<int>>);
    static_assert(std::is_move_constructible_v<sas::ref<int>>);
    static_assert(std::is_move_assignable_v<sas::ref<int>>);
}
