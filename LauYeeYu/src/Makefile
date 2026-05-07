# Define the compiler
CXX = g++

# --- Executable and Source Configuration ---
TARGET = evaluate
SOURCES = evaluate.cpp

# --- Build Directories ---
BUILD_DIR = build
BUILD_DIR_SAN = build-san

# --- Auto-generate Object File Lists ---
# Creates lists like 'build/evaluate.o' and 'build-san/evaluate.o'
OBJECTS = $(patsubst %.cpp,$(BUILD_DIR)/%.o,$(SOURCES))
OBJECTS_SAN = $(patsubst %.cpp,$(BUILD_DIR_SAN)/%.o,$(SOURCES))

# --- Dependency Files ---
# Auto-generate dependency files for header file tracking
DEPS = $(patsubst %.cpp,$(BUILD_DIR)/%.d,$(SOURCES))
DEPS_SAN = $(patsubst %.cpp,$(BUILD_DIR_SAN)/%.d,$(SOURCES))

# --- Common Flags for All Builds ---
PKGS = libCacheSim glib-2.0

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
	JSON_INCLUDE := -I$(shell brew --prefix nlohmann-json)/include
else
	JSON_INCLUDE := -I$(HOME)/.local/include
endif

# Ensure pkg-config can locate libCacheSim.pc installed under ~/.local
PKG_CONFIG := PKG_CONFIG_PATH=$(HOME)/.local/lib/pkgconfig:$(HOME)/.local/lib64/pkgconfig:$$PKG_CONFIG_PATH pkg-config

COMMON_CXXFLAGS =  $(JSON_INCLUDE) $(shell $(PKG_CONFIG) --cflags $(PKGS)) $(JSON_INCLUDE)
COMMON_LDLIBS = -L$(HOME)/.local/lib $(shell $(PKG_CONFIG) --libs $(PKGS))

# --- Build-Specific Flags ---
# Flags for the standard (release) build, e.g., optimization
RELEASE_CXXFLAGS = -O2 -DNDEBUG
# Flags for the sanitizer build
ASAN_FLAGS = -fsanitize=address -g
# Dependency generation flags
DEP_FLAGS = -MMD -MP

# =============================================================================
# --- Build Rules ---
# =============================================================================

# Default target: running 'make' or 'make all' builds the standard version.
all: $(BUILD_DIR)/$(TARGET)

# Target to build the sanitizer version: run 'make sanitize'.
sanitize: $(BUILD_DIR_SAN)/$(TARGET)-san

# Help target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Build:"
	@echo "  all           - Build executable (default)"
	@echo "  sanitize      - Build with address sanitizer"
	@echo "  clean         - Clean build files"
	@echo ""
	@echo "Evaluation:"
	@echo "  evaluate      - Run all evaluations sequentially"
	@echo "  eval-parallel - Run all evaluations in parallel"
	@echo "  clean-logs    - Clean evaluation results"
	@echo ""
	@echo "Visualization:"
	@echo "  all-plots     - Generate plots (runs evaluation if needed)"
	@echo "  plots-only    - Generate plots from existing logs"
	@echo "  clean-plots   - Clean visualization plots"
	@echo ""
	@echo "Trace Analysis:"
	@echo "  encode-traces - Add positional encoding to trace files"
	@echo "  analyze-traces- Analyze QWen traces and generate CDF plots"
	@echo "  analyze-holes - Analyze and visualize gap data from evaluation logs"
	@echo "  prefix-tree   - Build and visualize prefix radix trees (SVG)"
	@echo "  clean-analysis- Clean trace analysis results"
	@echo ""
	@echo "ILP Optimization:"
	@echo "  ilp-optimal   - Run ILP optimization sequentially"
	@echo "  ilp-parallel  - Run ILP optimization in parallel"
	@echo "  clean-optimal - Clean ILP optimization results"
	@echo ""
	@echo "Combined:"
	@echo "  eval-and-plot - Full pipeline (parallel eval + plots)"
	@echo "  all-analysis  - Complete analysis (eval + plots + trace analysis)"
	@echo "  clean-all     - Clean everything"
	@echo ""
	@echo "Examples:"
	@echo "  make eval-parallel    # Fast parallel evaluation"
	@echo "  make eval-and-plot    # Complete analysis pipeline"
	@echo "  make -j8 evaluate     # Custom parallel jobs"

