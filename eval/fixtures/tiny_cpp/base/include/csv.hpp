#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace tiny {

// Splits a CSV row on commas, with support for double-quoted fields.
// A field that begins and ends with a double-quote is unquoted, with
// embedded doubled-double-quotes ("") interpreted as a literal ".
// All other fields are returned verbatim. Empty input returns an empty
// vector. A trailing comma produces a trailing empty field.
std::vector<std::string> SplitCsvRow(std::string_view row);

// Joins a list of fields with commas, quoting any field that contains
// a comma, a double-quote, or a newline. Quotes inside a field are
// escaped as doubled double-quotes. Empty input returns an empty
// string.
std::string JoinCsvRow(const std::vector<std::string>& fields);

}  // namespace tiny
