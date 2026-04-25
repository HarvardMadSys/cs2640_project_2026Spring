BUILD_DIR ?= build
BUILD_TYPE ?= Release
CMAKE_FLAGS ?=

ifeq ($(SAN),1)
  BUILD_DIR = build_san
  BUILD_TYPE = Debug
  CMAKE_FLAGS += -DUSE_SANITIZERS=ON
endif

CMAKE_FLAGS += -DCMAKE_BUILD_TYPE=$(BUILD_TYPE)
NPROC ?= $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

.PHONY: all configure build host test bench clean

all: build

configure:
	@cmake -S . -B $(BUILD_DIR) $(CMAKE_FLAGS)

build: configure
	@cmake --build $(BUILD_DIR) -j$(NPROC)

host: configure
	@cmake --build $(BUILD_DIR) --target host -j$(NPROC)

test: build
	@cd $(BUILD_DIR) && ctest --output-on-failure

clean:
	rm -rf build build_san
