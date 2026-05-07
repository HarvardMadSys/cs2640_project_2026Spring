"""Agent loop runner with compaction hooks.

The minimal ReAct-style loop:

    while not done and step < max_steps:
        response = model.chat(messages, tools=...)
        messages.append(response.as_assistant_msg())
        if response.tool_calls:
            for tc in response.tool_calls:
                obs = task.tool_env.call(tc.name, tc.args)
                messages.append(tool_msg(obs))
        else:
            done = True
            final = response.content
        messages, evt = policy.maybe_compact(messages, ctx)
        log_step(...)

Per-step usage is captured directly from the model's response. Compaction
events are tagged onto the step they fire after.
"""

from __future__ import annotations

import json
import time
from typing import Callable, List, Optional

from .benchmarks.base import Task, ToolEnv
from .models.base import ChatModel
from .policies.base import CompactionContext, CompactionPolicy
from .tokenization import Tokenizer, count_messages, get_tokenizer
from .types import CompactionEvent, Message, Step, Trajectory, Usage


def _build_summarizer(model: ChatModel, max_summary_tokens: int = 400) -> Callable:
    """Returns a callable suitable for CompactionContext.summarizer.

    Returns (summary_text, in_cached, in_uncached, out_tokens). The runner
    sends the summarize request as `[same agent prefix][user: 'now write a
    summary']` so the messages-to-summarize hit the provider's prefix cache
    that the just-completed agent step warmed.
    """

    INSTRUCTION = (
        "Now produce a concise summary of the conversation above, "
        "preserving the agent's goal, tools used, key results, and any "
        f"decisions made. Output the summary text only, under {max_summary_tokens} tokens."
    )

    def summarize(messages_to_compact: List[dict]) -> tuple[str, int, int, int]:
        msgs = list(messages_to_compact) + [{"role": "user", "content": INSTRUCTION}]
        resp = model.chat(msgs, max_tokens=max_summary_tokens)
        in_cached = resp.usage.cached_tokens if resp.usage else 0
        in_uncached = ((resp.usage.prompt_tokens - in_cached)
                       if resp.usage else 0)
        out = resp.usage.completion_tokens if resp.usage else 0
        return resp.content.strip(), in_cached, in_uncached, out

    return summarize


def _coerce_args(args) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {"_raw": args}
    return {}


