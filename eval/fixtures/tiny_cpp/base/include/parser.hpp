#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace tiny {

struct Token {
    std::string text;
    int kind;
};

std::optional<std::string> ParseName(std::string_view input);
std::vector<Token> Tokenize(std::string_view input);

}  // namespace tiny
