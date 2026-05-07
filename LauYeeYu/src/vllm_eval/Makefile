LOGS_DIR = logs
SEGMENTS_DIR = $(LOGS_DIR)/segments
LATENCY_DIR = $(LOGS_DIR)/latency
THROUGHPUT_DIR = $(LOGS_DIR)/throughput
PLOTS_DIR = plots
SEGMENTS_PLOTS_DIR = $(PLOTS_DIR)/segments
LATENCY_PLOTS_DIR = $(PLOTS_DIR)/latency
THROUGHPUT_PLOTS_DIR = $(PLOTS_DIR)/throughput
PYTHON = python3
CACHE_SIZES = 4k 5k 6k 7k 8k 9k 10k 11k 12k 13k 14k 15k 16k 17k 18k 19k 20k 40k 80k
LATENCY_CACHE_SIZES = 4k 6k 8k 10k 16k
THROUGHPUT_CACHE_SIZES = 4k 6k 8k 10k 16k

# Compute-intensity models passed via --compute-intensity. Each gets its own
# subdirectory under logs/ and logs/segments/ so results don't collide.
#   qwen_coder_30b: 865 + 2*index (existing default; pre-existing logs were moved here)
#   linear:         index           (a*x + b with a=1, b=0)
#   constant:       1               (every block costs the same)
INTENSITIES = qwen_coder_30b linear constant

QWEN_TRACES = qwen_traceA_blksz_16_pos qwen_traceB_blksz_16_pos qwen_coder_blksz_16_pos qwen_thinking_blksz_16_pos
GAIA_TRACES = gaia_text_generations_blksz_16_pos_concurrency_8
CHUTES_DIR = /netscratch/shared/juncheng/2026_chutes_requests/per_model_hash
CHUTES_TRACES = \
	Qwen_Qwen3-14B_hash16_16dinquarterd \
	deepseek-ai_DeepSeek-V3_2-TEE_hash16_16dinquarterd \
	moonshotai_Kimi-K2_5-TEE_hash16_16dinquarterd \
	zai-org_GLM-5-TEE_hash16_16dinquarterd

ALGOS = LRU LCD GDCF BeladyCompute BeladyNodeCompute RandomSmallQueue Random RandomSmallQueueAdaptiveBlockEvent
LATENCY_ALGOS = LRU RandomSmallQueueAdaptiveBlockEvent
THROUGHPUT_TRACES = qwen_traceB_blksz_16_pos
THROUGHPUT_ALGOS = LRU RandomSmallQueueAdaptiveBlockEvent
THROUGHPUT_CONCURRENCIES = 1 4 16
THROUGHPUT_OUTPUT_LENGTHS = 1

MODEL_PATH = /scratch/yiyu/models/Qwen3-Coder-30B-A3B-Instruct

INTENSITY_LOG_DIRS = $(foreach i,$(INTENSITIES),$(LOGS_DIR)/$(i)) $(foreach i,$(INTENSITIES),$(SEGMENTS_DIR)/$(i))

QWEN_LOG_TARGETS = $(foreach intensity,$(INTENSITIES),$(foreach trace,$(QWEN_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(LOGS_DIR)/$(intensity)/$(trace)_cache_$(size)_$(algo).log))))
GAIA_LOG_TARGETS = $(foreach intensity,$(INTENSITIES),$(foreach trace,$(GAIA_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(LOGS_DIR)/$(intensity)/$(trace)_cache_$(size)_$(algo).log))))
CHUTES_LOG_TARGETS = $(foreach intensity,$(INTENSITIES),$(foreach trace,$(CHUTES_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(LOGS_DIR)/$(intensity)/$(trace)_cache_$(size)_$(algo).log))))

LOG_TARGETS = $(QWEN_LOG_TARGETS) $(GAIA_LOG_TARGETS) $(CHUTES_LOG_TARGETS)

# Per-intensity slices of LOG_TARGETS for the eval-<intensity> shortcuts.
$(foreach i,$(INTENSITIES),$(eval LOG_TARGETS_$(i) = $$(filter $$(LOGS_DIR)/$(i)/%,$$(LOG_TARGETS))))

