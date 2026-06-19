#pragma once

namespace geo {

struct Vec2 {
    double x;
    double y;
};

Vec2 Add(Vec2 a, Vec2 b);
double Cross(Vec2 a, Vec2 b);

}  // namespace geo
