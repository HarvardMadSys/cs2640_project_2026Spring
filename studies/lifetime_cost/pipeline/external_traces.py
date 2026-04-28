"""Adapters for publicly released agent / reasoning trace dumps.

Three sources, two shapes:

1. **Applied Compute / trie workloads** (`agentic_coding_8k.jsonl`,
   `code_qa_8k.jsonl`, `office_work_8k.jsonl`) — *statistics only*. Each
   line gives per-turn token lengths but no message content. We synthesize
   placeholder messages of the right length so cliff and cost math work
   the same way as for real trajectories.

2. **Hermes Agent Reasoning Traces** (HF: lambda/hermes-agent-reasoning-traces)
   — full multi-turn tool-calling trajectories with real message content
   and `<think>` blocks. Direct loader into our Trajectory type.

3. **TRAIL** (HF: PatronusAI/TRAIL) — 148 agent traces from GAIA and SWE-bench
   in OpenTelemetry span format. We extract per-step message content from
   the spans.

All three feed directly into the cliff analyzer (`replay.analyze_trajectory`)
and the policy machinery (we can simulate any policy on a recorded trajectory
shape to see what the cliff and cost *would have been* under that policy —
without spending a token).
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .pricing import PriceSheet, cost_of
from .policies import build_policy
from .policies.base import CompactionContext
from .tokenization import Tokenizer, get_tokenizer
from .types import (
    CompactionEvent,
    LifetimeCost,
    Message,
    Step,
    Trajectory,
    Usage,
)


# ---------------------------------------------------------------------------
# 1. Applied Compute (statistics → synthesized messages)
# ---------------------------------------------------------------------------

def _msg_id(trace_id: str, kind: str, idx: int) -> str:
    """Stable identity for a message: same (trace_id, kind, idx) → same id.
    Used for the message-identity prefix-match model."""
    return f"{trace_id}::{kind}::{idx}"


def _content_fingerprint(content) -> str:
    """Hash of message content for identity comparison when no `_msg_id` is
    set (e.g., messages produced by a compaction policy)."""
    import hashlib
    if isinstance(content, str):
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _identity(m: dict) -> tuple:
    """(role, id) — what defines a message as 'the same' for cache purposes."""
    role = m.get("role", "?")
    if "_msg_id" in m:
        return (role, m["_msg_id"])
    return (role, _content_fingerprint(m.get("content") or ""))


def synthesize_trajectory_from_lengths(
    *,
    task_id: str,
    benchmark: str,
    input_prompt_length: int,
    assistant_response_length: List[int],
    tool_call_output_length: List[int],
    final_assistant_response_length: int,
    tokenizer: Tokenizer = None,    # kept for API compatibility; unused in identity model
    seed: int = 0,
) -> Trajectory:
    """Build a Trajectory whose per-step token shape matches the recorded lengths.

    Identity model: each message carries a stable `_msg_id` (so same id ⇒ same
    bytes from the cache's perspective) and a `_token_count` (the recorded
    length). No text content is synthesized — the policies and the cliff math
    operate purely on (id, position, length).
    """
    n_turns = len(assistant_response_length)
    assert len(tool_call_output_length) == n_turns, "lengths must match"

    def make_msg(role: str, kind: str, idx: int, length: int, **extra) -> dict:
        return {
            "role": role,
            "content": "",                       # placeholder — never rendered
            "_msg_id": _msg_id(task_id, kind, idx),
            "_token_count": int(length),
            **extra,
        }

    messages: List[Dict[str, Any]] = [
        make_msg("user", "initial", 0, input_prompt_length),
    ]

    steps: List[Step] = []
    for i in range(n_turns):
        snap = [_msg_to_serializable(m) for m in messages]

        asst_msg = make_msg("assistant", "asst", i, assistant_response_length[i])
        tool_msg = make_msg("tool", "tool", i, tool_call_output_length[i],
                             tool_call_id=f"tc_{i}")

        prompt_tokens = sum(m["_token_count"] for m in messages)

        steps.append(Step(
            index=i,
            messages_in=[Message(**_msg_kwargs(m)) for m in snap],
            response=Message(role="assistant", content=""),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=assistant_response_length[i],
                cached_tokens=0,           # filled in by simulate_policy
            ),
        ))

        messages.append(asst_msg)
        messages.append(tool_msg)

    snap = [_msg_to_serializable(m) for m in messages]
    prompt_tokens = sum(m["_token_count"] for m in messages)
    steps.append(Step(
        index=n_turns,
        messages_in=[Message(**_msg_kwargs(m)) for m in snap],
        response=Message(role="assistant", content=""),
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=final_assistant_response_length,
            cached_tokens=0,
        ),
    ))

    return Trajectory(
        task_id=task_id,
        benchmark=benchmark,
        model="synthetic",
        policy="recorded",
        steps=steps,
        resolved=True,
        extra={
            "source": "applied_compute_workload",
            "trajectory_messages": messages,    # full reconstructed message list with ids+lengths
        },
    )


def _msg_to_serializable(m: dict) -> dict:
    """Flatten a message dict (preserving _msg_id / _token_count) for snapshot."""
    out = {"role": m["role"], "content": m.get("content", "")}
    for k in ("_msg_id", "_token_count", "tool_call_id", "name"):
        if k in m:
            out[k] = m[k]
    return out


def _msg_kwargs(m: dict) -> dict:
    out = {"role": m.get("role", "user"), "content": m.get("content") or ""}
    if "tool_call_id" in m:
        out["tool_call_id"] = m["tool_call_id"]
    if "tool_calls" in m:
        out["tool_calls"] = m["tool_calls"]
    if "_msg_id" in m:
        out["_msg_id"] = m["_msg_id"]
    if "_token_count" in m:
        out["_token_count"] = m["_token_count"]
    return out


def load_applied_compute_workload(
    path: Path,
    *,
    tokenizer: Tokenizer | None = None,
    benchmark_name: Optional[str] = None,
    max_traces: Optional[int] = None,
) -> List[Trajectory]:
    """Load one Applied Compute JSONL file → list of synthesized Trajectories."""
    tokenizer = tokenizer or get_tokenizer("tiktoken:cl100k_base")
    bench = benchmark_name or path.stem
    out: List[Trajectory] = []
    with open(path) as f:
        for i, line in enumerate(f):
            if max_traces is not None and i >= max_traces:
                break
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            traj = synthesize_trajectory_from_lengths(
                task_id=f"{bench}-{i:05d}",
                benchmark=bench,
                input_prompt_length=d["input_prompt_length"],
                assistant_response_length=d["assistant_response_length"],
                tool_call_output_length=d["tool_call_output_length"],
                final_assistant_response_length=d["final_assistant_response_length"],
                tokenizer=tokenizer,
                seed=i,
            )
            out.append(traj)
    return out


# ---------------------------------------------------------------------------
# 2. Hermes Agent Reasoning Traces (real message content, HF dataset)
# ---------------------------------------------------------------------------

def load_hermes_agent_traces(
    *,
    config: str = "kimi",                      # or "glm-5.1"
    parquet_path: str | None = None,
    max_traces: int = 200,
    tokenizer: Tokenizer | None = None,
    min_turns: int = 4,                        # filter trivial traces
) -> List[Trajectory]:
    """Load `lambda/hermes-agent-reasoning-traces`.

    Reads from a local parquet file (downloaded once) rather than streaming,
    because streaming-load hangs on this dataset's storage. Default path:
    `studies/lifetime_cost/external_traces/hermes/{config}_train.parquet`.

    Schema (verified by inspection):
      id                  : str (uuid)
      conversations       : list of {"from": "system|human|gpt|tool", "value": str}
      tools               : str (JSON array of tool defs)
      category, subcategory, task : str

    Mapping to our types:
      system → system, human → user, gpt → assistant, tool → tool.
      Each `gpt` turn terminates one Step (matches the agent's per-turn
      LLM call boundary).

    `_token_count` for each message is the actual tokenizer count of the
    `value` field — real bytes, no synthesis.
    """
    import pyarrow.parquet as pq
    tokenizer = tokenizer or get_tokenizer("tiktoken:cl100k_base")

    if parquet_path is None:
        from pathlib import Path
        parquet_path = str(
            Path(__file__).resolve().parent.parent
            / "external_traces" / "hermes" / f"{config}_train.parquet"
        )

    role_map = {"system": "system", "human": "user", "gpt": "assistant", "tool": "tool"}

    pf = pq.ParquetFile(parquet_path)
    out: List[Trajectory] = []
    for batch in pf.iter_batches(batch_size=64, columns=["id", "conversations", "category", "subcategory", "task"]):
        table = batch.to_pylist()
        for i, row in enumerate(table):
            if len(out) >= max_traces:
                return out
            convs = row.get("conversations") or []
            if len(convs) < min_turns:
                continue
            messages: List[dict] = []
            steps: List[Step] = []
            step_idx = 0
            for j, turn in enumerate(convs):
                role = role_map.get(turn.get("from"), "user")
                content = turn.get("value") or ""
                tok = tokenizer.count(content)
                msg = {
                    "role": role,
                    "content": content,
                    "_msg_id": f"{row['id']}::{j}",
                    "_token_count": tok,
                }
                if role == "assistant":
                    snap = [Message(**_msg_kwargs(m)) for m in messages]
                    steps.append(Step(
                        index=step_idx,
                        messages_in=snap,
                        response=Message(role="assistant", content=content),
                        usage=Usage(
                            prompt_tokens=sum(m["_token_count"] for m in messages),
                            completion_tokens=tok,
                            cached_tokens=0,
                        ),
                    ))
                    step_idx += 1
                messages.append(msg)
            if not steps:
                continue
            out.append(Trajectory(
                task_id=row["id"],
                benchmark=f"hermes_{config}",
                model="hermes-recorded",
                policy="recorded",
                steps=steps,
                resolved=None,
                extra={
                    "source": f"hermes-agent-reasoning-traces/{config}",
                    "category": row.get("category"),
                    "subcategory": row.get("subcategory"),
                    "task": row.get("task"),
                    "trajectory_messages": messages,
                },
            ))
    return out


# ---------------------------------------------------------------------------
# 3. TRAIL (PatronusAI) — agent traces in OTel span format
# ---------------------------------------------------------------------------

def load_trail_traces(
    *,
    split: str = "test",
    max_traces: int = 148,
) -> List[Trajectory]:
    """Load `PatronusAI/TRAIL` from HF.

    TRAIL stores OpenTelemetry spans per trace. Each `chat.completion`
    span carries the messages sent + the response. We reconstruct per-step
    message lists by walking spans in time order.
    """
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError("Install with `pip install datasets`") from e

    ds = load_dataset("PatronusAI/TRAIL", split=split)

    out: List[Trajectory] = []
    for i, row in enumerate(ds):
        if i >= max_traces:
            break
        # row may store spans as a JSON string or a list — handle both
        spans = row.get("spans") or row.get("trace") or []
        if isinstance(spans, str):
            try:
                spans = json.loads(spans)
            except json.JSONDecodeError:
                continue

        steps: List[Step] = []
        step_idx = 0
        for span in spans:
            if not isinstance(span, dict):
                continue
            attrs = span.get("attributes") or {}
            kind = (span.get("name") or attrs.get("openinference.span.kind") or "").lower()
            if "chat" not in kind and "llm" not in kind:
                continue
            # Extract input messages and output
            in_msgs = attrs.get("llm.input_messages") or attrs.get("input.value") or []
            if isinstance(in_msgs, str):
                try:
                    in_msgs = json.loads(in_msgs)
                except json.JSONDecodeError:
                    in_msgs = []
            out_msg = attrs.get("llm.output_messages") or attrs.get("output.value") or ""
            if isinstance(out_msg, list) and out_msg:
                out_msg = out_msg[0]
            out_content = (
                out_msg.get("message", {}).get("content")
                if isinstance(out_msg, dict) else (out_msg if isinstance(out_msg, str) else "")
            )

            if not in_msgs:
                continue

            steps.append(Step(
                index=step_idx,
                messages_in=[Message(**_msg_kwargs(m)) for m in in_msgs if isinstance(m, dict)],
                response=Message(role="assistant", content=out_content or ""),
                usage=Usage(
                    prompt_tokens=int(attrs.get("llm.token_count.prompt", 0) or 0),
                    completion_tokens=int(attrs.get("llm.token_count.completion", 0) or 0),
                    cached_tokens=0,
                ),
            ))
            step_idx += 1

        if steps:
            out.append(Trajectory(
                task_id=row.get("trace_id") or f"trail-{i:05d}",
                benchmark="trail",
                model=row.get("model") or "trail-recorded",
                policy="recorded",
                steps=steps,
                resolved=None,
                extra={"source": "PatronusAI/TRAIL"},
            ))
    return out


# ---------------------------------------------------------------------------
# Policy simulation: replay a recorded trajectory under a hypothetical policy
# ---------------------------------------------------------------------------

def _prefix_cached_tokens(prev: List[dict], curr: List[dict]) -> int:
    """Identity-model prefix cache: walk prev and curr in lockstep from index 0,
    summing curr[i]._token_count while curr[i] and prev[i] match in (role, id).
    Stop at first mismatch. Returns cached_tokens for `curr`."""
    n = min(len(prev), len(curr))
    total = 0
    for i in range(n):
        if _identity(prev[i]) != _identity(curr[i]):
            return total
        total += int(curr[i].get("_token_count") or 0)
    return total


def _ensure_token_count(m: dict, tokenizer: Tokenizer) -> dict:
    """Attach a _token_count to a message if it doesn't have one (e.g., a
    summary message produced by a policy). Idempotent."""
    if "_token_count" not in m:
        from .tokenization import count_messages
        m = dict(m)
        m["_token_count"] = count_messages([m], tokenizer)
    return m


def simulate_policy(
    traj: Trajectory,
    policy_name: str,
    *,
    budget_tokens: int = 32_000,
    hard_budget_tokens: int = 64_000,
    tokenizer: Tokenizer | None = None,
    summarizer_compression: float = 0.10,
    summarizer_mode: str = "llm",          # "llm" or "free_template"
    policy_kwargs: Dict[str, Any] | None = None,
) -> Trajectory:
    """Re-run the policy machinery against a *recorded* trajectory.

    Since we don't have a live model, we approximate the summarizer with
    a deterministic compressor: a summary of N messages contains
    `summarizer_compression * sum(len)` tokens of placeholder text. This
    approximation is sufficient for cliff and cost estimation — the
    exact summary content doesn't change which bytes are common-prefix
    with subsequent steps.

    Returns a new Trajectory with the same per-step responses and
    observations, but with `messages_in` rewritten as the policy would
    have transformed them, and with `compaction_after` events recorded.
    """
    tokenizer = tokenizer or get_tokenizer("tiktoken:cl100k_base")
    policy = build_policy(policy_name, **(policy_kwargs or {}))

    # The summarizer is called immediately after the agent's just-completed
    # step. The messages-to-summarize were part of the agent's last prompt and
    # are therefore already in the provider's prefix cache. We model this by
    # returning a 3-tuple of (input_cached, input_uncached, output) tokens.
    # `input_uncached` covers a small "now write a concise summary" instruction
    # appended to the cached prefix; `output` is the summary itself.
    SUMMARIZE_INSTRUCTION_TOKENS = 50

    def synth_summarizer(msgs: List[dict]) -> tuple[str, int, int, int]:
        from .tokenization import count_messages
        in_tok = count_messages(msgs, tokenizer)
        out_tok = max(1, int(in_tok * summarizer_compression))
        # Identity model: return empty placeholder text. The summary's true
        # cost lives in the returned (in_cached, in_uncached, out) triple,
        # and the inserted summary message gets a fresh _msg_id so it's
        # distinct from anything that came before.
        if summarizer_mode == "free_template":
            return "", 0, 0, 0
        return "", in_tok, SUMMARIZE_INSTRUCTION_TOKENS, out_tok

    new_steps: List[Step] = []
    if not traj.steps:
        return traj

    # The "appendix" at step i = what was newly added after step i in the
    # original trajectory. We pre-derive it from original messages_in pairs,
    # carrying _msg_id / _token_count through.
    def to_dict(m: Message) -> dict:
        d = _msg_to_dict(m)
        if hasattr(m, "_msg_id") or "_msg_id" in d:
            return d
        return d

    appendix: List[List[dict]] = []
    for i in range(len(traj.steps)):
        if i + 1 < len(traj.steps):
            curr_in = [_msg_to_dict(m) for m in traj.steps[i].messages_in]
            next_in = [_msg_to_dict(m) for m in traj.steps[i + 1].messages_in]
            tail = next_in[len(curr_in):]
            obs_only = [m for m in tail if m.get("role") != "assistant"]
            appendix.append(obs_only)
        else:
            appendix.append([])

    # Recover the base message list (with _msg_id / _token_count if present)
    base = [_msg_to_dict(m) for m in traj.steps[0].messages_in]
    messages: List[dict] = list(base)

    # Track previous step's prompt for identity-based prefix matching
    prev_prompt: List[dict] = []

    for i, step in enumerate(traj.steps):
        # Snapshot current prompt for the Step record
        snap = [dict(m) for m in messages]
        prompt_tok = sum(int(m.get("_token_count") or 0) for m in messages)
        # Fallback if any message is missing a _token_count (e.g., policy-inserted)
        if any("_token_count" not in m for m in messages):
            from .tokenization import count_messages
            prompt_tok = count_messages(messages, tokenizer)

        cached = _prefix_cached_tokens(prev_prompt, messages) if prev_prompt else 0

        new_steps.append(Step(
            index=i,
            messages_in=[Message(**_msg_kwargs(m)) for m in snap],
            response=step.response,
            usage=Usage(
                prompt_tokens=prompt_tok,
                completion_tokens=step.usage.completion_tokens,
                cached_tokens=cached,
            ),
            compaction_after=None,
        ))

        prev_prompt = snap

        # Append assistant response (use the recorded asst_msg from extra/messages
        # if available so we get the correct _msg_id/_token_count; else fall back).
        recorded = traj.extra.get("trajectory_messages") if isinstance(traj.extra, dict) else None
        # The assistant index for step i in the recorded trajectory
        if recorded:
            # Find the assistant message at this turn position
            asst_candidate = next(
                (m for m in recorded
                 if m.get("_msg_id") == _msg_id(traj.task_id, "asst", i)),
                None,
            )
            if asst_candidate is not None:
                messages.append(dict(asst_candidate))
            else:
                # Final assistant message — no kind=="asst" entry; tolerate
                messages.append({
                    "role": "assistant", "content": "",
                    "_msg_id": _msg_id(traj.task_id, "asst_final", i),
                    "_token_count": step.usage.completion_tokens,
                })
        else:
            messages.append({"role": "assistant", "content": step.response.content,
                             **({"tool_calls": step.response.tool_calls} if step.response.tool_calls else {})})

        # Append the pre-derived observation appendix for this step
        for e in appendix[i]:
            messages.append(dict(e))

        # Apply policy. New messages it inserts (e.g., summary) won't have
        # _msg_id / _token_count; we attach _token_count after.
        ctx = CompactionContext(
            step=i,
            budget=budget_tokens,
            hard_budget=hard_budget_tokens,
            tokenizer=tokenizer,
            summarizer=synth_summarizer,
        )
        messages, evt = policy.maybe_compact(messages, ctx)
        # Ensure all surviving messages have a _token_count
        messages = [_ensure_token_count(m, tokenizer) for m in messages]
        if evt is not None:
            new_steps[-1].compaction_after = evt

    return Trajectory(
        task_id=traj.task_id,
        benchmark=traj.benchmark,
        model=traj.model,
        policy=policy_name,
        steps=new_steps,
        resolved=traj.resolved,
        final_answer=traj.final_answer,
        extra={**traj.extra, "simulated_from": traj.policy},
    )


def _msg_to_dict(m: Message) -> dict:
    out = {"role": m.role, "content": m.content}
    if m.tool_calls is not None:
        out["tool_calls"] = m.tool_calls
    if m.tool_call_id is not None:
        out["tool_call_id"] = m.tool_call_id
    if m.name is not None:
        out["name"] = m.name
    if getattr(m, "_msg_id", None) is not None:
        out["_msg_id"] = m._msg_id
    if getattr(m, "_token_count", None) is not None:
        out["_token_count"] = m._token_count
    return out


# ---------------------------------------------------------------------------
# End-to-end: load + simulate all policies + produce summary
# ---------------------------------------------------------------------------

def evaluate_policies_on_traces(
    trajectories: List[Trajectory],
    policy_specs: List[Dict[str, Any]],
    *,
    sheet: PriceSheet,
    cost_models: List[str],
    budget_tokens: int = 32_000,
    hard_budget_tokens: int = 64_000,
    summarizer_mode: str = "llm",
) -> Dict[str, Any]:
    """For each (trajectory, policy) cell, simulate the policy and compute
    lifetime cost under each cost_model. Returns a nested dict suitable
    for plotting and JSON dump."""
    out_rows: List[Dict[str, Any]] = []
    simulated: Dict[str, List[Trajectory]] = {p["name"]: [] for p in policy_specs}
    for traj in trajectories:
        for p in policy_specs:
            sim = simulate_policy(
                traj, p["name"],
                budget_tokens=budget_tokens,
                hard_budget_tokens=hard_budget_tokens,
                summarizer_mode=summarizer_mode,
                policy_kwargs=p.get("kwargs"),
            )
            simulated[p["name"]].append(sim)
            for cm in cost_models:
                lc = cost_of(sim, sheet, override_model=cm)
                out_rows.append({
                    "task_id": traj.task_id,
                    "benchmark": traj.benchmark,
                    "policy": p["name"],
                    "cost_model": cm,
                    "n_steps": len(sim.steps),
                    "n_compactions": sim.num_compactions,
                    "total_prompt_tokens": sim.total_prompt_tokens,
                    "total_cached_tokens": sim.total_cached_tokens,
                    "total_completion_tokens": sim.total_completion_tokens,
                    "lifetime_cost": lc.total,
                    "uncached_dollars": lc.input_uncached_dollars,
                    "cached_dollars": lc.input_cached_dollars,
                    "output_dollars": lc.output_dollars,
                    "compaction_dollars": lc.compaction_dollars,
                })
    return {"rows": out_rows, "simulated": simulated}
