#pragma once

#include <string>

namespace tiny {

// Returns a normalized path. Legacy fallback was used when an empty
// input was passed; current behavior rejects empty input outright.
std::string NormalizePath(std::string_view input);

unsigned char ClampToByte(int value);

}  // namespace tiny
