#pragma once

#include <cstdio>
#include <cstdlib>
#include <string>

namespace tiny_test {

inline int& FailCount() { static int n = 0; return n; }
inline int& CheckCount() { static int n = 0; return n; }

inline void ReportFailure(const char* file, int line, const std::string& msg) {
    std::fprintf(stderr, "FAIL %s:%d %s\n", file, line, msg.c_str());
    ++FailCount();
}

inline int ReportSummary() {
    std::fprintf(stderr, "checks=%d failures=%d\n", CheckCount(), FailCount());
    return FailCount() == 0 ? 0 : 1;
}

}  // namespace tiny_test

#define TINY_CHECK(cond)                                                        \
    do {                                                                        \
        ++::tiny_test::CheckCount();                                            \
        if (!(cond)) {                                                          \
            ::tiny_test::ReportFailure(__FILE__, __LINE__, #cond);              \
        }                                                                       \
    } while (0)

#define TINY_CHECK_EQ(a, b)                                                     \
    do {                                                                        \
        ++::tiny_test::CheckCount();                                            \
        auto _av = (a);                                                         \
        auto _bv = (b);                                                         \
        if (!(_av == _bv)) {                                                    \
            std::string _m = std::string("CHECK_EQ(" #a ", " #b ") ");          \
            _m += "got=";                                                       \
            ::tiny_test::ReportFailure(__FILE__, __LINE__, _m);                 \
        }                                                                       \
    } while (0)
