#include "test_main.hpp"

int main() {
    extern void RegisterConfigTests();
    extern void RegisterParserTests();
    extern void RegisterNormalizeTests();
    extern void RegisterUsersTests();
    extern void RegisterStatsTests();
    extern void RegisterMatrixTests();
    RegisterConfigTests();
    RegisterParserTests();
    RegisterNormalizeTests();
    RegisterUsersTests();
    RegisterStatsTests();
    RegisterMatrixTests();
    return ::tiny_test::ReportSummary();
}
