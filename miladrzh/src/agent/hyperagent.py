"""
Hyperagent worker. Each worker owns up to 2 partner tasks (A, B) at a time.

State machine, per partner, viewed by the worker scheduler:
  ready -> infer (engine) -> [tool calls outstanding] -> tool (cpu/io)
        -> [more tools] -> ... -> next infer -> ... -> finished

Concurrency policy inside one worker:
  - At most one partner is "infer" at a time, by construction: we only start
    partner X's next inference at moments when the other partner Y is already
    in "tool" or "finished". Race: when Y returns from tool while X is still
    decoding, we DO start Y's next inference immediately. With
    max_num_seqs == n_workers, vLLM is forced to preempt X and swap its KV
    to CPU (swap_space). This is the explicit "preempt on tool finish" path.

Driver: run_hyperagent(args, tasks, engine) spawns args.workers workers.
Each worker pulls 2 partners from a shared asyncio.Queue, runs them, and
refills as partners finish.
"""

import asyncio
import json
import os
import time

from agent.tracer import Tracer
from agent.tools import dispatch_tool, clear_namespace
from agent.loop import (
    SYSTEM_PROMPTS, TOOL_SCHEMAS, MAX_TOOL_CALLS_PER_TASK,
    resolve_max_turns, _classify_terminal_outcome,
)
from agent.vllm_engine import AgentAwareBlockManager


def _now_rel(t0):
    return round((time.time() - t0) * 1000.0, 1)


class _Partner:
    """Mutable per-task state inside a hyperagent worker."""

    def __init__(self, task, model, max_turns_override, t_batch_start, prefix_caching):
        self.task        = task
        self.model       = model
        self.budget      = resolve_max_turns(task, cli_override=max_turns_override)
        self.agent_type  = task["agent_type"]
        self.tracer      = Tracer(task, model, prefix_caching=prefix_caching)
        self.messages    = [
            {"role": "system", "content": SYSTEM_PROMPTS[self.agent_type]},
            {"role": "user",   "content": task["prompt"]},
        ]
        self.tools           = TOOL_SCHEMAS[self.agent_type]
        self.pending_prefill = list(self.messages)
        self.tool_call_count = 0
        self.turns_used      = 0
        self.done            = False

        # Latency clock starts at batch start, not when the worker pulls the
        # task. This makes queue-wait visible: tasks at the back of the
        # queue carry their wait into total_ms.
        self.t_submit  = t_batch_start
        self.t_acquire = None
        self.t_end     = None

        # Tool calls extracted from latest assistant turn, dispatched serially.
        self.pending_tool_calls = []
        self.current_tool_call  = None
        self.tool_t0            = None
        self.tool_snap_before   = None

        # Saved across phases.
        self.snap_pre_inf = None
        self.t_request    = None

        # In-flight asyncio tasks for this partner.
        self.infer_task = None
        self.tool_task  = None

        # Coarse state label for the timeline.
        self.state = "ready"

        # Priority for the partner's NEXT inference request. Bumped each time
        # this partner finishes a tool call so the post-tool inference can
        # preempt the sibling that is currently running on the engine.
        # vLLM's priority scheduler swaps the lower-priority sequence's KV
        # to CPU and resumes it later when the slot frees.
        self.next_priority = 0


async def _start_inference(p, engine):
    p.snap_pre_inf = engine.get_kv_snapshot()
    p.t_request    = time.time()
    if p.t_acquire is None:
        p.t_acquire = p.t_request
    tools_for_turn = [] if p.tool_call_count >= MAX_TOOL_CALLS_PER_TASK else p.tools
    # priority kwarg disabled: vLLM 0.7.3 priority scheduler crashes under
    # concurrency. The hyperagent gain is cooperative overlap, not priority.
    p.infer_task = asyncio.ensure_future(
        engine.generate_turn(p.messages, tools_for_turn)
    )
    p.state = "infer"


