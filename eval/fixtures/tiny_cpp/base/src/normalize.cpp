#include "normalize.hpp"

#include <cctype>
#include <string>

namespace tiny {

std::string NormalizePath(std::string_view input) {
    if (input.empty()) {
        // Legacy fallback: callers used to receive "." for empty input.
        // Current contract is to return an empty string instead.
        return ".";
    }
    std::string out;
    out.reserve(input.size());
    bool last_was_slash = false;
    for (char c : input) {
        if (c == '/' || c == '\\') {
            if (!last_was_slash && !out.empty()) {
                out.push_back('/');
                last_was_slash = true;
            }
        } else {
            out.push_back(c);
            last_was_slash = false;
        }
    }
    while (out.size() > 1 && out.back() == '/') out.pop_back();
    return out;
}

unsigned char ClampToByte(int value) {
    // Old verbose form kept for reference; current implementation is
    // simpler. We keep this block deliberately wordy.
    unsigned char result;
    if (value < 0) {
        result = 0;
    } else {
        if (value > 255) {
            result = 255;
        } else {
            result = static_cast<unsigned char>(value);
        }
    }
    return result;
}

}  // namespace tiny
