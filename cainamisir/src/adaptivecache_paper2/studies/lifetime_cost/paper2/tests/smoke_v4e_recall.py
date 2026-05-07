"""Phase 4e GPU smoke: verify recalls_via_unmask increments end-to-end.

Drives ONE chat with attention_mask_mode=True. Manually calls
`model.queue_recall(obs_text)` BEFORE the chat fires. Expected:
the engine generates the obs's content-hash memento_id during
compaction, finds it in the recall queue, SKIPs masking, bumps
`ENGINE_STATS["recalls_via_unmask"]`. We read that counter from the
volume after the run and verify it's > 0.

If recalls_via_unmask == 0, either:
* obs_id mismatch between adapter (tokenizes obs_text directly) and
  engine (extracts obs span from rendered prompt at compaction time)
* IPC file path mismatch
* queue_recall fired AFTER engine had already compacted

Run (needs GPU):
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.smoke_v4e_recall
"""
from __future__ import annotations

import os

os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
os.environ.setdefault("HF_HOME", "/scratch/hf/")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")

# Make engine + adapter use a deterministic queue path on the volume.
os.environ.setdefault(
    "PAPER2_ENGINE_STATS_PATH", "/scratch/out/v4e_smoke/engine_stats.jsonl"
)
os.environ.setdefault(
    "PAPER2_RECALL_QUEUE_PATH", "/scratch/out/v4e_smoke/recall_queue.jsonl"
)

from studies.lifetime_cost.paper2.adapters.memento_vllm import MementoVLLMModel


MODEL = os.environ.get("PAPER2_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")


def main() -> int:
    print("Building MementoVLLMModel with attention_mask_mode=True ...")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=float(os.environ.get("PAPER2_GPU_UTIL", "0.92")),
        max_model_len=int(os.environ.get("PAPER2_MAX_LEN", "32000")),
        masking_enabled=True,
        last_only_masking=False,  # Phase 4e wants markers on every memento'd msg
        auto_capture_mementos=True,
        attention_mask_mode=True,
        debug_masking=False,
    )

    obs_text = "OBSERVATION " * 200  # ~200+ tokens
    memento = "X summarized: observation pattern repeats"

    print(f"\n--- Chat 1 (no recall queued — should mask the obs) ---")
    msgs1 = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Read X and summarize."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "x", "function": {"name": "read_file", "arguments": {"path": "X"}}},
        ]},
        {"role": "tool", "tool_call_id": "x", "content": obs_text, "memento": memento},
        {"role": "user", "content": "What did you see?"},
    ]
    resp1 = model.chat(msgs1, max_tokens=64)
    print(f"masked-response head: {resp1.content[:120]!r}")

    # Phase 4e: queue the obs for recall on the next compaction.
    print(f"\n--- Phase 4e: queue_recall ---")
    obs_id = model.queue_recall(obs_text)
    print(f"queued obs_id: {obs_id}")

    print(f"\n--- Chat 2 (obs_id queued — should UNmask) ---")
    msgs2 = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Read X and summarize."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "x", "function": {"name": "read_file", "arguments": {"path": "X"}}},
        ]},
        {"role": "tool", "tool_call_id": "x", "content": obs_text, "memento": memento},
        {"role": "user", "content": "What were the EXACT contents — quote them?"},
    ]
    resp2 = model.chat(msgs2, max_tokens=64)
    print(f"unmasked-response head: {resp2.content[:120]!r}")

    # Read engine stats from disk.
    import json
    stats_path = os.environ["PAPER2_ENGINE_STATS_PATH"]
    print(f"\nReading engine stats from {stats_path} ...")
    if not os.path.exists(os.path.dirname(stats_path)):
        os.makedirs(os.path.dirname(stats_path), exist_ok=True)
    found = []
    for fname in sorted(os.listdir(os.path.dirname(stats_path))):
        full = os.path.join(os.path.dirname(stats_path), fname)
        if not fname.startswith(os.path.basename(stats_path)):
            continue
        try:
            d = json.loads(open(full).read())
            print(f"  {fname}: {d}")
            found.append(d)
        except Exception as e:
            print(f"  {fname}: parse error {e}")

    if not found:
        print("WARN: no engine_stats files on disk — engine subprocess may not have run.")
        return 1

    aggregate = {}
    for d in found:
        for k, v in d.items():
            if k == "pid":
                continue
            aggregate[k] = aggregate.get(k, 0) + v
    print(f"\nAggregate: {aggregate}")

    if aggregate.get("recalls_via_unmask", 0) > 0:
        print()
        print("PHASE 4E GPU SMOKE PASS: engine consumed at least one recall_id.")
        return 0
    else:
        print()
        print("PHASE 4E SMOKE FAIL: recalls_via_unmask = 0.")
        print("Engine ran compaction but didn't find the queued obs_id.")
        print("Likely: obs_id mismatch between adapter and engine, or queue path mismatch.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
