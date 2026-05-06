cmake_minimum_required(VERSION 3.19)
project(sas_object_store CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED True)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON CACHE INTERNAL "")
add_compile_options(-Wall -Wextra)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(USE_SANITIZERS "Build with Address and Undefined Behavior Sanitizers" OFF)
if(USE_SANITIZERS)
    message(WARNING "Building with sanitizers enabled!")
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -g)
    add_link_options(-fsanitize=address,undefined)
else()
    add_compile_definitions(NDEBUG)
endif()

include(FetchContent)

FetchContent_Declare(
  boost
  URL https://archives.boost.io/release/1.84.0/source/boost_1_84_0.tar.gz
)
FetchContent_MakeAvailable(boost)
include_directories(${boost_SOURCE_DIR})

set(MI_BUILD_TESTS OFF CACHE BOOL "Disable mimalloc tests" FORCE)
set(MI_BUILD_SHARED OFF CACHE BOOL "Build static library" FORCE)
set(MI_BUILD_OBJECT OFF CACHE BOOL "Disable mimalloc object build" FORCE)
FetchContent_Declare(
    mimalloc
    GIT_REPOSITORY https://github.com/microsoft/mimalloc.git
    GIT_TAG        v2.1.2
)
FetchContent_MakeAvailable(mimalloc)

FetchContent_Declare(
  googlebenchmark
  GIT_REPOSITORY https://github.com/google/benchmark.git
    GIT_TAG v1.9.1
)
set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_GTEST_TESTS OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googlebenchmark)

FetchContent_Declare(
  hdrhistogram
  GIT_REPOSITORY https://github.com/HdrHistogram/HdrHistogram_c.git
  GIT_TAG 0.11.8
)
set(HDR_HISTOGRAM_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(hdrhistogram)

set(HP_SRCS hash_tables/hp_store.cpp hash_tables/hazard.cpp handle.cpp)
set(EBR_SRCS hash_tables/ebr_store.cpp hash_tables/ebr.cpp handle.cpp)
set(HOST_SRCS host.cpp ${HP_SRCS})
set(BENCH_INCLUDES
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/hash_tables"
    "${CMAKE_CURRENT_SOURCE_DIR}/external")

add_executable(host ${HOST_SRCS})
target_link_libraries(host PRIVATE dl pthread mimalloc-static)
set_target_properties(host PROPERTIES ENABLE_EXPORTS TRUE)
target_include_directories(host PRIVATE ${BENCH_INCLUDES})

function(add_plugin TARGET_NAME SRC_FILE OUT_DIR INCLUDE_DIRS)
    add_library(${TARGET_NAME} MODULE ${SRC_FILE})
    set_target_properties(${TARGET_NAME} PROPERTIES 
        PREFIX ""
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/${OUT_DIR}"
    )
    target_include_directories(${TARGET_NAME} PRIVATE ${INCLUDE_DIRS})
endfunction()

file(GLOB EXAMPLE_SRCS "example/*.cpp")
foreach(src IN LISTS EXAMPLE_SRCS)
    get_filename_component(name ${src} NAME_WE)
    add_plugin("example_${name}" ${src} "example" "${BENCH_INCLUDES}")
endforeach()

enable_testing()
file(GLOB TEST_SRCS "tests/*.cpp")
foreach(src IN LISTS TEST_SRCS)
    get_filename_component(name ${src} NAME_WE)
    add_plugin("test_${name}" ${src} "tests" "${BENCH_INCLUDES}")
    target_compile_options("test_${name}" PRIVATE -UNDEBUG)
    add_test(NAME "test_${name}" COMMAND host "$<TARGET_FILE:test_${name}>")
endforeach()

file(GLOB ALL_BENCH_SRCS "benchmarks/*.cpp")
foreach(src IN LISTS ALL_BENCH_SRCS)
    get_filename_component(name ${src} NAME_WE)
    if(name MATCHES "^compare_")
        if(name STREQUAL "compare_ebr")
            add_executable(${name} ${src} ${EBR_SRCS})
        else()
            add_executable(${name} ${src} ${HP_SRCS})
        endif()
        target_include_directories(${name} PRIVATE ${BENCH_INCLUDES})
        target_link_libraries(${name} PRIVATE 
            pthread 
            mimalloc-static 
            benchmark::benchmark
            hdr_histogram
        )
        set_target_properties(${name} PROPERTIES 
            RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/benchmarks"
        )
    else()
        add_plugin("bench_${name}" ${src} "benchmarks" "${BENCH_INCLUDES}")
        target_link_libraries("bench_${name}" PRIVATE 
            benchmark::benchmark
            hdr_histogram
        )
    endif()
endforeach()