.DEFAULT_GOAL := help

help:
	@echo "Available targets:"
	@echo ""
	@echo "Evaluation (C++-free, runs evaluate.py):"
	@echo "  evaluate                      - Run all evaluations sequentially (all intensities)"
	@echo "  eval-parallel                 - Run all evaluations in parallel (all intensities)"
	@echo "  evaluate-<intensity>          - Run all eval for a single compute-intensity model"
	@echo "  eval-parallel-<intensity>     - Same, in parallel.  intensity in: $(INTENSITIES)"
	@echo "  clean-logs                    - Remove $(LOGS_DIR) and $(SEGMENTS_DIR)"
	@echo ""
	@echo "Visualization (evaluation):"
	@echo "  all-plots                     - Run evaluation then generate plots"
	@echo "  plots-only                    - Plot existing logs only"
	@echo "  plots-segments                - Plot segment analysis only"
	@echo "  eval-and-plot                 - Parallel eval + all plots"
	@echo "  clean-plots                   - Remove $(PLOTS_DIR)"
	@echo ""
	@echo "Latency benchmarking (benchmark_latency.py):"
	@echo "  latency-benchmark             - Run all latency benchmarks"
	@echo "  latency-plots                 - Run benchmarks (if needed) then plot"
	@echo "  latency-plots-only            - Plot existing latency results only"
	@echo "  latency-benchmark-and-plot    - Run benchmarks + generate plots"
	@echo "  clean-latency                 - Remove $(LATENCY_DIR)"
	@echo ""
	@echo "Throughput benchmarking (benchmark_throughput.py):"
	@echo "  throughput-benchmark          - Run all throughput benchmarks"
	@echo "  throughput-plots              - Run benchmarks (if needed) then plot"
	@echo "  throughput-plots-only         - Plot existing throughput results only"
	@echo "  throughput-benchmark-and-plot - Run benchmarks + generate plots"
	@echo "  clean-throughput              - Remove $(THROUGHPUT_DIR)"
	@echo ""
	@echo "Combined:"
	@echo "  clean-all                     - Clean logs, plots, latency, throughput"
	@echo ""
	@echo "Throughput sweep:"
	@echo "  traces=$(THROUGHPUT_TRACES)"
	@echo "  cache sizes=$(THROUGHPUT_CACHE_SIZES)"
	@echo "  algos=$(THROUGHPUT_ALGOS)"
	@echo "  concurrencies=$(THROUGHPUT_CONCURRENCIES)"
	@echo "  output lengths=$(THROUGHPUT_OUTPUT_LENGTHS)"

evaluate: | $(LOGS_DIR)
	@$(MAKE) $(LOG_TARGETS)

eval-parallel: | $(LOGS_DIR)
	@$(MAKE) -j$(shell nproc 2>/dev/null || echo 4) $(LOG_TARGETS)

# Per-intensity shortcuts: evaluate-<intensity> / eval-parallel-<intensity>
define make_intensity_eval_targets
.PHONY: evaluate-$(1) eval-parallel-$(1)
evaluate-$(1): | $$(LOGS_DIR)
	@$$(MAKE) $$(LOG_TARGETS_$(1))
eval-parallel-$(1): | $$(LOGS_DIR)
	@$$(MAKE) -j$$(shell nproc 2>/dev/null || echo 4) $$(LOG_TARGETS_$(1))
endef
$(foreach i,$(INTENSITIES),$(eval $(call make_intensity_eval_targets,$(i))))

QWEN_LATENCY_TARGETS = $(foreach trace,$(QWEN_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(LATENCY_DIR)/$(trace)_cache_$(size)_$(algo).json)))
GAIA_LATENCY_TARGETS = $(foreach trace,$(GAIA_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(LATENCY_DIR)/$(trace)_cache_$(size)_$(algo).json)))
CHUTES_LATENCY_TARGETS = $(foreach trace,$(CHUTES_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(LATENCY_DIR)/$(trace)_cache_$(size)_$(algo).json)))

