CXX      := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Iinclude -MMD -MP
LDFLAGS  :=

SRCDIR := src
INCDIR := include
OBJDIR := build

SOURCES := $(wildcard $(SRCDIR)/*.cpp)
OBJECTS := $(patsubst $(SRCDIR)/%.cpp, $(OBJDIR)/%.o, $(SOURCES))
TARGET  := cache_sim

.PHONY: all clean run run-sweep plots test shards-large phase-04 ablation-s3fifo ablation-sieve ablation-doorkeeper demo

all: $(TARGET)

$(TARGET): $(OBJECTS)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

$(OBJDIR)/%.o: $(SRCDIR)/%.cpp | $(OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(OBJDIR):
	mkdir -p $(OBJDIR)

-include $(OBJECTS:.o=.d)

clean:
	rm -rf $(OBJDIR) $(TEST_OBJDIR) $(TARGET)

# Workload parameterization (Phase 3 D-12 / D-14). Default preserves prior
# behavior: plain `make run` / `make run-sweep` / `make plots` all target
# results/congress/ unchanged. To target the court trace, pass
# `WORKLOAD=court TRACE=traces/court_trace.csv`.
WORKLOAD ?= congress
WORKLOAD_RESULTS_DIR := results/$(WORKLOAD)

# Quick run with defaults (synthetic Zipf, alpha=0.8). Writes to
# results/$(WORKLOAD)/ (default: results/congress/).
run: $(TARGET)
	mkdir -p $(WORKLOAD_RESULTS_DIR)
	./$(TARGET) --output-dir $(WORKLOAD_RESULTS_DIR)

# Full run: MRC + alpha sweep into results/$(WORKLOAD)/.
# When TRACE is set, switch to replay-Zipf mode (Phase 3 court trace path)
# and drop --shards (SHARDS on court is not in Phase 3 scope; Phase 4 uses a
# separate synthetic 1M trace for SHARDS). When TRACE is empty (default),
# preserve Phase 1/2 behavior: synthetic-Zipf + SHARDS + alpha-sweep against
# results/congress/.
TRACE ?=
SWEEP_FLAGS := --alpha-sweep --shards --output-dir $(WORKLOAD_RESULTS_DIR)
ifneq ($(TRACE),)
SWEEP_FLAGS := --trace $(TRACE) --replay-zipf --alpha-sweep --output-dir $(WORKLOAD_RESULTS_DIR)
endif
run-sweep: $(TARGET)
	mkdir -p $(WORKLOAD_RESULTS_DIR)
	./$(TARGET) $(SWEEP_FLAGS)

# Generate plots from results/$(WORKLOAD)/.
#
# macOS libexpat workaround: Python 3.14's pyexpat requires a newer libexpat
# than /usr/lib/libexpat.1.dylib ships. The .venv's Python binary strips
# DYLD_LIBRARY_PATH (hardened runtime), so invoke the real Homebrew python
# directly and point DYLD_LIBRARY_PATH at the Homebrew libexpat; use
# PYTHONPATH to pick up matplotlib from the .venv's site-packages.
#
# Override by exporting PLOT_PYTHON and PLOT_PYTHONPATH if your setup differs.
PLOT_PYTHON ?= /opt/homebrew/opt/python@3.14/bin/python3.14
PLOT_PYTHONPATH ?= .venv/lib/python3.14/site-packages
plots:
	DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib \
	PYTHONPATH=$(PLOT_PYTHONPATH) \
	$(PLOT_PYTHON) scripts/plot_results.py --workload $(WORKLOAD)

# ==================== Test binaries (Phase 2 D-04/D-05/D-07 + Phase 4) ====================
# Two standalone assertion-based binaries: test_wtinylfu (Phase 2 — WTLFU-04
# acceptance) and test_doorkeeper (Phase 4 Plan 04-02 — DOOR-01 acceptance).
# Separate build/test/ object dir per D-07 so `make && make test` does not
# rebuild the main simulator. No third-party framework (D-06).

TEST_OBJDIR  := build/test

TEST_WTLFU_SRC    := tests/test_wtinylfu.cpp
TEST_WTLFU_OBJ    := $(TEST_OBJDIR)/test_wtinylfu.o
TEST_WTLFU_TARGET := $(TEST_OBJDIR)/test_wtinylfu

TEST_DK_SRC    := tests/test_doorkeeper.cpp
TEST_DK_OBJ    := $(TEST_OBJDIR)/test_doorkeeper.o
TEST_DK_TARGET := $(TEST_OBJDIR)/test_doorkeeper

$(TEST_OBJDIR):
	mkdir -p $(TEST_OBJDIR)

$(TEST_WTLFU_OBJ): $(TEST_WTLFU_SRC) | $(TEST_OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(TEST_WTLFU_TARGET): $(TEST_WTLFU_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

$(TEST_DK_OBJ): $(TEST_DK_SRC) | $(TEST_OBJDIR)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(TEST_DK_TARGET): $(TEST_DK_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

# `make test` builds and runs BOTH test binaries. Non-zero exit on any failure
# (Make stops at the first non-zero exit; both binaries' main() returns 1 on
# failures, so the whole `make test` invocation exits non-zero if either suite
# fails — the failing binary's stderr is the diagnosis surface).
test: $(TEST_WTLFU_TARGET) $(TEST_DK_TARGET)
	@echo "=== Running W-TinyLFU test suite ==="
	$(TEST_WTLFU_TARGET)
	@echo ""
	@echo "=== Running Doorkeeper test suite ==="
	$(TEST_DK_TARGET)

# ==================== Phase 4 — SHARDS large-scale (D-15, D-17, D-18) ====================
# Independent of WORKLOAD=/TRACE= plumbing per D-17. Each Phase 4 target owns
# its invocation shape — no flag-juggling via the Phase 3 sweep target.

# Generate the 1M-access synthetic Zipf(alpha=0.8, 100K objects, seed=42) trace.
# Gitignored per D-15; regenerated on demand. Runtime: <5s on dev laptop.
traces/shards_large.csv: $(TARGET)
	./$(TARGET) --emit-trace traces/shards_large.csv \
	            --num-requests 1000000 --num-objects 100000 --alpha 0.8

# SHARDS large-scale validation: 4-rate sweep + self-convergence + 50K oracle.
# Runs the simulator TWICE: first the 50K oracle regime (saves shards_mrc
# renamed to shards_mrc_50k.csv so step 2 can overwrite shards_mrc.csv without
# clobbering the oracle-regime MRC), then the 1M self-convergence regime.
# Outputs land in results/shards_large/.
shards-large: $(TARGET) traces/shards_large.csv
	mkdir -p results/shards_large
	@echo "=== Step 1/2: 50K oracle regime (--limit 50000 + --shards-exact) ==="
	./$(TARGET) --trace traces/shards_large.csv \
	            --shards --shards-exact --limit 50000 \
	            --shards-rates 0.001,0.01,0.1 \
	            --output-dir results/shards_large
	@mv results/shards_large/shards_mrc.csv results/shards_large/shards_mrc_50k.csv 2>/dev/null || true
	@echo ""
	@echo "=== Step 2/2: 1M self-convergence regime ==="
	./$(TARGET) --trace traces/shards_large.csv \
	            --shards \
	            --shards-rates 0.0001,0.001,0.01,0.1 \
	            --output-dir results/shards_large
	@echo "=== shards-large complete. Figures via 'make plots WORKLOAD=shards_large' ==="

# ==================== Phase 4 — Axis C: S3-FIFO small-queue ratio ablation (D-11, D-13, D-14, D-17) ====================
# Runs --alpha-sweep with the 3 small_frac variants on Congress AND Court at
# fixed 1% cache (src/main.cpp alpha-sweep path uses wb/100 for cache size
# per D-13). After each invocation, renames the produced alpha_sensitivity.csv
# to ablation_s3fifo.csv so the ablation output is namespaced and a subsequent
# `make run-sweep` does not clobber it.
ablation-s3fifo: $(TARGET)
	mkdir -p results/congress results/court
	@echo "=== Ablation s3fifo: Congress trace ==="
	./$(TARGET) --trace traces/congress_trace.csv --replay-zipf \
	            --alpha-sweep --policies s3fifo-5,s3fifo-10,s3fifo-20 \
	            --output-dir results/congress
	@mv results/congress/alpha_sensitivity.csv results/congress/ablation_s3fifo.csv
	@echo ""
	@echo "=== Ablation s3fifo: Court trace ==="
	./$(TARGET) --trace traces/court_trace.csv --replay-zipf \
	            --alpha-sweep --policies s3fifo-5,s3fifo-10,s3fifo-20 \
	            --output-dir results/court
	@mv results/court/alpha_sensitivity.csv results/court/ablation_s3fifo.csv
	@echo "=== ablation-s3fifo complete; figures via 'make plots WORKLOAD=congress && make plots WORKLOAD=court' ==="

# ==================== Phase 4 — Axis D: SIEVE visited-bit ablation (D-12, D-13, D-14, D-17) ====================
# Runs --alpha-sweep with both SIEVE variants (legacy `sieve` default +
# `sieve-noprom` new variant) on Congress AND Court at fixed 1% cache (via
# the simulator's --alpha-sweep path which hardcodes wb/100 per D-13). After
# each invocation, renames the produced alpha_sensitivity.csv to
# ablation_sieve.csv so the ablation output is namespaced and a subsequent
# `make run-sweep` does not clobber it.
ablation-sieve: $(TARGET)
	mkdir -p results/congress results/court
	@echo "=== Ablation sieve: Congress trace ==="
	./$(TARGET) --trace traces/congress_trace.csv --replay-zipf \
	            --alpha-sweep --policies sieve,sieve-noprom \
	            --output-dir results/congress
	@mv results/congress/alpha_sensitivity.csv results/congress/ablation_sieve.csv
	@echo ""
	@echo "=== Ablation sieve: Court trace ==="
	./$(TARGET) --trace traces/court_trace.csv --replay-zipf \
	            --alpha-sweep --policies sieve,sieve-noprom \
	            --output-dir results/court
	@mv results/court/alpha_sensitivity.csv results/court/ablation_sieve.csv
	@echo "=== ablation-sieve complete; figures via 'make plots WORKLOAD=congress && make plots WORKLOAD=court' ==="

# ==================== Phase 4 — Axis B: Doorkeeper × W-TinyLFU ablation (D-08, D-13, D-14, D-17 / DOOR-03) ====================
# Runs --alpha-sweep with both W-TinyLFU variants (legacy `wtinylfu` default +
# `wtinylfu-dk` Doorkeeper variant) on Congress AND Court at fixed 1% cache
# (via the simulator's --alpha-sweep path which hardcodes wb/100 per D-13).
# After each invocation, renames the produced alpha_sensitivity.csv to
# ablation_doorkeeper.csv so the ablation output is namespaced and a subsequent
# `make run-sweep` does not clobber it.
ablation-doorkeeper: $(TARGET)
	mkdir -p results/congress results/court
	@echo "=== Ablation doorkeeper: Congress trace ==="
	./$(TARGET) --trace traces/congress_trace.csv --replay-zipf \
	            --alpha-sweep --policies wtinylfu,wtinylfu-dk \
	            --output-dir results/congress
	@mv results/congress/alpha_sensitivity.csv results/congress/ablation_doorkeeper.csv
	@echo ""
	@echo "=== Ablation doorkeeper: Court trace ==="
	./$(TARGET) --trace traces/court_trace.csv --replay-zipf \
	            --alpha-sweep --policies wtinylfu,wtinylfu-dk \
	            --output-dir results/court
	@mv results/court/alpha_sensitivity.csv results/court/ablation_doorkeeper.csv
	@echo "=== ablation-doorkeeper complete; figures via 'make plots WORKLOAD=congress && make plots WORKLOAD=court' ==="

# Convenience: all Phase 4 axes in sequence. Plan 04-05 appended
# ablation-doorkeeper — Phase 4 now covers shards-large + 3 ablation axes.
phase-04: shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper
	@echo "phase-04 complete: shards-large + ablation-s3fifo + ablation-sieve + ablation-doorkeeper"

# ==================== Phase 6 — Live demo ====================
# Invokes the repo-root demo.sh which runs a <60s 6-policy sweep on a
# pre-generated 5K-request Congress slice (traces/demo_trace.csv).
# .PHONY for `demo` is declared on line 13.
demo:
	./demo.sh