# --- Linking Rules ---

# 1. Links the standard executable
$(BUILD_DIR)/$(TARGET): $(OBJECTS)
	@mkdir -p $(@D)
	@echo "Linking standard executable: $@"
	$(CXX) $(LDFLAGS) $^ -o $@ $(COMMON_LDLIBS)

# 2. Links the sanitized executable
$(BUILD_DIR_SAN)/$(TARGET)-san: $(OBJECTS_SAN)
	@mkdir -p $(@D)
	@echo "Linking sanitized executable: $@"
	$(CXX) $(LDFLAGS) $(ASAN_FLAGS) $^ -o $@ $(COMMON_LDLIBS)

# --- Compilation Rules ---

# 1. Compiles object files for the standard build
$(BUILD_DIR)/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(COMMON_CXXFLAGS) $(RELEASE_CXXFLAGS) $(DEP_FLAGS) -c $< -o $@

# 2. Compiles object files for the sanitizer build
$(BUILD_DIR_SAN)/%.o: %.cpp
	@mkdir -p $(@D)
	$(CXX) $(COMMON_CXXFLAGS) $(ASAN_FLAGS) $(DEP_FLAGS) -c $< -o $@

# --- Include Dependency Files ---
# Include the dependency files (if they exist)
# The '-' prefix tells make to ignore errors if the files don't exist yet
-include $(DEPS)
-include $(DEPS_SAN)

# Evaluation configuration
LOGS_DIR = logs
PLOTS_DIR = plots
ANALYSIS_DIR = trace_analysis
PREFIX_TREE_DIR = prefix_tree
OPTIMAL_DIR = optimal
OPTIMAL_LOGS_DIR = logs/optimal
PYTHON = python3
CACHE_SIZES = 4k 6k 8k 10k 20k 80k
ORIGNIAL_TRACES = qwen_traceA_blksz_16 qwen_traceB_blksz_16 qwen_coder_blksz_16 qwen_thinking_blksz_16
ORIGINAL_TRACE_FILES = $(foreach trace,$(ORIGNIAL_TRACES),../datasets/qwen-bailian-usagetraces-anon/$(trace).jsonl)
TRACES_QWEN = qwen_traceA_blksz_16_pos qwen_traceB_blksz_16_pos qwen_coder_blksz_16_pos qwen_thinking_blksz_16_pos
TRACES_FREEINFERENCE = # freeinference_blksz_16_pos # freeinference_blksz_16_pos_limit_160k freeinference_blksz_16_pos_tail_160k
TRACES_GAIA =

CHUTES_DIR = /netscratch/shared/juncheng/2026_chutes_requests/per_model_hash
TRACES_CHUTES = \
	Qwen_Qwen3-14B_hash16_16dinquarterd \
	deepseek-ai_DeepSeek-V3_2-TEE_hash16_16dinquarterd \
	moonshotai_Kimi-K2_5-TEE_hash16_16dinquarterd \
	zai-org_GLM-5-TEE_hash16_16dinquarterd

TRACES = $(TRACES_QWEN) $(TRACES_FREEINFERENCE) $(TRACES_GAIA) $(TRACES_CHUTES)
TRACE_FILES = $(foreach trace,$(TRACES_QWEN),../datasets/qwen-bailian-usagetraces-anon/$(trace).jsonl) $(foreach trace,$(TRACES_FREEINFERENCE),../datasets/freeinference/$(trace).jsonl) $(foreach trace,$(TRACES_GAIA),../datasets/gaia/$(trace).jsonl) $(foreach trace,$(TRACES_CHUTES),$(CHUTES_DIR)/$(trace).jsonl)
LOG_TARGETS = $(foreach trace,$(TRACES),$(foreach size,$(CACHE_SIZES),$(LOGS_DIR)/$(trace)_cache_$(size).log))

