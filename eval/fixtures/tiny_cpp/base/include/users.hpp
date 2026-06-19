#pragma once

#include <optional>
#include <string>
#include <vector>

namespace tiny {

struct User {
    std::string name;
    int id = 0;
};

// FindUser returns true if a user with the given id is known.
// Returning bool means callers cannot tell apart "missing" from "error".
bool FindUser(int id, User* out);

std::vector<User> ListUsers();

}  // namespace tiny
