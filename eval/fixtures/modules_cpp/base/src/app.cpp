#include "shape.hpp"

#include <cmath>
#include <cstdio>

int main() {
    double area = geo::TriangleArea(4.0, 0.0, 0.0, 3.0);
    if (std::fabs(area - 6.0) > 1e-9) {
        std::printf("FAIL area=%f\n", area);
        return 1;
    }
    std::printf("ok area=%f\n", area);
    return 0;
}
