"""Microbenchmark: masking on vs off, fixed prompt, measure wall-clock.

Constructs a synthetic 5-turn conversation where each user (tool) message
holds ~4000 chars of obs. Runs the same prompt twice — once with masking
enabled (each tool message tagged with a short fixed memento), once with
masking disabled — and reports per-prefill wall clock + prompt tokens.

This bypasses the agent loop and the Haiku writer to give a clean signal
on whether the engine-level masking actually reduces compute.

Run from repo root:
    cd /home/vlad/adaptivecache-paper2
    PAPER2_MODEL="Qwen/Qwen3-4B-Instruct-2507" \
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.microbench_masking
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Path setup for `python -m` and direct script invocation.
_here = Path(__file__).resolve().parent
_repo_root = _here.parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
os.environ.setdefault("HF_HOME", "/scratch/hf/")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")

from studies.lifetime_cost.paper2.adapters.memento_vllm import MementoVLLMModel


MODEL = os.environ.get("PAPER2_MODEL", "Qwen/Qwen3-4B-Instruct-2507")
GPU_MEM_UTIL = float(os.environ.get("PAPER2_GPU_UTIL", "0.4"))
MAX_MODEL_LEN = int(os.environ.get("PAPER2_MAX_LEN", "32000"))
N_TURNS = int(os.environ.get("PAPER2_N_TURNS", "5"))
OBS_CHARS = int(os.environ.get("PAPER2_OBS_CHARS", "4000"))
KEEP_LAST_N = int(os.environ.get("PAPER2_KEEP_LAST_N", "0"))
MEMENTO_TEXT = "[masked obs] file had 50 functions, 12 classes, key entrypoint at line 42"


def _make_messages(n_turns: int, obs_chars: int, with_memento: bool, last_only: bool = False):
    """Build a synthetic conversation with n_turns of tool calls + obs.

    Each turn: user asks something, assistant calls a tool, tool returns
    `obs_chars` of repeated text. With masking on, each tool message
    gets a `memento` field that the adapter expands into block + summary
    tokens.

    If last_only=True and with_memento=True, only the FINAL tool message
    gets markers; earlier ones are rendered as plain text. Tests whether
    the multi-turn slowdown is caused by cascading rewinds (5 markers
    in prompt → engine rewinds to earliest summary_start → re-prefills
    everything after).
    """
    msgs = [{"role": "user", "content": "Read the codebase and summarize the architecture."}]
    obs_filler = ("def function_n(): pass\n# This is filler line for measurement.\n" * (obs_chars // 70))[:obs_chars]
    for i in range(n_turns):
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"tc_{i}",
                "type": "function",
                "function": {"name": "read_file", "arguments": {"path": f"src/module_{i}.py"}},
            }],
        })
        tool_msg = {
            "role": "tool",
            "tool_call_id": f"tc_{i}",
            "content": f"[file src/module_{i}.py, {obs_chars} chars]\n{obs_filler}",
        }
        is_last_tool = (i == n_turns - 1)
        if with_memento and (not last_only or is_last_tool):
            tool_msg["memento"] = MEMENTO_TEXT
        msgs.append(tool_msg)
    msgs.append({"role": "user", "content": "Now produce a one-sentence summary of the codebase."})
    return msgs


LAST_ONLY = os.environ.get("PAPER2_LAST_ONLY", "0") == "1"


def run(label: str, masking: bool) -> dict:
    print(f"\n--- {label} (masking={masking}, last_only={LAST_ONLY}) ---")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=masking,
        keep_last_n_blocks=KEEP_LAST_N,
        debug_masking=False,
    )
    msgs = _make_messages(N_TURNS, OBS_CHARS, with_memento=masking, last_only=LAST_ONLY)

    # Warmup so cuda graphs / kernel autotuning don't taint the measurement.
    _ = model.chat(msgs, max_tokens=8)

    timings = []
    for i in range(3):
        t0 = time.perf_counter()
        resp = model.chat(msgs, max_tokens=64)
        dt = (time.perf_counter() - t0) * 1000
        timings.append(dt)
        print(f"  trial {i+1}: {dt:.1f} ms, prompt={resp.usage.prompt_tokens}, cached={resp.usage.cached_tokens}, output={resp.usage.completion_tokens}")
    return {
        "label": label,
        "masking": masking,
        "n_turns": N_TURNS,
        "obs_chars": OBS_CHARS,
        "wall_ms": timings,
        "wall_ms_median": sorted(timings)[len(timings) // 2],
        "prompt_tokens": resp.usage.prompt_tokens,
        "cached_tokens": resp.usage.cached_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "completion_text": resp.content[:200],
    }


def main():
    print(f"Microbench: model={MODEL}, n_turns={N_TURNS}, obs_chars={OBS_CHARS}")
    a = run("baseline", masking=False)
    b = run("memento", masking=True)

    print("\n=== summary ===")
    print(f"  baseline median wall: {a['wall_ms_median']:.1f} ms (prompt_tokens={a['prompt_tokens']})")
    print(f"  memento  median wall: {b['wall_ms_median']:.1f} ms (prompt_tokens={b['prompt_tokens']})")
    delta = a["wall_ms_median"] - b["wall_ms_median"]
    pct = (delta / a["wall_ms_median"]) * 100 if a["wall_ms_median"] else 0
    print(f"  delta:                {delta:+.1f} ms ({pct:+.1f}%)")
    print(f"  baseline output: {a['completion_text']!r}")
    print(f"  memento output:  {b['completion_text']!r}")


if __name__ == "__main__":
    main()