LATENCY_TARGETS = $(QWEN_LATENCY_TARGETS) $(GAIA_LATENCY_TARGETS) $(CHUTES_LATENCY_TARGETS)

$(LOGS_DIR) $(PLOTS_DIR) $(SEGMENTS_DIR) $(SEGMENTS_PLOTS_DIR) $(LATENCY_DIR) $(LATENCY_PLOTS_DIR) $(THROUGHPUT_DIR) $(THROUGHPUT_PLOTS_DIR) $(INTENSITY_LOG_DIRS):
	@mkdir -p $@

# Args: $(1)=trace $(2)=cache_size $(3)=algo $(4)=intensity
define make_qwen_log_rule
$(LOGS_DIR)/$(4)/$(1)_cache_$(2)_$(3).log: evaluate.py | $(LOGS_DIR)/$(4) $(SEGMENTS_DIR)/$(4)
	@echo "Evaluating $(1) cache=$(2) algo=$(3) intensity=$(4)"
	$(PYTHON) evaluate.py --trace-file ../../datasets/qwen-bailian-usagetraces-anon/$(1).jsonl --cache-size $(2) --algo $(3) --compute-intensity $(4) --dump-segments $(SEGMENTS_DIR)/$(4)/$(1)_cache_$(2)_$(3)_segments.jsonl --latency-file measurement/results/qwen3_coder_30b_latency.json > $$@
endef

$(foreach intensity,$(INTENSITIES),$(foreach trace,$(QWEN_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(eval $(call make_qwen_log_rule,$(trace),$(size),$(algo),$(intensity)))))))

# Args: $(1)=trace $(2)=cache_size $(3)=algo $(4)=intensity
define make_gaia_log_rule
$(LOGS_DIR)/$(4)/$(1)_cache_$(2)_$(3).log: evaluate.py | $(LOGS_DIR)/$(4) $(SEGMENTS_DIR)/$(4)
	@echo "Evaluating $(1) cache=$(2) algo=$(3) intensity=$(4)"
	$(PYTHON) evaluate.py --trace-file ../../datasets/gaia/$(1).jsonl --cache-size $(2) --algo $(3) --compute-intensity $(4) --dump-segments $(SEGMENTS_DIR)/$(4)/$(1)_cache_$(2)_$(3)_segments.jsonl --latency-file measurement/results/qwen3_coder_30b_latency.json > $$@
endef

$(foreach intensity,$(INTENSITIES),$(foreach trace,$(GAIA_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(eval $(call make_gaia_log_rule,$(trace),$(size),$(algo),$(intensity)))))))

# Args: $(1)=trace $(2)=cache_size $(3)=algo $(4)=intensity
define make_chutes_log_rule
$(LOGS_DIR)/$(4)/$(1)_cache_$(2)_$(3).log: evaluate.py | $(LOGS_DIR)/$(4) $(SEGMENTS_DIR)/$(4)
	@echo "Evaluating $(1) cache=$(2) algo=$(3) intensity=$(4)"
	$(PYTHON) evaluate.py --trace-file $(CHUTES_DIR)/$(1).jsonl --cache-size $(2) --algo $(3) --compute-intensity $(4) --dump-segments $(SEGMENTS_DIR)/$(4)/$(1)_cache_$(2)_$(3)_segments.jsonl --latency-file measurement/results/qwen3_coder_30b_latency.json > $$@
endef

$(foreach intensity,$(INTENSITIES),$(foreach trace,$(CHUTES_TRACES),$(foreach size,$(CACHE_SIZES),$(foreach algo,$(ALGOS),$(eval $(call make_chutes_log_rule,$(trace),$(size),$(algo),$(intensity)))))))

# Latency benchmarking rules
define make_qwen_latency_rule
$(LATENCY_DIR)/$(1)_cache_$(2)_$(3).json: benchmark_latency.py | $(LATENCY_DIR)
	@echo "Latency benchmarking $(1) with cache size $(2) using algo $(3)"
	VLLM_ENABLE_V1_MULTIPROCESSING=0 $(PYTHON) benchmark_latency.py \
		--trace-file ../../datasets/qwen-bailian-usagetraces-anon/$(1).jsonl \
		--cache-size $(2) \
		--algo $(3) \
		--model $(MODEL_PATH) \
		--attention-backend FLASHINFER \
		--output-file $$@
