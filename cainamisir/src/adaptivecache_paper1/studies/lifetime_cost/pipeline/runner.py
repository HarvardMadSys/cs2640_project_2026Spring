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

from .benchmarks.base import Task
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

    messages = list(task.messages_init)
    tools_schema = task.tool_env.schemas() or None

    final_answer: Optional[str] = None
    done = False
    step_idx = 0

    while not done and step_idx < task.max_steps:
        t0 = time.perf_counter()
        resp = model.chat(
            messages,
            tools=tools_schema,
            max_tokens=max_completion_tokens,
        )
        wall_ms = int((time.perf_counter() - t0) * 1000)

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
                obs = task.tool_env.call(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": obs[:80_000],   # safety cap on tool output (was 20K — too tight when files don't fit and agent loops re-reading)
                })
                # If submit was called, record the answer
                if name in ("submit", "respond"):
                    final_answer = args.get("answer") or args.get("content") or final_answer
                # tau-bench signals end-of-conversation via "###STOP###"
                if "###STOP###" in (obs or ""):
                    done = True
        elif resp.content and "respond" in task.tool_env:
            # Qwen3-style models emit plain text instead of wrapping a user-facing
            # reply in the `respond` tool. When `respond` is available (τ-bench),
            # treat the plain text as the message to the user, step the simulator,
            # and append its reply as a normal user turn so the dialogue continues
            # in the form Qwen3's chat template expects.
            obs = task.tool_env.call("respond", {"content": resp.content})
            final_answer = resp.content
            if "###STOP###" in (obs or ""):
                done = True
            else:
                messages.append({"role": "user", "content": obs[:80_000]})
        elif resp.content and "submit" in task.tool_env:
            # Coding-agent style (SWE-bench live): no `respond`, but `submit`
            # exists. Plain-text turns happen when the agent reasons aloud
            # ("now I'll edit X"). Don't terminate — append a kick reminder
            # and let the next turn act. Bounded by max_steps so this can't loop
            # forever.
            messages.append({
                "role": "user",
                "content": "Reminder: please call a tool to make progress (read_file / edit_file / search / run_tests), or call submit() if the task is complete.",
            })
        else:
            # No tool call and no respond/submit channel → treat as final answer.
            done = True
            final_answer = resp.content

        # Compaction
        _summarizer_chatmodel = summarizer_model or model
        ctx = CompactionContext(
            step=step_idx,
            budget=budget_tokens,
            hard_budget=hard_budget_tokens,
            tokenizer=tokenizer,
            summarizer_model=_summarizer_chatmodel,
            summarizer=summarizer,
        )
        messages, comp_event = policy.maybe_compact(messages, ctx)

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
    return out
