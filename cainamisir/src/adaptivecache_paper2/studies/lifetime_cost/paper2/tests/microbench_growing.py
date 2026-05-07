"""Growing-trajectory microbench: per-turn cost as conversation extends.

The prior microbench measured the same prompt three times (warm cache).
Real agent loops grow turn-by-turn — turn N+1's prompt is turn N's
prompt plus a new asst response and a new tool obs. This script
simulates that growth and measures per-turn wall clock for both
variants.

Baseline (no masking): each turn's prompt includes the FULL obs of
every prior tool message. Prompt size grows by ~obs_chars each turn.

Memento (last_only_masking, default): each turn's prompt includes only
the MEMENTO text of every prior tool message + full obs + markers
around the LATEST tool message. Prompt grows by ~memento_size per turn
(~30 tokens) instead of ~obs_chars (~800 tokens).

Run:
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.microbench_growing
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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
N_TURNS = int(os.environ.get("PAPER2_N_TURNS", "10"))
OBS_CHARS = int(os.environ.get("PAPER2_OBS_CHARS", "4000"))
MAX_TOKENS = int(os.environ.get("PAPER2_MAX_TOKENS", "16"))  # constant output length

MEMENTO_TEXT = "[evicted] file had 50 functions, 12 classes, key entrypoint at line 42"


def _obs(i: int) -> str:
    filler = ("def function_n(): pass\n# This is filler line for measurement.\n" * (OBS_CHARS // 70))[:OBS_CHARS]
    return f"[file src/module_{i}.py, {OBS_CHARS} chars]\n{filler}"


def _build_messages_for_turn(turn_idx: int, *, with_memento: bool) -> list[dict]:
    """Build the prompt as it would look at turn `turn_idx` (0-indexed),
    after the assistant has just made its tool_call_{turn_idx} and the
    tool returned its obs. The model is now being asked to take the
    next action.

    With masking on: every prior tool message has `memento` set. The
    adapter renders all but the latest as plain inline text and the
    latest with full markers.
    """
    msgs: list[dict] = [
        {"role": "user", "content": "Read each module to understand the codebase."}
    ]
    for i in range(turn_idx + 1):
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"tc_{i}",
                "type": "function",
                "function": {"name": "read_file", "arguments": {"path": f"src/module_{i}.py"}},
            }],
        })
        tool = {"role": "tool", "tool_call_id": f"tc_{i}", "content": _obs(i)}
        if with_memento:
            tool["memento"] = MEMENTO_TEXT
        msgs.append(tool)
    msgs.append({"role": "user", "content": "Now decide the next action."})
    return msgs


def run(label: str, masking: bool):
    print(f"\n--- {label} (masking={masking}) ---")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=masking,
        debug_masking=False,
    )

    # Warm: hit the engine once so cuda graphs / JIT don't taint turn 0.
    _ = model.chat(_build_messages_for_turn(0, with_memento=masking), max_tokens=4)

    rows = []
    for t in range(N_TURNS):
        msgs = _build_messages_for_turn(t, with_memento=masking)
        t0 = time.perf_counter()
        resp = model.chat(msgs, max_tokens=MAX_TOKENS)
        dt_ms = (time.perf_counter() - t0) * 1000
        rows.append({
            "turn": t,
            "wall_ms": dt_ms,
            "prompt_tokens": resp.usage.prompt_tokens,
            "cached_tokens": resp.usage.cached_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        })
        print(f"  turn {t:2d}: {dt_ms:6.1f} ms, prompt={resp.usage.prompt_tokens:5d}, cached={resp.usage.cached_tokens:5d}, out={resp.usage.completion_tokens}")
    return rows


def main():
    print(f"Growing trajectory: model={MODEL}, n_turns={N_TURNS}, obs_chars={OBS_CHARS}, max_out={MAX_TOKENS}")
    a = run("baseline", masking=False)
    b = run("memento", masking=True)

    print("\n=== per-turn wall ms ===")
    print(f"{'turn':>4} {'baseline':>10} {'memento':>10} {'delta_ms':>10} {'b_tok':>7} {'m_tok':>7}")
    for ra, rb in zip(a, b):
        delta = rb["wall_ms"] - ra["wall_ms"]
        print(f"{ra['turn']:>4} {ra['wall_ms']:>10.1f} {rb['wall_ms']:>10.1f} {delta:>+10.1f} {ra['prompt_tokens']:>7d} {rb['prompt_tokens']:>7d}")

    total_a = sum(r["wall_ms"] for r in a)
    total_b = sum(r["wall_ms"] for r in b)
    print(f"\n  total baseline wall: {total_a:.0f} ms")
    print(f"  total memento  wall: {total_b:.0f} ms")
    print(f"  total delta:        {total_b - total_a:+.0f} ms ({(total_b - total_a)/total_a*100:+.1f}%)")


if __name__ == "__main__":
    main()