def _process_inference(p, engine, infer_result):
    """Returns 'finished' or 'has_tool_calls'."""
    raw_text, tool_calls, finish_reason, usage, metrics = infer_result
    t_received = time.time()
    snap_post  = engine.get_kv_snapshot()

    prompt_tokens     = usage.prompt_tokens     if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    t_first_token = None
    t_first_scheduled = None
    if metrics is not None:
        ftt = getattr(metrics, "first_token_time", None)
        fst = getattr(metrics, "first_scheduled_time", None)
        if ftt is not None:
            t_first_token = float(ftt)
        if fst is not None:
            t_first_scheduled = float(fst)
    if t_first_token is None:
        t_first_token = p.t_request

    prefill_t_start = t_first_scheduled if t_first_scheduled is not None else p.t_request
    engine_queue_ms = ((t_first_scheduled - p.t_request) * 1000.0
                       if t_first_scheduled is not None else None)
    p.tracer.record_prefill(
        t_start=prefill_t_start,
        t_end=t_first_token,
        new_messages=p.pending_prefill,
        prompt_tokens=prompt_tokens,
        kv_snap_before=p.snap_pre_inf,
        kv_snap_after=snap_post,
        engine_queue_ms=engine_queue_ms,
    )
    p.pending_prefill = []

    assistant_msg = {"role": "assistant", "content": raw_text or ""}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"],
                          "arguments": json.dumps(tc["arguments"])}}
            for tc in tool_calls
        ]

    p.tracer.record_decode(
        t_start=t_first_token,
        t_end=t_received,
        message=assistant_msg,
        completion_tokens=completion_tokens,
        kv_snap_before=snap_post,
        kv_snap_after=snap_post,
    )
    p.messages.append(assistant_msg)
    p.turns_used += 1

    if finish_reason == "length":
        p.tracer.finish("timeout", "context_length_exceeded")
        return "finished"
    if not tool_calls:
        p.tracer.finish(_classify_terminal_outcome(raw_text), raw_text)
        return "finished"
    if p.turns_used >= p.budget:
        p.tracer.finish("timeout", "")
        return "finished"

    p.pending_tool_calls = list(tool_calls)
    return "has_tool_calls"


async def _start_tool(p, engine):
    tc = p.pending_tool_calls[0]
    p.current_tool_call   = tc
    AgentAwareBlockManager.set_predicted_idle(tc["id"], 0.0)
    p.tool_snap_before    = engine.get_kv_snapshot()
    p.tool_t0             = time.time()
    p.tool_task = asyncio.ensure_future(
        asyncio.to_thread(dispatch_tool, tc["name"], tc["arguments"], p.task)
    )
    p.state = "tool"


def _finish_tool(p, engine, result):
    tc = p.current_tool_call
    t1 = time.time()
    snap_after = engine.get_kv_snapshot()
    AgentAwareBlockManager.clear_prediction(tc["id"])
    p.tool_call_count += 1
    p.tracer.record_tool_call(
        t_start=p.tool_t0, t_end=t1,
        tool_name=tc["name"], tool_args=tc["arguments"],
        tool_result=result,  tool_call_id=tc["id"],
        kv_snap_before=p.tool_snap_before, kv_snap_after=snap_after,
    )
    tool_msg = {"role": "tool", "tool_call_id": tc["id"], "content": result}
    p.messages.append(tool_msg)
    p.pending_prefill.append(tool_msg)
    p.pending_tool_calls.pop(0)
    p.current_tool_call = None
    p.tool_t0 = None
    p.tool_snap_before = None
    p.tool_task = None


def _finalize(p, args, summaries, t_batch_start):
    p.t_end = time.time()
    p.done  = True
    trace = p.tracer.to_dict()
    out_path = os.path.join(args.output_dir, f"{trace['trace_id']}.json")
    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2)
    summaries.append({
        "task_id":        p.task["id"],
        "outcome":        trace["outcome"],
        "trace_path":     os.path.relpath(out_path, args.output_dir),
        "submit_rel_ms":  round((p.t_submit  - t_batch_start) * 1000.0, 1),
        "acquire_rel_ms": round((p.t_acquire - t_batch_start) * 1000.0, 1)
                          if p.t_acquire else None,
        "end_rel_ms":     round((p.t_end     - t_batch_start) * 1000.0, 1),
        "queue_ms":       round((p.t_acquire - p.t_submit) * 1000.0, 1)
                          if p.t_acquire else None,
        "service_ms":     round((p.t_end     - p.t_acquire) * 1000.0, 1)
                          if p.t_acquire else None,
        "total_ms":       round((p.t_end     - p.t_submit) * 1000.0, 1),
        "turns":          p.turns_used,
        "tool_calls":     p.tool_call_count,
    })
    clear_namespace(p.task["id"])


