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

YCSB_HOME ?= external/ycsb
YCSB_BACKEND ?= hp
YCSB_WORKLOAD ?= workloada
YCSB_THREADS ?= 1
YCSB_CLASSES = $(BUILD_DIR)/benchmarks/ycsb/classes
YCSB_LIB = $(BUILD_DIR)/benchmarks/ycsb
YCSB_CORE_JAR = $(firstword $(wildcard $(YCSB_HOME)/lib/core-*.jar))
YCSB_CLASSPATH = $(abspath $(YCSB_HOME))/lib/*:$(abspath $(YCSB_CLASSES))

.PHONY: all configure build host test bench clean ycsb ycsb-bench ycsb-load ycsb-run

all: build

configure:
	@cmake -S . -B $(BUILD_DIR) $(CMAKE_FLAGS)

build: configure
	@cmake --build $(BUILD_DIR) -j$(NPROC)

host: configure
	@cmake --build $(BUILD_DIR) --target host -j$(NPROC)

test: build
	@cd $(BUILD_DIR) && ctest --output-on-failure

YCSB_SOURCES = benchmarks/ycsb/SasClient.java benchmarks/ycsb/SasYcsbDriver.java
YCSB_STAMP = $(YCSB_CLASSES)/.stamp

ycsb: build $(YCSB_STAMP)

$(YCSB_STAMP): $(YCSB_SOURCES)
	@test -n "$(YCSB_CORE_JAR)" || \
	    (echo "YCSB not found at $(YCSB_HOME); run setup.sh"; exit 1)
	@mkdir -p $(YCSB_CLASSES)
	@javac -cp "$(YCSB_CORE_JAR)" -d $(YCSB_CLASSES) $(YCSB_SOURCES)
	@touch $@

ycsb-bench: ycsb
	@java -cp "$(YCSB_CLASSPATH)" \
	    -Djava.library.path=$(abspath $(YCSB_LIB)) \
	    -Dsas.backend=$(YCSB_BACKEND) \
	    site.ycsb.db.SasYcsbDriver \
	    -P $(abspath $(YCSB_HOME))/workloads/$(YCSB_WORKLOAD) \
	    -threads $(YCSB_THREADS)

ycsb-load: ycsb
	@cd $(YCSB_HOME) && ./bin/ycsb load basic \
	    -P workloads/$(YCSB_WORKLOAD) \
	    -db site.ycsb.db.SasClient \
	    -cp $(abspath $(YCSB_CLASSES)) \
	    -jvm-args "-Djava.library.path=$(abspath $(YCSB_LIB)) -Dsas.backend=$(YCSB_BACKEND)"

ycsb-run: ycsb
	@cd $(YCSB_HOME) && ./bin/ycsb run basic \
	    -P workloads/$(YCSB_WORKLOAD) \
	    -db site.ycsb.db.SasClient \
	    -cp $(abspath $(YCSB_CLASSES)) \
	    -jvm-args "-Djava.library.path=$(abspath $(YCSB_LIB)) -Dsas.backend=$(YCSB_BACKEND)"

clean:
	rm -rf build build_san