# ILP optimization configuration
ILP_TRACES = qwen_traceA_blksz_16_pos qwen_traceB_blksz_16_pos qwen_thinking_blksz_16_pos qwen_coder_blksz_16_pos gaia_text_generations_blksz_16_pos_concurrency_8
ILP_LOG_TARGETS = $(foreach trace,$(ILP_TRACES),$(foreach size,$(CACHE_SIZES),$(OPTIMAL_LOGS_DIR)/$(trace)_cache_$(size).log))
ILP_DECISION_TARGETS = $(foreach trace,$(ILP_TRACES),$(foreach size,$(CACHE_SIZES),$(OPTIMAL_DIR)/$(trace)_request_all_cache_$(size).jsonl))

# Evaluation targets
evaluate: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@$(MAKE) $(LOG_TARGETS)

eval-parallel: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@$(MAKE) -j$(shell nproc 2>/dev/null || echo 4) $(LOG_TARGETS)

$(LOGS_DIR) $(PLOTS_DIR) $(ANALYSIS_DIR) $(PREFIX_TREE_DIR) $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR):
	mkdir -p $@

# Generate rules for each cache size dynamically
define make_qwen_log_rule
$(LOGS_DIR)/$(1)_cache_$(2).log: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@echo "Evaluating $(1) with cache size $(2)"
	$(BUILD_DIR)/$(TARGET) --trace-file ../datasets/qwen-bailian-usagetraces-anon/$(1).jsonl --cache-size $(2) > $$@
endef

define make_freeinference_log_rule
$(LOGS_DIR)/$(1)_cache_$(2).log: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@echo "Evaluating $(1) with cache size $(2)"
	$(BUILD_DIR)/$(TARGET) --trace-file ../datasets/freeinference/$(1).jsonl --cache-size $(2) > $$@
endef

define make_gaia_log_rule
$(LOGS_DIR)/$(1)_cache_$(2).log: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@echo "Evaluating $(1) with cache size $(2)"
	$(BUILD_DIR)/$(TARGET) --trace-file ../datasets/gaia/$(1).jsonl --cache-size $(2) > $$@
endef

define make_chutes_log_rule
$(LOGS_DIR)/$(1)_cache_$(2).log: $(BUILD_DIR)/$(TARGET) | $(LOGS_DIR)
	@echo "Evaluating $(1) with cache size $(2)"
	$(BUILD_DIR)/$(TARGET) --trace-file $(CHUTES_DIR)/$(1).jsonl --cache-size $(2) > $$@
endef

$(foreach trace,$(TRACES_QWEN),$(foreach size,$(CACHE_SIZES),$(eval $(call make_qwen_log_rule,$(trace),$(size)))))
$(foreach trace,$(TRACES_FREEINFERENCE),$(foreach size,$(CACHE_SIZES),$(eval $(call make_freeinference_log_rule,$(trace),$(size)))))
$(foreach trace,$(TRACES_GAIA),$(foreach size,$(CACHE_SIZES),$(eval $(call make_gaia_log_rule,$(trace),$(size)))))
$(foreach trace,$(TRACES_CHUTES),$(foreach size,$(CACHE_SIZES),$(eval $(call make_chutes_log_rule,$(trace),$(size)))))

# Visualization targets
all-plots: $(LOG_TARGETS) | $(PLOTS_DIR)
	$(PYTHON) visualize_results.py --logs-dir $(LOGS_DIR) --output-dir $(PLOTS_DIR)

