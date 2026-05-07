# AsyncMux

AsyncMux is an asynchronous multi-tier storage mux built on top of cppcoro. It splits file writes into 4 KiB blocks, stores block metadata, and routes reads and writes to one or more filesystem tiers. The project also includes a small FUSE-facing frontend wrapper and correctness tests for single-tier and multi-tier behavior.

## What It Does

- Splits writes into fixed-size blocks (4096 bytes).
- Tracks block locations, tiers, and file extents in an in-memory metadata store.
- Reads data back from the correct tier or stitches together reads across multiple tiers.
- Supports block migration and promotion between tiers.
- Exposes a minimal FuseFrontend adapter for filesystem integration.

## Repository Layout

```text
.
├── profiles/
│   ├── blockstore_profile.py  # CloudLab profile for the block-store-backed setup
│   └── loopback_profile.py    # CloudLab profile for the loopback-based setup
├── src/
│   ├── CMakeLists.txt         # Source-level CMake configuration
│   ├── amux/                  # AsyncMux implementation and public API
│   ├── bmux/                  # Block-mux implementation
│   ├── tests/                 # Benchmarks, correctness tests, and analysis helpers
│   └── third_party/cppcoro/   # Vendored cppcoro dependency
├── build/                     # Local build output
├── README.md                  # Project overview and usage notes
└── src/Dockerfile, src/Makefile
```

## Prerequisites

- CloudLab access
- Git submodules initialized, including third_party/cppcoro

If the cppcoro headers are missing, initialize the submodule with:

```bash
git submodule update --init --recursive
```

## Build and Run

This project is intended to be built and run in CloudLab using the associated profiles in [profiles/](profiles).

- Use [profiles/loopback_profile.py](profiles/loopback_profile.py) for the loopback-backed setup.
- Use [profiles/blockstore_profile.py](profiles/blockstore_profile.py) for the block-store-backed setup.
- Instantiate the desired profile in CloudLab, then use the provided environment to build and run the project there.

If you are updating the code locally, you can still use the existing CMake output in [build/](build/) as a reference, but the supported execution path is CloudLab.

### Correctness Tests

- single_fs exercises the core read/write path against one filesystem tier.
- multiple_fs mounts and verifies several filesystems, so it should be run in the CloudLab environment provided by the profiles.

### Performance Tests

The performance test suite can be run using the included shell script `run_benchmark_tests.sh`. 

## Clean

Remove the local CMake build output directory if you created one during local development:

```bash
rm -rf build/
```

## Implementation Notes

- AsyncMux is the main orchestration layer. It lives in [amux/asyncmux.cc](amux/asyncmux.cc) and is declared by the umbrella header [amux/asyncmux.hh](amux/asyncmux.hh).
- Shared types and helpers live in [amux/asyncmux.hh](amux/asyncmux.hh) and [amux/asyncmux.cc](amux/asyncmux.cc).
- MetadataStore tracks per-file extents and block-to-path lookups in [amux/asyncmux.hh](amux/asyncmux.hh) and [amux/asyncmux.cc](amux/asyncmux.cc).
- Placement is controlled by policies in [amux/asyncmux.hh](amux/asyncmux.hh) and [amux/asyncmux.cc](amux/asyncmux.cc), which lets tests steer writes to specific tiers.
- Tier, TierRegistry, and FileSystemTier are declared in [amux/asyncmux.hh](amux/asyncmux.hh) and implemented in [amux/asyncmux.cc](amux/asyncmux.cc).

## Troubleshooting

- If the build fails with missing cppcoro headers, initialize submodules first.
- If multiple_fs fails, make sure you are running in the CloudLab environment defined by the profiles.