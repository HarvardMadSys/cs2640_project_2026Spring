"""KV-cache simulator package."""

from simulator.cache import CacheEntry, KVCacheSimulator
from simulator.global_cache import (
    GlobalSimulationResult,
    SharedGlobalKVCacheSimulator,
    run_shared_events,
    validate_global_result,
)
from simulator.global_workload import (
    generate_concurrent_request_traces,
    generate_shared_global_events,
    generate_shifted_global_events,
    interleave_request_traces,
    summarize_events,
)
from simulator.policies import FIFOPolicy, LRUPolicy
from simulator.runner import SimulationResult, run_trace, validate_result
from simulator.workload import (
    DEFAULT_WORKLOAD_SEEDS,
    generate_default_workloads,
    generate_long_context_workload,
    generate_multiturn_workload,
    generate_short_prompt_workload,
    make_default_workload_seeds,
    summarize_trace,
    summarize_workloads,
)

__all__ = [
    "CacheEntry",
    "DEFAULT_WORKLOAD_SEEDS",
    "FIFOPolicy",
    "GlobalSimulationResult",
    "KVCacheSimulator",
    "LRUPolicy",
    "SharedGlobalKVCacheSimulator",
    "SimulationResult",
    "generate_concurrent_request_traces",
    "generate_default_workloads",
    "generate_long_context_workload",
    "generate_multiturn_workload",
    "generate_shared_global_events",
    "generate_short_prompt_workload",
    "generate_shifted_global_events",
    "interleave_request_traces",
    "make_default_workload_seeds",
    "run_trace",
    "run_shared_events",
    "summarize_events",
    "summarize_trace",
    "summarize_workloads",
    "validate_global_result",
    "validate_result",
]
