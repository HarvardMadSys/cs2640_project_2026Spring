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

LIGHTNING_DAEMON = $(LIGHTNING_BUILD)/store
LIGHTNING_SOCKET ?= /tmp/lightning
LIGHTNING_PIDFILE = /tmp/lightning.pid

.PHONY: all configure build host test bench clean ycsb ycsb-bench \
        lightning-daemon-start lightning-daemon-stop

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

lightning-daemon-start:
	@test -x $(LIGHTNING_DAEMON) || \
	    (echo "lightning daemon not built at $(LIGHTNING_DAEMON); run setup.sh"; exit 1)
	@rm -f $(LIGHTNING_SOCKET)
	@$(LIGHTNING_DAEMON) >/dev/null 2>&1 & echo $$! > $(LIGHTNING_PIDFILE)
	@for i in $$(seq 1 50); do \
	    test -S $(LIGHTNING_SOCKET) && exit 0; sleep 0.1; \
	done; echo "lightning daemon failed to bind $(LIGHTNING_SOCKET)"; exit 1

lightning-daemon-stop:
	@if [ -f $(LIGHTNING_PIDFILE) ]; then \
	    kill $$(cat $(LIGHTNING_PIDFILE)) 2>/dev/null || true; \
	    rm -f $(LIGHTNING_PIDFILE); \
	fi
	@rm -f $(LIGHTNING_SOCKET)

clean:
	rm -rf build build_san
