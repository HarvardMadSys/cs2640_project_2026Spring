"""
Speculation accuracy experiment.

Goal: measure how well the model can predict its own tool-call behavior:
  (a) one-shot: at task start, predict total tool calls + average duration
  (b) per-step: before each tool call, predict remaining count + next duration

Standalone — does not modify or share state with run.py / agent/loop.py
beyond importing read-only constants and the local vLLM engine.

Outputs go to traces/final/speculation/<timestamp>/ :
  - one JSON per task (full record)
  - speculation_results.csv (one row per per-step prediction)
  - speculation_oneshot.csv (one row per task)
"""

import argparse
import json
import os
import re
import time
import datetime as _dt
from typing import Dict, List, Optional, Tuple

import numpy as np

import config  # sets BRAVE_API_KEY env var
from agent.loop import SYSTEM_PROMPTS, TOOL_SCHEMAS
from agent.tools import dispatch_tool, clear_namespace
from agent.vllm_engine import InstrumentedEngine

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(ROOT, "traces", "final", "speculation")

AGENT_TYPES = ["data_analysis", "sql", "rag", "swe_bench"]
TASKS_PER_AGENT = 5
MAX_TURNS_BY_AGENT = {"rag": 8, "sql": 12, "data_analysis": 12, "swe_bench": 20}
MAX_TOOL_CALLS_PER_TASK = 8


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_agent_tasks(agent_type: str, n: int) -> List[dict]:
    if agent_type == "data_analysis":
        from tasks.data_analysis import TASKS
    elif agent_type == "sql":
        from tasks.sql import TASKS
    elif agent_type == "rag":
        from tasks.rag import TASKS
    elif agent_type == "swe_bench":
        from tasks.swe_bench import TASKS
    else:
        raise ValueError(agent_type)
    return list(TASKS)[:n]


def setup_task_env(task: dict):
    """For swe_bench, clone the repo and apply env_setup. Returns workspace
    path or None. For other agents, no-op."""
    if task.get("agent_type") == "swe_bench":
        from tasks.swe_bench import setup_workspace
        return setup_workspace(task)
    return None


def cleanup_task_env(task: dict):
    if task.get("agent_type") == "swe_bench":
        from tasks.swe_bench import cleanup_workspace
        try:
            cleanup_workspace(task)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# JSON extraction from free-form model output
# ---------------------------------------------------------------------------

_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def extract_json_obj(text: str) -> Optional[dict]:
    if not text:
        return None
    # Strip code fences
    t = text.replace("```json", "").replace("```", "")
    matches = _JSON_OBJ_RE.findall(t)
    for m in matches[::-1]:  # prefer last (model often reasons then concludes)
        try:
            return json.loads(m)
        except Exception:
            continue
    return None


def coerce_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def coerce_int(v) -> Optional[int]:
    f = coerce_float(v)
    if f is None:
        return None
    return int(round(f))


# ---------------------------------------------------------------------------
# Prediction prompts
# ---------------------------------------------------------------------------

ONE_SHOT_PRED_PROMPT = (
    "Before you start solving this task, predict your own tool-call behavior. "
    "Estimate (1) the TOTAL number of tool calls you will make to solve this task, "
    "and (2) the AVERAGE duration in seconds of each of those tool calls. "
    "Reply with ONE JSON object only, no other text:\n"
    '{"predicted_total_tool_calls": <int>, "predicted_avg_tool_duration_s": <float>}'
)

PER_STEP_PRED_PROMPT = (
    "Pause before your next tool call. Predict: "
    "(1) how many MORE tool calls you will make INCLUDING the next one to finish the task, "
    "(2) how long the NEXT tool call will take in seconds. "
    "Reply with ONE JSON object only, no other text:\n"
    '{"remaining_tool_calls": <int>, "next_tool_duration_s": <float>}'
)


