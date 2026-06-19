cmake_minimum_required(VERSION 3.28)
project(modules_cpp CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Header-based build. The task converts vec and shape into C++20 module
# interface units; the model must move their sources into a
# FILE_SET CXX_MODULES on this target.
add_executable(app
  src/app.cpp
  src/shape.cpp
  src/vec.cpp
)
target_include_directories(app PRIVATE src)
