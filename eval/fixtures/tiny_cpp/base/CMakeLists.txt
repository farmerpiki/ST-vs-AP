cmake_minimum_required(VERSION 3.16)
project(tiny_cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(tiny_core STATIC
  src/config.cpp
  src/parser.cpp
  src/normalize.cpp
  src/util.cpp
  src/users.cpp
  src/stats.cpp
  src/matrix.cpp
  src/csv.cpp
)
target_include_directories(tiny_core PUBLIC include)

enable_testing()
add_subdirectory(tests)
