#include "vec.hpp"

namespace geo {

Vec2 Add(Vec2 a, Vec2 b) {
    return {a.x + b.x, a.y + b.y};
}

double Cross(Vec2 a, Vec2 b) {
    return a.x * b.y - a.y * b.x;
}

}  // namespace geo
