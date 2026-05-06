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
LIGHTNING_HOME ?= external/lightning
LIGHTNING_BUILD = $(abspath $(LIGHTNING_HOME)/build)

YCSB_STORE ?= sas
YCSB_BACKEND ?= hp
YCSB_WORKLOAD ?= workloada
YCSB_THREADS ?= 1
YCSB_RECORDS ?= 1000000
YCSB_OPS ?= 1000000
YCSB_CLASSES = $(BUILD_DIR)/benchmarks/ycsb/classes
YCSB_CORE_JAR = $(firstword $(wildcard $(YCSB_HOME)/lib/core-*.jar))

ifeq ($(YCSB_STORE),lightning)
    YCSB_DB_CLASS = site.ycsb.db.LightningClient
    YCSB_SYS_PROPS =
else
    YCSB_DB_CLASS = site.ycsb.db.SasClient
    YCSB_SYS_PROPS = -Dsas.backend=$(YCSB_BACKEND)
endif

.PHONY: all configure build host test bench clean ycsb ycsb-bench

all: build

configure:
	@cmake -S . -B $(BUILD_DIR) $(CMAKE_FLAGS)

build: configure
	@cmake --build $(BUILD_DIR) -j$(NPROC)

host: configure
	@cmake --build $(BUILD_DIR) --target host -j$(NPROC)

test: build
	@cd $(BUILD_DIR) && ctest --output-on-failure

ycsb: build
	@test -n "$(YCSB_CORE_JAR)" || \
	    (echo "YCSB not found at $(YCSB_HOME); run setup.sh"; exit 1)
	@mkdir -p $(YCSB_CLASSES)
	@javac -cp "$(YCSB_CORE_JAR):$(LIGHTNING_BUILD)" \
	    -d $(YCSB_CLASSES) $(wildcard benchmarks/ycsb/*.java)

ycsb-bench: ycsb
	@java -cp "$(abspath $(YCSB_HOME))/lib/*:$(abspath $(YCSB_CLASSES)):$(LIGHTNING_BUILD)" \
	    -Djava.library.path=$(abspath $(BUILD_DIR)/benchmarks/ycsb):$(LIGHTNING_BUILD) \
	    $(YCSB_SYS_PROPS) \
	    site.ycsb.db.YcsbDriver \
	    -P $(abspath $(YCSB_HOME))/workloads/$(YCSB_WORKLOAD) \
	    -threads $(YCSB_THREADS) \
	    -p dbclass=$(YCSB_DB_CLASS) \
	    -p recordcount=$(YCSB_RECORDS) \
	    -p operationcount=$(YCSB_OPS)

clean:
	rm -rf build build_san
