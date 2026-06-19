#include <string>

namespace tiny {

bool IsWhitespace(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

}  // namespace tiny
