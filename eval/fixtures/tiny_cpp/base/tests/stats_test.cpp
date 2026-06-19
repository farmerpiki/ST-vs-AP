#include "test_main.hpp"
#include "stats.hpp"

#include <cmath>
#include <vector>

namespace {
bool ApproxEq(double a, double b) {
    return std::fabs(a - b) < 1e-9;
}
}

void RegisterStatsTests() {
    // Median
    TINY_CHECK(ApproxEq(tiny::Median({1, 2, 3, 4, 5}), 3.0));
    TINY_CHECK(ApproxEq(tiny::Median({1, 2, 3, 4}), 2.5));
    TINY_CHECK(ApproxEq(tiny::Median({4, 1, 3, 2}), 2.5));
    TINY_CHECK(ApproxEq(tiny::Median({}), 0.0));
    TINY_CHECK(ApproxEq(tiny::Median({7}), 7.0));

    // Mean
    TINY_CHECK(ApproxEq(tiny::Mean({1, 2, 3, 4}), 2.5));
    TINY_CHECK(ApproxEq(tiny::Mean({}), 0.0));
    TINY_CHECK(ApproxEq(tiny::Mean({5}), 5.0));

    // Sorted
    {
        auto s = tiny::Sorted({3, 1, 4, 1, 5, 9, 2, 6});
        TINY_CHECK_EQ(s.size(), (std::size_t)8);
        TINY_CHECK(ApproxEq(s[0], 1.0));
        TINY_CHECK(ApproxEq(s[7], 9.0));
    }

    // MinMaxBuckets
    {
        auto r = tiny::MinMaxBuckets({1, 2, 3, 4, 5, 6}, 3);
        TINY_CHECK_EQ(r.size(), (std::size_t)6);
        TINY_CHECK(ApproxEq(r[0], 1.0)); TINY_CHECK(ApproxEq(r[1], 2.0));
        TINY_CHECK(ApproxEq(r[2], 3.0)); TINY_CHECK(ApproxEq(r[3], 4.0));
        TINY_CHECK(ApproxEq(r[4], 5.0)); TINY_CHECK(ApproxEq(r[5], 6.0));
    }
}
