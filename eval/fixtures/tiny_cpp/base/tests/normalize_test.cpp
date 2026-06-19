#include "test_main.hpp"
#include "normalize.hpp"

void RegisterNormalizeTests() {
    // Baseline tests assert the legacy behavior. Tasks 003 and 004
    // change the implementation; the oracle verifies post-task state.
    TINY_CHECK_EQ(tiny::NormalizePath("a//b"), std::string("a/b"));
    TINY_CHECK_EQ(tiny::ClampToByte(-5), 0);
    TINY_CHECK_EQ(tiny::ClampToByte(0), 0);
    TINY_CHECK_EQ(tiny::ClampToByte(255), 255);
    TINY_CHECK_EQ(tiny::ClampToByte(256), 255);
    TINY_CHECK_EQ(tiny::ClampToByte(128), 128);
}
