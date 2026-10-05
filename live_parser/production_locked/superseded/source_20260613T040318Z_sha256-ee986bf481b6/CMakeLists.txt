cmake_minimum_required(VERSION 3.16)
project(ofi_live_parser CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Release build with max optimisation
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native -DNDEBUG")

find_package(PkgConfig REQUIRED)
pkg_check_modules(SIMDJSON REQUIRED simdjson)

add_executable(ofi_live_parser src/ofi_live_parser.cpp)

target_include_directories(ofi_live_parser PRIVATE
    include
    ${SIMDJSON_INCLUDE_DIRS}
)
target_link_libraries(ofi_live_parser PRIVATE
    ${SIMDJSON_LIBRARIES}
)
target_compile_options(ofi_live_parser PRIVATE ${SIMDJSON_CFLAGS_OTHER})
