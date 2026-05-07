cmake_minimum_required(VERSION 3.16)

project(cs2640_final_project VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CTest)

find_package(Threads REQUIRED)

# ---------------------------------------------------------------------------
# Optional generated source documentation
# ---------------------------------------------------------------------------

find_package(Doxygen QUIET)

if(DOXYGEN_FOUND)
    add_custom_target(docs
        COMMAND ${DOXYGEN_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/Doxyfile
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Generating Doxygen HTML documentation"
        VERBATIM
    )
else()
    message(STATUS "Doxygen not found -- skipping docs target")
endif()

# ---------------------------------------------------------------------------
# Core library (TCP baseline - no RDMA dependency)
# ---------------------------------------------------------------------------

add_library(project_core
    src/kvstore/kv_store.cpp
    src/net/event_loop.cpp
    src/net/socket_utils.cpp
    src/protocol/text_protocol.cpp
)

target_include_directories(project_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(project_core
    PUBLIC
        Threads::Threads
)

# ---------------------------------------------------------------------------
# TCP baseline executables
# ---------------------------------------------------------------------------

add_executable(kv_server src/server/main.cpp)
target_link_libraries(kv_server PRIVATE project_core)

add_executable(kv_client src/client/main.cpp)
target_link_libraries(kv_client PRIVATE project_core)

add_executable(kv_benchmark benchmarks/kv_benchmark.cpp)
target_link_libraries(kv_benchmark PRIVATE project_core)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

add_executable(kvstore_tests tests/kv_store_test.cpp)
target_link_libraries(kvstore_tests PRIVATE project_core)

add_executable(protocol_tests tests/protocol_test.cpp)
target_link_libraries(protocol_tests PRIVATE project_core)

add_executable(kv_store_concurrency_test tests/kv_store_concurrency_test.cpp)
target_link_libraries(kv_store_concurrency_test PRIVATE project_core)

if(BUILD_TESTING)
    add_test(NAME kvstore_tests            COMMAND kvstore_tests)
    add_test(NAME protocol_tests           COMMAND protocol_tests)
    add_test(NAME kv_store_concurrency_test COMMAND kv_store_concurrency_test)
endif()

# ---------------------------------------------------------------------------
# RDMA targets (only built when libibverbs is present)
# ---------------------------------------------------------------------------

find_library(IBVERBS_LIB ibverbs)
find_path(IBVERBS_INCLUDE infiniband/verbs.h)

if(IBVERBS_LIB AND IBVERBS_INCLUDE)
    message(STATUS "libibverbs found: ${IBVERBS_LIB} (headers: ${IBVERBS_INCLUDE})")

    # RDMA core library: RdmaContext + RdmaStore
    add_library(rdma_core
        src/net/rdma_context.cpp
        src/kvstore/rdma_store.cpp
        src/protocol/text_protocol.cpp
    )

    target_include_directories(rdma_core
        PUBLIC
            ${CMAKE_CURRENT_SOURCE_DIR}/include
            ${IBVERBS_INCLUDE}
    )

    target_link_libraries(rdma_core
        PUBLIC
            ${IBVERBS_LIB}
            Threads::Threads
    )

    # Two-sided and one-sided RDMA server.
    # Links project_core as well because two-sided mode uses the
    # mutex-protected KeyValueStore implementation.
    add_executable(kv_server_rdma src/server/rdma_main.cpp)
    target_link_libraries(kv_server_rdma PRIVATE rdma_core project_core)

    # Interactive RDMA client (two-sided and one-sided modes)
    add_executable(kv_client_rdma src/client/rdma_client.cpp)
    target_link_libraries(kv_client_rdma PRIVATE rdma_core)

else()
    message(STATUS "libibverbs not found -- skipping RDMA targets (kv_server_rdma, kv_client_rdma)")
    message(STATUS "  On CloudLab: sudo apt install libibverbs-dev")
endif()