# Calibration metadata + one worked example per agent type. Used only when
# --calibrated is passed; injected into the PREDICTION prompts (not the
# task system prompt). Numbers are generic priors, NOT derived from the
# uncalibrated run, so this is the realistic deploy-time setting.
CALIBRATION = {
    "data_analysis": (
        "Calibration metadata for python_exec (persistent Python REPL):\n"
        "- Trivial computation (arithmetic, df.head()): 0.05-0.2 s\n"
        "- Data inspection (df.info, value_counts): 0.2-1 s\n"
        "- Parquet/CSV multi-file load (multi-GB): 5-30 s\n"
        "- Aggregation / groupby on large DataFrame: 1-10 s\n"
        "- Training a small sklearn model: 5-60 s\n"
        "Worked example: loading 24 NYC-taxi parquet files into one DataFrame "
        "takes about 25 s; df.head() returns in about 0.1 s."
    ),
    "sql": (
        "Calibration metadata for sql_exec (DuckDB), measured medians:\n"
        "- Schema inspection (SHOW TABLES, PRAGMA table_info, DESCRIBE): ~0.05 s\n"
        "- SELECT ... LIMIT few rows: ~0.05 s\n"
        "- Aggregation / GROUP BY / JOIN: typically 0.3-1 s, "
        "can reach 5-7 s on the largest tables\n"
        "Most calls in this benchmark are schema or LIMIT queries (~0.05 s); "
        "only the final answer query is usually a GROUP BY (~1 s).\n"
        "Worked example: PRAGMA table_info('clients') ~0.05 s; "
        "SELECT COUNT(*) FROM loans GROUP BY status ~0.7 s."
    ),
    "rag": (
        "Calibration metadata for web tools, measured medians:\n"
        "- web_search (Brave API): ~0.7 s round trip\n"
        "- fetch_url: ~0.6 s typical\n"
        "Both calls are fast and fairly constant; variance is small.\n"
        "Worked example: web_search('paris olympics 2024') returns in ~0.7 s; "
        "fetch_url on a typical Wikipedia article ~0.6 s."
    ),
    "swe_bench": (
        "Calibration metadata for SWE-bench tools:\n"
        "- view_file (read source with line numbers): 0.05-0.3 s\n"
        "- search_dir (grep across .py files): 0.1-1 s\n"
        "- edit_file (exact-string replace): 0.05-0.2 s\n"
        "- bash_exec (pytest on a single test, grep, find): 1-30 s; "
        "pytest typically 2-15 s\n"
        "Worked example: view_file on a 200-line module takes about 0.1 s; "
        "pytest -xvs <one test> takes about 5 s."
    ),
}


# ---------------------------------------------------------------------------
# Engine wrapper for plain (no-tool) generations used for predictions
# ---------------------------------------------------------------------------

def generate_text(engine: InstrumentedEngine, messages: List[dict],
                  max_tokens: int = 200) -> str:
    """Generate without tools and return raw text."""
    from vllm import SamplingParams
    prompt = engine.tokenizer.apply_chat_template(
        messages, tools=None, add_generation_prompt=True, tokenize=False
    )
    params = SamplingParams(
        temperature=0,
        max_tokens=max_tokens,
        stop_token_ids=engine._stop_token_ids(),
    )
    out = engine.llm.generate([prompt], params)
    return out[0].outputs[0].text.strip()


# ---------------------------------------------------------------------------
# Run a single task with speculation
# ---------------------------------------------------------------------------