def run_task(
    task: Task,
    model: ChatModel,
    policy: CompactionPolicy,
    *,
    benchmark_name: str,
    budget_tokens: int = 32_000,
    hard_budget_tokens: int = 64_000,
    summarizer_model: Optional[ChatModel] = None,
    tokenizer: Optional[Tokenizer] = None,
    max_completion_tokens: int = 2048,
) -> Trajectory:
    """Run one task end-to-end. Returns a populated Trajectory."""
    tokenizer = tokenizer or get_tokenizer("tiktoken:cl100k_base")
    summarizer = _build_summarizer(summarizer_model or model)

    traj = Trajectory(
        task_id=task.id,
        benchmark=benchmark_name,
        model=model.model_name,
        policy=policy.name,
    )

    # Deep-copy task.messages_init: the policy may mutate message dicts
    # (memento, prior_memento, recalled_step) and we must not let those
    # mutations leak back to the shared task object — otherwise multi-variant
    # bakes (validate_recall.py) see corrupted starting state across runs.
    import copy
    messages = copy.deepcopy(task.messages_init)

    # Phase 5: let the policy contribute extra tools (e.g. a `recall(mem_id)`
    # tool that depends on this run's model adapter) and an addendum to the
    # system prompt explaining how to use them. Build a scoped tool_env that
    # wraps the task's tools + extras so we don't mutate task.tool_env (the
    # same Task object is reused across variants in validate_recall).
    extra_tools: List = []
    get_extra_tool = getattr(policy, "get_recall_tool", None)
    if callable(get_extra_tool):
        queue_recall = getattr(model, "queue_recall", None)
        if callable(queue_recall):
            extra = get_extra_tool(queue_recall)
            if extra is not None:
                extra_tools.append(extra)
    if extra_tools:
        base_tools = list(task.tool_env._tools.values())
        tool_env = ToolEnv(base_tools + extra_tools)
    else:
        tool_env = task.tool_env

    get_addendum = getattr(policy, "get_system_prompt_addendum", None)
    if callable(get_addendum):
        addendum = get_addendum()
        if addendum:
            for m in messages:
                if m.get("role") == "system":
                    m["content"] = (m.get("content") or "") + "\n\n" + addendum
                    break
            else:
                messages.insert(0, {"role": "system", "content": addendum})

    tools_schema = tool_env.schemas() or None

    # Phase 9: give the policy a content-hash function that uses the model's
    # actual tokenizer (Qwen3 in our case), not the budget-counting tokenizer
    # (often tiktoken cl100k_base). Otherwise policy.maybe_recall computes
    # wrong obs_ids and the captured-obs gate never lets recall through.
    obs_id_for_text = getattr(model, "compute_obs_id_for_text", None)
    if callable(obs_id_for_text):
        setattr(policy, "_obs_id_for_text", obs_id_for_text)

    final_answer: Optional[str] = None
    done = False
    step_idx = 0

    while not done and step_idx < task.max_steps:
        _step_t0 = time.perf_counter()
        print(f"[runner] step={step_idx} START", flush=True)
        # Recall hook fires BEFORE chat — gives the policy a chance to swap
        # an inlined memento back to its full obs so the model sees the
        # bytes it needs this turn.
        recall_ctx = CompactionContext(
            step=step_idx,
            budget=budget_tokens,
            hard_budget=hard_budget_tokens,
            tokenizer=tokenizer,
            summarizer=summarizer,
        )
        _recall_t0 = time.perf_counter()
        messages, recall_event = policy.maybe_recall(messages, recall_ctx)
        print(f"[runner] step={step_idx} maybe_recall done "
              f"in {(time.perf_counter()-_recall_t0)*1000:.0f}ms "
              f"event={'yes' if recall_event else 'no'}",
              flush=True)

        # Phase 4e: drain attmask recall hints staged by the policy and
        # push them into the engine via the model adapter. Each entry is
        # an obs_text whose obs_id we want skipped on the next compaction
        # (so the masked KV is read by attention).
        pending = getattr(policy, "_pending_attmask_recalls", None)
        if pending:
            queue_recall = getattr(model, "queue_recall", None)
            if callable(queue_recall):
                while pending:
                    obs_text = pending.pop(0)
                    try:
                        queue_recall(obs_text)
                    except Exception:
                        pass

        # Phase 3c + 9: drain kvrestore recall hints. Each entry's obs gets
        # restored from CPU pinned memory into fresh GPU blocks and spliced
        # into the upcoming request's block_table at the obs's original
        # logical position; the suffix's cached K is then rotated by
        # delta_tokens to correct RoPE phase. No re-prefill anywhere.
        # Each entry is either an (obs_text, delta_tokens) tuple (post-
        # Phase 9) or a bare obs_text (legacy; treated as delta=0).
        pending_kvr = getattr(policy, "_pending_kvrestore_recalls", None)
        if pending_kvr:
            queue_kv_restore = getattr(model, "queue_kv_restore", None)
            if callable(queue_kv_restore):
                while pending_kvr:
                    entry = pending_kvr.pop(0)
                    if isinstance(entry, tuple):
                        obs_text, delta = entry
                    else:
                        obs_text, delta = entry, 0
                    try:
                        queue_kv_restore(obs_text, delta)
                    except TypeError:
                        # legacy adapter signature without delta
                        try:
                            queue_kv_restore(obs_text)
                        except Exception:
                            pass
                    except Exception:
                        pass

        t0 = time.perf_counter()
        print(f"[runner] step={step_idx} chat() ENTER "
              f"n_messages={len(messages)}",
              flush=True)
        resp = model.chat(
            messages,
            tools=tools_schema,
            max_tokens=max_completion_tokens,
        )
        wall_ms = int((time.perf_counter() - t0) * 1000)
        print(f"[runner] step={step_idx} chat() EXIT in {wall_ms}ms "
              f"n_tool_calls={len(resp.tool_calls or [])}",
              flush=True)

        # Phase 7: any tool message that was freshly mementoed BEFORE this
        # chat just had its full obs sent through the engine, which (under
        # attention_mask_mode) captured + pinned its KV blocks. Flip the
        # flag so the next chat renders this msg as memento-only — the
        # carry cost is gone, the pinned KV survives in the block pool.
        for m in messages:
            if m.get("_freshly_mementoed"):
                m["_freshly_mementoed"] = False
                m["_obs_dropped"] = True

        # Record assistant message
        asst_msg_dict = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            asst_msg_dict["tool_calls"] = resp.tool_calls
        messages_in_snapshot = [dict(m) for m in messages]
        messages.append(asst_msg_dict)

        # Execute tool calls
        if resp.tool_calls:
            for tc in resp.tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                args = _coerce_args(fn.get("arguments"))
                obs = tool_env.call(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": obs[:20_000],   # safety cap on tool output
                })
                # If submit was called, record the answer
                if name in ("submit", "respond"):
                    final_answer = args.get("answer") or args.get("content") or final_answer
        else:
            # No tool call → treat as final answer
            done = True
            final_answer = resp.content

        # Compaction
        ctx = CompactionContext(
            step=step_idx,
            budget=budget_tokens,
            hard_budget=hard_budget_tokens,
            tokenizer=tokenizer,
            summarizer=summarizer,
        )
        _comp_t0 = time.perf_counter()
        print(f"[runner] step={step_idx} maybe_compact ENTER", flush=True)
        messages, comp_event = policy.maybe_compact(messages, ctx)
        print(f"[runner] step={step_idx} maybe_compact EXIT "
              f"in {(time.perf_counter()-_comp_t0)*1000:.0f}ms "
              f"event={'yes' if comp_event else 'no'}",
              flush=True)
        print(f"[runner] step={step_idx} END "
              f"total={(time.perf_counter()-_step_t0)*1000:.0f}ms",
              flush=True)

        traj.steps.append(Step(
            index=step_idx,
            messages_in=[Message(**_message_kwargs(m)) for m in messages_in_snapshot],
            response=Message(
                role="assistant",
                content=resp.content or "",
                tool_calls=resp.tool_calls,
            ),
            usage=resp.usage or Usage(0, 0, 0),
            wallclock_ms=wall_ms,
            compaction_after=comp_event,
            recall_before=recall_event,
        ))
        step_idx += 1

    traj.final_answer = final_answer if isinstance(final_answer, str) else (
        json.dumps(final_answer) if final_answer is not None else None
    )
    if task.evaluator is not None:
        try:
            traj.resolved = bool(task.evaluator(traj))
        except Exception as e:
            traj.extra["eval_error"] = str(e)
            traj.resolved = False

    return traj


def _message_kwargs(m: dict) -> dict:
    """Trim a raw message dict to the fields Message accepts."""
    out = {"role": m.get("role", "user"), "content": m.get("content") or ""}
    if not isinstance(out["content"], str):
        out["content"] = json.dumps(out["content"])
    if "name" in m:
        out["name"] = m["name"]
    if "tool_calls" in m:
        out["tool_calls"] = m["tool_calls"]
    if "tool_call_id" in m:
        out["tool_call_id"] = m["tool_call_id"]
    if "memento" in m:
        out["memento"] = m["memento"]
    if "recalled_step" in m:
        out["recalled_step"] = m["recalled_step"]
    return out
