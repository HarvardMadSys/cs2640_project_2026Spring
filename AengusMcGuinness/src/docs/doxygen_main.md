# Source Code Documentation {#mainpage}

@tableofcontents

## Project Overview

This repository implements a small distributed key-value cache for comparing
three communication paths:

- TCP/RPC over ordinary sockets.
- Two-sided RDMA send/receive over `libibverbs`.
- One-sided RDMA reads over a remotely readable hash table.

The research question is whether one-sided RDMA can remove server CPU work from
cache reads, and how much of that benefit remains when the read path also
updates cache metadata.  The metadata experiment uses an RDMA
`FETCH_AND_ADD` against each slot's `access_count` field as a lightweight
proxy for LRU-style recency bookkeeping.

## Documentation Groups

@defgroup storage Storage Layer
Thread-safe TCP/two-sided storage and fixed-layout one-sided RDMA storage.

@defgroup protocol Text Protocol
Line-oriented request parsing and response serialization shared by TCP and
two-sided RDMA.

@defgroup networking Networking and Scheduling
POSIX socket helpers, coroutine event loop, and RDMA queue-pair management.

@defgroup executables Executables
Interactive clients, servers, and benchmark drivers.

@defgroup tests Tests
Unit and concurrency tests for the core storage and protocol code.

## Layering

The implementation is intentionally split into four layers:

1. **Storage**: `kvstore::KeyValueStore` stores normal C++ strings behind a
   mutex, while `kvstore::RdmaStore` stores fixed-size slots in a registered
   memory region.
2. **Protocol**: `protocol::parse_request()` and
   `protocol::serialize_response()` define the text command language used by
   TCP and two-sided RDMA.
3. **Transport**: `net::EventLoop` and `net::socket_utils` implement the TCP
   path; `net::RdmaContext` owns the RDMA resources needed for two-sided and
   one-sided operations.
4. **Benchmarking**: `benchmarks/kv_benchmark.cpp` drives the TCP server, and
   `src/client/rdma_client.cpp` drives both RDMA modes.

Keeping these layers separate is what makes the comparison meaningful.  TCP and
two-sided RDMA share request semantics; one-sided RDMA changes the storage
layout only where remote address arithmetic requires it.

## Important Invariants

The one-sided RDMA path depends on several invariants:

- `kvstore::RdmaSlot` must remain exactly 1024 bytes.
- `kvstore::RdmaSlot::access_count` must remain at byte offset 8 for
  8-byte-aligned RDMA atomics.
- `kvstore::RDMA_NUM_SLOTS` must remain a power of two because both server and
  client use a bit mask instead of modulo for slot selection.
- The client and server must use the same FNV-1a hash and linear-probing
  behavior.
- Local buffers used for RDMA reads, writes, and atomics must stay registered
  until the corresponding work completion has been polled.

## Common Control Flows

### TCP Request

1. `kv_client` or `kv_benchmark` writes a text command to a TCP socket.
2. `kv_server` receives bytes through the coroutine event loop.
3. The server calls `protocol::parse_request()`.
4. The server dispatches to `kvstore::KeyValueStore`.
5. The server calls `protocol::serialize_response()` and writes one response
   line.

### Two-Sided RDMA Request

1. Client and server exchange `net::QpInfo` over a TCP side channel.
2. Both peers transition their RC queue pairs to ready-to-send.
3. The client posts an RDMA send containing a text protocol request.
4. The server polls a receive completion, parses the request, and posts a send
   containing the response.
5. The client polls the response completion.

### One-Sided RDMA Read

1. The server preloads `kvstore::RdmaStore`, registers its slot array, and
   shares the remote key and base address.
2. The client hashes the key and computes the remote slot address.
3. The client issues an RDMA read into a registered local slot buffer.
4. The client validates the slot state and key locally.
5. In metadata mode, the client issues a second RDMA operation:
   `FETCH_AND_ADD` to the remote `access_count`.

## Generating HTML Documentation

Doxygen is the recommended tool for this repository because the codebase is
C++, the public API lives in headers, and the project benefits from generated
cross-references between classes, functions, and files.

Install Doxygen, then run:

```bash
doxygen Doxyfile
```

or, if CMake found Doxygen when configuring:

```bash
cmake --build build --target docs
```

Open the generated entry point:

```text
docs/doxygen/html/index.html
```

The default `Doxyfile` enables source browsing and extracts private/static
symbols so the generated documentation is useful for code review and grading,
not only for public API reference.