def run_task_with_speculation(engine: InstrumentedEngine, task: dict,
                              out_dir: str, calibrated: bool = False) -> dict:
    agent_type = task["agent_type"]
    sys_prompt = SYSTEM_PROMPTS[agent_type]
    tools = TOOL_SCHEMAS[agent_type]
    max_turns = MAX_TURNS_BY_AGENT[agent_type]
    cal_block = (CALIBRATION[agent_type] + "\n\n") if calibrated else ""

    record = {
        "task_id": task["id"],
        "agent_type": agent_type,
        "model": engine.model_name,
        "calibrated": calibrated,
        "started_at": time.time(),
        "one_shot": None,
        "per_step": [],
        "actuals": {},
        "errors": [],
    }

    # For swe_bench, prepare the workspace before any tool call can run.
    try:
        setup_task_env(task)
    except Exception as e:
        record["errors"].append(f"setup_task_env: {e}")

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": task["prompt"]},
    ]

    # ---- (a) ONE-SHOT prediction --------------------------------------
    pred_msgs = messages + [{"role": "user", "content": cal_block + ONE_SHOT_PRED_PROMPT}]
    try:
        raw = generate_text(engine, pred_msgs, max_tokens=128)
    except Exception as e:
        raw = ""
        record["errors"].append(f"oneshot_gen: {e}")
    obj = extract_json_obj(raw) or {}
    record["one_shot"] = {
        "raw": raw,
        "predicted_total_tool_calls": coerce_int(obj.get("predicted_total_tool_calls")),
        "predicted_avg_tool_duration_s": coerce_float(obj.get("predicted_avg_tool_duration_s")),
    }
    print(f"  [oneshot] total={record['one_shot']['predicted_total_tool_calls']} "
          f"avg={record['one_shot']['predicted_avg_tool_duration_s']}")

    # ---- (b) ReAct loop with PER-STEP predictions ---------------------
    actual_durations: List[float] = []
    tool_call_count = 0

    for turn_idx in range(max_turns):
        # Per-step prediction (only if a tool call could happen this turn)
        per_step_pred = {"remaining_tool_calls": None, "next_tool_duration_s": None,
                         "raw": "", "turn_index": turn_idx,
                         "tool_calls_so_far": tool_call_count}
        if tool_call_count < MAX_TOOL_CALLS_PER_TASK:
            pred_msgs = messages + [{"role": "user", "content": cal_block + PER_STEP_PRED_PROMPT}]
            try:
                praw = generate_text(engine, pred_msgs, max_tokens=96)
            except Exception as e:
                praw = ""
                record["errors"].append(f"perstep_gen turn {turn_idx}: {e}")
            pobj = extract_json_obj(praw) or {}
            per_step_pred = {
                "raw": praw,
                "remaining_tool_calls": coerce_int(pobj.get("remaining_tool_calls")),
                "next_tool_duration_s": coerce_float(pobj.get("next_tool_duration_s")),
                "turn_index": turn_idx,
                "tool_calls_so_far": tool_call_count,
            }

        # Real generation with tools
        active_tools = tools if tool_call_count < MAX_TOOL_CALLS_PER_TASK else []
        try:
            raw, tcalls, finish, usage = engine.generate_turn(
                messages=messages, tools=active_tools, max_tokens=2048
            )
        except Exception as e:
            record["errors"].append(f"gen_turn {turn_idx}: {e}")
            break

        assistant_msg = {"role": "assistant", "content": raw}
        if tcalls:
            assistant_msg["tool_calls"] = [
                {"id": tc.get("id") or f"tc_{turn_idx}_{i}",
                 "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for i, tc in enumerate(tcalls)
            ]
        messages.append(assistant_msg)

        if not tcalls:
            # No tool call this turn -> task done (or model gave up)
            # Record per-step pred only if we asked for it AND a tool call happened.
            break

        # Execute tool calls and time them
        for i, tc in enumerate(tcalls):
            t0 = time.time()
            try:
                result = dispatch_tool(tc["name"], tc["arguments"], task)
            except Exception as e:
                result = f"ERROR: {e}"
            dt = time.time() - t0
            tool_call_count += 1
            actual_durations.append(dt)

            # Attach per-step pred to the FIRST tool call of this turn
            if i == 0:
                per_step_pred["actual_next_tool_duration_s"] = dt
                per_step_pred["actual_tool_name"] = tc["name"]
                record["per_step"].append(per_step_pred)

            messages.append({
                "role": "tool",
                "tool_call_id": assistant_msg["tool_calls"][i]["id"],
                "name": tc["name"],
                "content": result if isinstance(result, str) else str(result),
            })

            if tool_call_count >= MAX_TOOL_CALLS_PER_TASK:
                break

    # Cleanup task state (python_exec namespace etc.)
    try:
        clear_namespace(task["id"])
    except Exception:
        pass
    cleanup_task_env(task)

    # Fill in actuals
    record["actuals"] = {
        "total_tool_calls": tool_call_count,
        "tool_durations_s": actual_durations,
        "avg_tool_duration_s": float(np.mean(actual_durations)) if actual_durations else None,
    }

    # After the loop ends, every per_step prediction now knows the FINAL count
    for ps in record["per_step"]:
        # remaining at the time of the prediction = total - calls_already_made
        sofar = ps.get("tool_calls_so_far", 0)
        ps["actual_remaining_tool_calls"] = tool_call_count - sofar

    record["finished_at"] = time.time()

    # Persist single-task record
    out_path = os.path.join(out_dir, f"{agent_type}__{task['id']}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    print(f"  -> {out_path}  (tool_calls={tool_call_count}, "
          f"per_step preds={len(record['per_step'])})")
    return record


# ---------------------------------------------------------------------------
# CSV aggregation
# ---------------------------------------------------------------------------

def write_csvs(records: List[dict], out_dir: str):
    import csv

    one_shot_path = os.path.join(out_dir, "speculation_oneshot.csv")
    with open(one_shot_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "agent_type", "task_id",
            "pred_total_tool_calls", "actual_total_tool_calls",
            "pred_avg_tool_duration_s", "actual_avg_tool_duration_s",
        ])
        for r in records:
            os_pred = r["one_shot"] or {}
            act = r["actuals"]
            w.writerow([
                r["agent_type"], r["task_id"],
                os_pred.get("predicted_total_tool_calls"),
                act.get("total_tool_calls"),
                os_pred.get("predicted_avg_tool_duration_s"),
                act.get("avg_tool_duration_s"),
            ])

    per_step_path = os.path.join(out_dir, "speculation_perstep.csv")
    with open(per_step_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "agent_type", "task_id", "turn_index", "tool_calls_so_far",
            "pred_remaining_tool_calls", "actual_remaining_tool_calls",
            "pred_next_tool_duration_s", "actual_next_tool_duration_s",
            "actual_tool_name",
        ])
        for r in records:
            for ps in r["per_step"]:
                w.writerow([
                    r["agent_type"], r["task_id"], ps.get("turn_index"),
                    ps.get("tool_calls_so_far"),
                    ps.get("remaining_tool_calls"),
                    ps.get("actual_remaining_tool_calls"),
                    ps.get("next_tool_duration_s"),
                    ps.get("actual_next_tool_duration_s"),
                    ps.get("actual_tool_name"),
                ])

    print(f"\nCSVs written:\n  {one_shot_path}\n  {per_step_path}")
    return one_shot_path, per_step_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    p.add_argument("--max-model-len", type=int, default=8192)
    p.add_argument("--gpu-memory-utilization", type=float, default=0.7)
    p.add_argument("--tensor-parallel-size", type=int, default=1)
    p.add_argument("--agent-types", nargs="+", default=AGENT_TYPES)
    p.add_argument("--tasks-per-agent", type=int, default=TASKS_PER_AGENT)
    p.add_argument("--calibrated", action="store_true",
                   help="Inject per-agent calibration metadata + worked example into prediction prompts")
    p.add_argument("--out-dir", default=None,
                   help="Override output directory (otherwise timestamped under traces/final/speculation/)")
    args = p.parse_args()

    if args.out_dir is None:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(OUT_ROOT, ts)
    else:
        out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    print(f"[speculation] output -> {out_dir}")

    print(f"[speculation] loading vLLM engine: {args.model}")
    engine = InstrumentedEngine(
        model=args.model,
        dtype="bfloat16",
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        tensor_parallel_size=args.tensor_parallel_size,
        enable_prefix_caching=False,
    )
    print(f"[speculation] engine ready\n")

    all_records: List[dict] = []
    for agent_type in args.agent_types:
        tasks = load_agent_tasks(agent_type, args.tasks_per_agent)
        print(f"\n=== {agent_type}  ({len(tasks)} tasks) ===")
        for t in tasks:
            print(f"-> {agent_type} :: {t['id']}")
            try:
                rec = run_task_with_speculation(engine, t, out_dir,
                                                calibrated=args.calibrated)
                all_records.append(rec)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"  !! task failed: {e}")

    write_csvs(all_records, out_dir)

    # Save manifest
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump({
            "model": args.model,
            "agent_types": args.agent_types,
            "tasks_per_agent": args.tasks_per_agent,
            "calibrated": args.calibrated,
            "n_records": len(all_records),
        }, f, indent=2)

    print(f"\n[speculation] done. {len(all_records)} task records in {out_dir}")
    print(f"To plot: python plot_speculation.py --dir {out_dir}")


if __name__ == "__main__":
    main()
