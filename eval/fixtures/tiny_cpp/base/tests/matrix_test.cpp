#include "test_main.hpp"
#include "matrix.hpp"

#include <cmath>
#include <vector>

namespace {
bool ApproxEq(double a, double b) {
    return std::fabs(a - b) < 1e-9;
}
}

void RegisterMatrixTests() {
    // SumAll
    {
        tiny::Matrix m(3, 4);
        // Fill with 1..12
        double v = 1.0;
        for (std::size_t r = 0; r < 3; ++r) {
            for (std::size_t c = 0; c < 4; ++c) {
                m.at(r, c) = v;
                v += 1.0;
            }
        }
        TINY_CHECK_EQ(m.SumAll(), 78.0);
    }
    // SumPerRow
    {
        tiny::Matrix m(2, 3);
        double v = 1.0;
        for (std::size_t r = 0; r < 2; ++r) {
            for (std::size_t c = 0; c < 3; ++c) {
                m.at(r, c) = v;
                v += 1.0;
            }
        }
        // row 0 = 1+2+3=6, row 1 = 4+5+6=15
        auto row_sums = m.SumPerRow();
        TINY_CHECK_EQ(row_sums.size(), (std::size_t)2);
        TINY_CHECK(ApproxEq(row_sums[0], 6.0));
        TINY_CHECK(ApproxEq(row_sums[1], 15.0));
    }
    // SumPerColumn - this is the operation that gets reimplemented
    {
        tiny::Matrix m(2, 3);
        double v = 1.0;
        for (std::size_t r = 0; r < 2; ++r) {
            for (std::size_t c = 0; c < 3; ++c) {
                m.at(r, c) = v;
                v += 1.0;
            }
        }
        // col 0 = 1+4=5, col 1 = 2+5=7, col 2 = 3+6=9
        auto col_sums = m.SumPerColumn();
        TINY_CHECK_EQ(col_sums.size(), (std::size_t)3);
        TINY_CHECK(ApproxEq(col_sums[0], 5.0));
        TINY_CHECK(ApproxEq(col_sums[1], 7.0));
        TINY_CHECK(ApproxEq(col_sums[2], 9.0));
    }
    // TransposeSquare
    {
        tiny::Matrix m(3, 3);
        m.at(0, 0) = 1; m.at(0, 1) = 2; m.at(0, 2) = 3;
        m.at(1, 0) = 4; m.at(1, 1) = 5; m.at(1, 2) = 6;
        m.at(2, 0) = 7; m.at(2, 1) = 8; m.at(2, 2) = 9;
        tiny::TransposeSquare(m);
        TINY_CHECK_EQ(m.at(0, 0), 1); TINY_CHECK_EQ(m.at(0, 1), 4); TINY_CHECK_EQ(m.at(0, 2), 7);
        TINY_CHECK_EQ(m.at(1, 0), 2); TINY_CHECK_EQ(m.at(1, 1), 5); TINY_CHECK_EQ(m.at(1, 2), 8);
        TINY_CHECK_EQ(m.at(2, 0), 3); TINY_CHECK_EQ(m.at(2, 1), 6); TINY_CHECK_EQ(m.at(2, 2), 9);
    }
}