async def hyperagent_worker(worker_id, queue, engine, args, summaries,
                            t_batch_start, switch_log, prio_state):
    """Owns up to 2 partners. Each loop iteration: collect in-flight
    futures (one per partner per phase), wait for first completion,
    advance that partner, ensure the other has work running.

    prio_state is a shared dict {"counter": int} so priorities across all
    workers are monotonically increasing. Each tool-finish bumps the
    counter and assigns it to the partner's next inference, guaranteeing
    that the most recently tool-finished partner can preempt anyone
    currently running at a lower priority (vLLM SWAPs to CPU)."""
    slots = [None, None]

    def _log(p, ev):
        switch_log.append({
            "t_rel_ms": _now_rel(t_batch_start),
            "worker":   worker_id,
            "task_id":  p.task["id"] if p else None,
            "event":    ev,
            "state_a":  slots[0].state if slots[0] else "-",
            "state_b":  slots[1].state if slots[1] else "-",
        })

    def _refill():
        for i in (0, 1):
            if slots[i] is None and not queue.empty():
                t = queue.get_nowait()
                slots[i] = _Partner(
                    t, args.model, args.max_turns, t_batch_start,
                    prefix_caching=getattr(engine, "prefix_caching_enabled", None),
                )
                _log(slots[i], "pulled")

    async def _kick(s):
        """Start whichever phase is appropriate for an idle partner."""
        if s is None or s.done: return
        if s.infer_task is not None or s.tool_task is not None: return
        if s.pending_tool_calls:
            await _start_tool(s, engine);     _log(s, "tool_start")
        else:
            await _start_inference(s, engine); _log(s, "infer_start")

    _refill()
    if all(s is None for s in slots):
        return
    for s in slots:
        await _kick(s)

    while True:
        waiting = []
        owners  = {}
        for s in slots:
            if s is None: continue
            if s.infer_task is not None:
                waiting.append(s.infer_task); owners[s.infer_task] = (s, "infer")
            if s.tool_task is not None:
                waiting.append(s.tool_task);  owners[s.tool_task]  = (s, "tool")

        if not waiting:
            # All partners done. Refill from queue; exit if empty.
            for i in (0, 1):
                if slots[i] is not None and slots[i].done:
                    slots[i] = None
            _refill()
            if all(s is None for s in slots):
                return
            for s in slots:
                await _kick(s)
            continue

        done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

        for fut in done:
            p, kind = owners[fut]
            if kind == "infer":
                p.infer_task = None
                try:
                    result = fut.result()
                except Exception as e:
                    p.tracer.finish("error", str(e))
                    _finalize(p, args, summaries, t_batch_start)
                    _log(p, "error")
                    continue
                status = _process_inference(p, engine, result)
                _log(p, f"infer_done:{status}")
                if status == "finished":
                    _finalize(p, args, summaries, t_batch_start)
                    _log(p, "finished")
                else:
                    # Start tool dispatch for this partner; engine becomes free.
                    await _start_tool(p, engine); _log(p, "tool_start")
                    # Other partner: if idle, give it the engine.
                    other = slots[0] if slots[1] is p else slots[1]
                    await _kick(other)
            else:  # "tool"
                p.tool_task = None
                try:
                    result = fut.result()
                except Exception as e:
                    result = f"Tool error: {e}"
                _finish_tool(p, engine, result)
                # Bump priority so the next inference for this partner
                # preempts whichever sibling is currently running.
                prio_state["counter"] += 1
                p.next_priority = prio_state["counter"]
                _log(p, f"tool_done prio={p.next_priority}")
                if p.turns_used >= p.budget:
                    p.tracer.finish("timeout", "")
                    _finalize(p, args, summaries, t_batch_start)
                    _log(p, "finished")
                elif p.pending_tool_calls:
                    await _start_tool(p, engine); _log(p, "tool_start")
                else:
                    # Re-enter inference. If the other partner is currently
                    # decoding, vLLM will preempt it to CPU swap (because
                    # max_num_seqs == n_workers).
                    await _start_inference(p, engine); _log(p, "infer_start")

        # Recycle finished partners, refill, kick newcomers.
        for i in (0, 1):
            if slots[i] is not None and slots[i].done:
                slots[i] = None
        _refill()
        for s in slots:
            await _kick(s)


async def run_hyperagent(args, tasks, engine):
    """Driver. Returns a dict with summaries, switch_log, and timing."""
    queue = asyncio.Queue()
    for t in tasks:
        queue.put_nowait(t)

    summaries  = []
    switch_log = []
    prio_state = {"counter": 0}
    t_batch_start = time.time()
    workers = [
        asyncio.create_task(
            hyperagent_worker(wid, queue, engine, args, summaries,
                              t_batch_start, switch_log, prio_state)
        )
        for wid in range(args.workers)
    ]
    await asyncio.gather(*workers)
    t_batch_end = time.time()
    return {
        "summaries":     summaries,
        "switch_log":    switch_log,
        "t_batch_start": t_batch_start,
        "t_batch_end":   t_batch_end,
    }