endef

$(foreach trace,$(QWEN_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(eval $(call make_qwen_latency_rule,$(trace),$(size),$(algo))))))

define make_gaia_latency_rule
$(LATENCY_DIR)/$(1)_cache_$(2)_$(3).json: benchmark_latency.py | $(LATENCY_DIR)
	@echo "Latency benchmarking $(1) with cache size $(2) using algo $(3)"
	VLLM_ENABLE_V1_MULTIPROCESSING=0 $(PYTHON) benchmark_latency.py \
		--trace-file ../../datasets/gaia/$(1).jsonl \
		--cache-size $(2) \
		--algo $(3) \
		--model $(MODEL_PATH) \
		--attention-backend FLASHINFER \
		--output-file $$@
endef

$(foreach trace,$(GAIA_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(eval $(call make_gaia_latency_rule,$(trace),$(size),$(algo))))))

define make_chutes_latency_rule
$(LATENCY_DIR)/$(1)_cache_$(2)_$(3).json: benchmark_latency.py | $(LATENCY_DIR)
	@echo "Latency benchmarking $(1) with cache size $(2) using algo $(3)"
	VLLM_ENABLE_V1_MULTIPROCESSING=0 $(PYTHON) benchmark_latency.py \
		--trace-file $(CHUTES_DIR)/$(1).jsonl \
		--cache-size $(2) \
		--algo $(3) \
		--model $(MODEL_PATH) \
		--attention-backend FLASHINFER \
		--output-file $$@
endef

$(foreach trace,$(CHUTES_TRACES),$(foreach size,$(LATENCY_CACHE_SIZES),$(foreach algo,$(LATENCY_ALGOS),$(eval $(call make_chutes_latency_rule,$(trace),$(size),$(algo))))))

latency-benchmark: | $(LATENCY_DIR)
	@$(MAKE) -j1 $(LATENCY_TARGETS)

# Throughput benchmarking rules
QWEN_THROUGHPUT_TARGETS = $(foreach trace,$(THROUGHPUT_TRACES),$(foreach size,$(THROUGHPUT_CACHE_SIZES),$(foreach algo,$(THROUGHPUT_ALGOS),$(foreach conc,$(THROUGHPUT_CONCURRENCIES),$(foreach out,$(THROUGHPUT_OUTPUT_LENGTHS),$(THROUGHPUT_DIR)/$(trace)_cache_$(size)_$(algo)_concurrency_$(conc)_output_$(out).json)))))

THROUGHPUT_TARGETS = $(QWEN_THROUGHPUT_TARGETS)

define make_qwen_throughput_rule
$(THROUGHPUT_DIR)/$(1)_cache_$(2)_$(3)_concurrency_$(4)_output_$(5).json: benchmark_throughput.py | $(THROUGHPUT_DIR)
	@echo "Throughput benchmarking $(1) cache=$(2) algo=$(3) concurrency=$(4) output=$(5)"
	$(PYTHON) benchmark_throughput.py \
		--trace-file ../../datasets/qwen-bailian-usagetraces-anon/$(1).jsonl \
		--cache-size $(2) \
		--algo $(3) \
		--model $(MODEL_PATH) \
		--attention-backend FLASHINFER \
		--max-concurrent $(4) \
		--output-length $(5) \
		--output-file $$@
endef

$(foreach trace,$(THROUGHPUT_TRACES),$(foreach size,$(THROUGHPUT_CACHE_SIZES),$(foreach algo,$(THROUGHPUT_ALGOS),$(foreach conc,$(THROUGHPUT_CONCURRENCIES),$(foreach out,$(THROUGHPUT_OUTPUT_LENGTHS),$(eval $(call make_qwen_throughput_rule,$(trace),$(size),$(algo),$(conc),$(out))))))))

throughput-benchmark: | $(THROUGHPUT_DIR)
	@$(MAKE) -j1 $(THROUGHPUT_TARGETS)