plots-only: | $(PLOTS_DIR)
	$(PYTHON) visualize_results.py --logs-dir $(LOGS_DIR) --output-dir $(PLOTS_DIR)

eval-and-plot: eval-parallel all-plots

# Trace analysis targets
analyze-traces: | $(ANALYSIS_DIR)
	@echo "Analyzing QWen traces and generating CDF plots..."
	$(PYTHON) analyze_traces.py $(TRACE_FILES) --output-dir $(ANALYSIS_DIR)

analyze-holes:
	@echo "Analyzing gap data and generating holes distribution plots..."
	$(PYTHON) analyze_holes.py

prefix-tree: | $(PREFIX_TREE_DIR)
	@echo "Building and visualizing prefix radix trees..."
	$(PYTHON) analyze_prefix_tree.py $(TRACE_FILES) --output-dir $(PREFIX_TREE_DIR)

all-analysis: eval-and-plot analyze-traces encode-traces

# ILP optimization targets
ilp-optimal: | $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR)
	@$(MAKE) $(ILP_LOG_TARGETS)

ilp-parallel: | $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR)
	@$(MAKE) -j$(shell nproc 2>/dev/null || echo 4) $(ILP_LOG_TARGETS)

# Generate ILP rules for qwen traces
define make_ilp_qwen_rule
$(OPTIMAL_LOGS_DIR)/$(1)_cache_$(2).log: | $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR)
	@echo "Running ILP optimization for $(1) with cache size $(2)"
	$(PYTHON) ilp_reuse_interval.py ../datasets/qwen-bailian-usagetraces-anon/$(1).jsonl --cache-size $(2) --log-file $(OPTIMAL_DIR)/$(1)_request_all_cache_$(2).jsonl > $$@
endef

# Generate ILP rules for gaia traces
define make_ilp_gaia_rule
$(OPTIMAL_LOGS_DIR)/$(1)_cache_$(2).log: | $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR)
	@echo "Running ILP optimization for $(1) with cache size $(2)"
	$(PYTHON) ilp_reuse_interval.py ../datasets/gaia/$(1).jsonl --cache-size $(2) --log-file $(OPTIMAL_DIR)/$(1)_request_all_cache_$(2).jsonl > $$@
endef

# Generate the rules for qwen traces
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_qwen_rule,qwen_traceA_blksz_16_pos,$(size))))
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_qwen_rule,qwen_traceB_blksz_16_pos,$(size))))
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_qwen_rule,qwen_thinking_blksz_16_pos,$(size))))
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_qwen_rule,qwen_coder_blksz_16_pos,$(size))))

# Generate the rules for gaia traces
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_gaia_rule,gaia_text_generations_blksz_16_pos_concurrency_1,$(size))))
$(foreach size,$(CACHE_SIZES),$(eval $(call make_ilp_gaia_rule,gaia_text_generations_blksz_16_pos_concurrency_8,$(size))))

clean-optimal:
	rm -rf $(OPTIMAL_DIR) $(OPTIMAL_LOGS_DIR)

clean-logs:
	rm -rf $(LOGS_DIR)

clean-plots:
	rm -rf $(PLOTS_DIR)

clean-analysis:
	rm -rf $(ANALYSIS_DIR) $(PREFIX_TREE_DIR)

# --- Cleanup Rule ---
clean:
	@echo "Cleaning up build directories..."
	rm -rf $(BUILD_DIR) $(BUILD_DIR_SAN)

clean-all: clean clean-logs clean-plots clean-analysis clean-optimal

.PHONY: all sanitize clean evaluate eval-parallel clean-logs clean-plots clean-all all-plots plots-only eval-and-plot help analyze-traces analyze-holes prefix-tree clean-analysis all-analysis ilp-optimal ilp-parallel clean-optimal

# Positional encoding targets
encode-traces:
	@echo "Adding positional encoding to trace files..."
	$(PYTHON) add_positional_encoding.py $(ORIGINAL_TRACE_FILES) --suffix _pos
