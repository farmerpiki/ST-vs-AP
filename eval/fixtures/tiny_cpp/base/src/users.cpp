#include "users.hpp"

#include <vector>

namespace tiny {

namespace {
const std::vector<User>& KnownUsers() {
    static const std::vector<User> kUsers = {
        {"root", 0},
        {"alice", 1},
        {"bob", 2},
    };
    return kUsers;
}
}  // namespace

bool FindUser(int id, User* out) {
    for (const auto& u : KnownUsers()) {
        if (u.id == id) {
            if (out) *out = u;
            return true;
        }
    }
    return false;
}

std::vector<User> ListUsers() {
    return KnownUsers();
}

}  // namespace tiny
