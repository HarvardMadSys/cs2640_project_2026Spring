"""Benchmarks. Each adapter exposes Tasks compatible with the runner.

A Task = a starting message list + a tool environment + an evaluator.
Benchmarks chosen specifically for long-context agent loops where
compaction actually triggers (not single-shot QA).
"""

from .base import Task, ToolEnv, Tool
from .swebench_replay import SWEBenchReplay
from .swebench_live import SWEBenchLive
from .taubench import TauBench
from .gaia import GAIA
from .longdoc import LongDocAgent


REGISTRY = {
    "swebench_replay": SWEBenchReplay,
    "swebench_live": SWEBenchLive,
    "taubench": TauBench,
    "gaia": GAIA,
    "longdoc": LongDocAgent,
}


def build_benchmark(name: str, **kwargs):
    if name not in REGISTRY:
        raise KeyError(f"Unknown benchmark {name!r}. Choices: {list(REGISTRY)}")
    return REGISTRY[name](**kwargs)


__all__ = ["Task", "ToolEnv", "Tool", "build_benchmark", "REGISTRY",
           "SWEBenchReplay", "SWEBenchLive", "TauBench", "GAIA", "LongDocAgent"]