throughput-plots: $(THROUGHPUT_TARGETS) | $(THROUGHPUT_PLOTS_DIR)
	@echo "Generating throughput visualization plots..."
	@$(PYTHON) visualize_throughput.py --throughput-dir $(THROUGHPUT_DIR) --output-dir $(THROUGHPUT_PLOTS_DIR)

throughput-plots-only: | $(THROUGHPUT_PLOTS_DIR)
	@echo "Generating throughput visualization plots (without running benchmarks)..."
	@$(PYTHON) visualize_throughput.py --throughput-dir $(THROUGHPUT_DIR) --output-dir $(THROUGHPUT_PLOTS_DIR)

throughput-benchmark-and-plot: throughput-benchmark throughput-plots


latency-plots: $(LATENCY_TARGETS) | $(LATENCY_PLOTS_DIR)
	@echo "Generating latency visualization plots..."
	@$(PYTHON) visualize_latency.py --latency-dir $(LATENCY_DIR) --output-dir $(LATENCY_PLOTS_DIR)

latency-plots-only: | $(LATENCY_PLOTS_DIR)
	@echo "Generating latency visualization plots (without running benchmarks)..."
	@$(PYTHON) visualize_latency.py --latency-dir $(LATENCY_DIR) --output-dir $(LATENCY_PLOTS_DIR)

latency-benchmark-and-plot: latency-benchmark latency-plots

all-plots: $(LOG_TARGETS) | $(PLOTS_DIR) $(SEGMENTS_PLOTS_DIR)
	@for intensity in $(INTENSITIES); do \
	    echo "[$$intensity] visualize_results"; \
	    mkdir -p $(PLOTS_DIR)/$$intensity $(SEGMENTS_PLOTS_DIR)/$$intensity; \
	    $(PYTHON) visualize_results.py --logs-dir $(LOGS_DIR)/$$intensity --output-dir $(PLOTS_DIR)/$$intensity; \
	    echo "[$$intensity] analyze_segments"; \
	    $(PYTHON) analyze_segments.py --segments-dir $(SEGMENTS_DIR)/$$intensity --output-dir $(SEGMENTS_PLOTS_DIR)/$$intensity; \
	done

plots-only: | $(PLOTS_DIR)
	@for intensity in $(INTENSITIES); do \
	    if [ -d $(LOGS_DIR)/$$intensity ]; then \
	        echo "[$$intensity] visualize_results"; \
	        mkdir -p $(PLOTS_DIR)/$$intensity; \
	        $(PYTHON) visualize_results.py --logs-dir $(LOGS_DIR)/$$intensity --output-dir $(PLOTS_DIR)/$$intensity; \
	    fi; \
	done

plots-segments: | $(SEGMENTS_PLOTS_DIR)
	@for intensity in $(INTENSITIES); do \
	    if [ -d $(SEGMENTS_DIR)/$$intensity ]; then \
	        echo "[$$intensity] analyze_segments"; \
	        mkdir -p $(SEGMENTS_PLOTS_DIR)/$$intensity; \
	        $(PYTHON) analyze_segments.py --segments-dir $(SEGMENTS_DIR)/$$intensity --output-dir $(SEGMENTS_PLOTS_DIR)/$$intensity; \
	    fi; \
	done

eval-and-plot: eval-parallel all-plots

clean-logs:
	rm -rf $(LOGS_DIR)
	rm -rf $(SEGMENTS_DIR)

clean-plots:
	rm -rf $(PLOTS_DIR)

clean-latency:
	rm -rf $(LATENCY_DIR)

clean-throughput:
	rm -rf $(THROUGHPUT_DIR)

clean-all: clean-logs clean-plots clean-latency clean-throughput

.PHONY: help evaluate eval-parallel clean-logs clean-plots clean-latency clean-throughput clean-all all-plots plots-only plots-segments eval-and-plot latency-benchmark latency-plots latency-plots-only latency-benchmark-and-plot throughput-benchmark throughput-plots throughput-plots-only throughput-benchmark-and-plot
