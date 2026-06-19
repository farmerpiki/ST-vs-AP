add_executable(tiny_tests
  test_main.cpp
  config_test.cpp
  parser_test.cpp
  normalize_test.cpp
  users_test.cpp
  stats_test.cpp
  matrix_test.cpp
)
target_link_libraries(tiny_tests PRIVATE tiny_core)
target_include_directories(tiny_tests PRIVATE ../include)
add_test(NAME tiny_tests COMMAND tiny_tests)
