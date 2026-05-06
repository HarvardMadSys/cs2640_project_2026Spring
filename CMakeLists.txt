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
set(BENCH_INCLUDES
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/hash_tables"
    "${CMAKE_CURRENT_SOURCE_DIR}/external")

set(BACKENDS hp ebr sharded spinlock hybrid)

foreach(be IN LISTS BACKENDS)
    if(be STREQUAL "hp")
        set(host_target host)
        set(host_extra ${HP_SRCS})
    elseif(be STREQUAL "ebr")
        set(host_target host_ebr)
        set(host_extra ${EBR_SRCS})
    elseif(be STREQUAL "hybrid")
        set(host_target host_hybrid)
        set(host_extra ${HP_SRCS})
    else()
        set(host_target host_${be})
        set(host_extra handle.cpp)
    endif()
    string(TOUPPER "${be}" be_upper)
    add_executable(${host_target} host.cpp ${host_extra})
    target_compile_definitions(${host_target} PRIVATE "SAS_BACKEND_${be_upper}")
    target_link_libraries(${host_target} PRIVATE dl pthread mimalloc-static)
    set_target_properties(${host_target} PROPERTIES ENABLE_EXPORTS TRUE)
    target_include_directories(${host_target} PRIVATE ${BENCH_INCLUDES})
endforeach()

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

# Failures caused by timing assertion for frees. Checked that memory is not
# leaked indefinitely (new threads will reclaim old threads' memory), but for
# now this is not verified by the testing infrastructure.
set(EXPECTED_FAILURES test_ebr_advanced test_ebr_gc)

file(GLOB TEST_SRCS "tests/*.cpp")
foreach(src IN LISTS TEST_SRCS)
    get_filename_component(name ${src} NAME_WE)
    add_plugin("test_${name}" ${src} "tests" "${BENCH_INCLUDES}")
    target_compile_options("test_${name}" PRIVATE -UNDEBUG)
    foreach(be IN LISTS BACKENDS)
        if(be STREQUAL "hp")
            set(host_bin host)
        else()
            set(host_bin host_${be})
        endif()
        set(test_name "test_${be}_${name}")
        add_test(NAME "${test_name}"
                 COMMAND ${host_bin} "$<TARGET_FILE:test_${name}>")
        if(test_name IN_LIST EXPECTED_FAILURES)
            set_tests_properties("${test_name}" PROPERTIES DISABLED TRUE)
        endif()
    endforeach()
endforeach()

find_path(JNI_HDR_DIR jni.h
    HINTS ENV JAVA_HOME
    PATHS /usr/lib/jvm/default-java
          /usr/lib/jvm/java-17-openjdk-amd64
          /usr/lib/jvm/java-17-openjdk-arm64
    PATH_SUFFIXES include)
find_path(JNI_MD_DIR jni_md.h
    HINTS ${JNI_HDR_DIR}
    PATH_SUFFIXES linux darwin)
if(JNI_HDR_DIR AND JNI_MD_DIR)
    set(JNI_INCLUDE_DIRS ${JNI_HDR_DIR} ${JNI_MD_DIR})
    foreach(be IN LISTS BACKENDS)
        if(be STREQUAL "hp" OR be STREQUAL "hybrid")
            set(jni_extra ${HP_SRCS})
        elseif(be STREQUAL "ebr")
            set(jni_extra ${EBR_SRCS})
        else()
            set(jni_extra handle.cpp)
        endif()
        string(TOUPPER "${be}" be_upper)
        set(jni_target sas_jni_${be})
        add_library(${jni_target} SHARED
            benchmarks/ycsb/sas_jni.cpp ${jni_extra})
        target_compile_definitions(${jni_target}
            PRIVATE "SAS_BACKEND_${be_upper}")
        target_include_directories(${jni_target}
            PRIVATE ${BENCH_INCLUDES} ${JNI_INCLUDE_DIRS})
        target_link_libraries(${jni_target}
            PRIVATE pthread)
        set_target_properties(${jni_target} PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY
                "${CMAKE_CURRENT_BINARY_DIR}/benchmarks/ycsb"
            PREFIX "lib")
    endforeach()
endif()

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
