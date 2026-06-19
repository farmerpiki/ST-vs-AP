#include "shape.hpp"

#include "vec.hpp"

#include <cmath>

namespace geo {

double TriangleArea(double ax, double ay, double bx, double by) {
    Vec2 a{ax, ay};
    Vec2 b{bx, by};
    return std::fabs(Cross(a, b)) / 2.0;
}

}  // namespace geo
