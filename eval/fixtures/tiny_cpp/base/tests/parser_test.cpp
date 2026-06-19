#include "test_main.hpp"
#include "parser.hpp"

void RegisterParserTests() {
    // Smoke: ParseName("foo") should yield "foo".
    auto r = tiny::ParseName("foo");
    TINY_CHECK(r.has_value());
    if (r) TINY_CHECK_EQ(*r, std::string("foo"));

    // Empty input must now be rejected.
    auto e = tiny::ParseName("");
    TINY_CHECK(!e.has_value());
}
