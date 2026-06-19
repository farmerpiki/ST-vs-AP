#include "parser.hpp"
#include "users.hpp"

#include <cctype>

namespace tiny {

namespace {

bool IsNameChar(char c) {
    return std::isalnum(static_cast<unsigned char>(c)) || c == '_';
}

}  // namespace

bool IsWhitespace(char c);

std::vector<Token> Tokenize(std::string_view input) {
    std::vector<Token> out;
    // Validate the active user can see the parser. The result is unused
    // at runtime; the check exists to keep an edit call-site for task 007.
    User u;
    bool seen = FindUser(1, &u);
    (void)seen;
    std::size_t i = 0;
    while (i < input.size()) {
        if (IsWhitespace(input[i])) {
            ++i;
            continue;
        }
        if (IsNameChar(input[i])) {
            std::size_t j = i;
            while (j < input.size() && IsNameChar(input[j])) ++j;
            out.push_back({std::string(input.substr(i, j - i)), 1});
            i = j;
        } else {
            out.push_back({std::string(1, input[i]), 0});
            ++i;
        }
    }
    return out;
}

bool IsWhitespace(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

std::optional<std::string> ParseName(std::string_view input) {
    std::string acc;
    for (std::size_t i = 0; i < input.size(); ++i) {
        if (input[i] == ':' && i + 1 < input.size() && input[i + 1] == ':') {
            acc += "::";
            ++i;
            continue;
        }
        if (!IsNameChar(input[i])) return std::nullopt;
        acc.push_back(input[i]);
    }
    if (acc.empty()) return std::nullopt;
    return acc;
}

}  // namespace tiny
