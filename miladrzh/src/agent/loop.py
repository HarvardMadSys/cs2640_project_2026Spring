"""
ReAct agent loop. Supports two backends:

  HTTP  (default) — calls a running vLLM OpenAI-compatible server.
                    Uses streaming to measure real prefill vs decode split.
                    KV counts are proxies (no block-manager access over HTTP).

  Local           — imports vLLM as a Python library via InstrumentedEngine.
                    KV counts come from the real BlockSpaceManager.
                    Prefill/decode split from RequestOutput.metrics.first_token_time.

One ReAct step emits:
  prefill event  -> decode event  -> [tool_call event]*  -> next prefill ...

Turn budget is chosen adaptively from agent_type + per-task override, with a
hard safety ceiling that run.py also enforces.
"""

# Per-agent default turn budgets. Tuned to typical ReAct depth:
#   rag          : 2-3 searches + 1-2 fetches typical
#   sql          : schema peek + 1-2 refinements
#   data_analysis: load + inspect + transform + model + interpret
#   swe_bench    : read + search + edit + test-retry cycles
DEFAULT_MAX_TURNS = {
    "rag":           8,
    "sql":           12,
    "data_analysis": 12,
    "swe_bench":     20,
}
SAFETY_CEILING_TURNS = 256

# Hard cap on tool calls per task. Once reached, subsequent turns are run
# with an empty tools list, forcing the agent to answer from what it has.
MAX_TOOL_CALLS_PER_TASK = 8


def resolve_max_turns(task, cli_override=None):
    """
    Priority: CLI override > task["max_turns"] > agent-type default.
    Always clamped to SAFETY_CEILING_TURNS.
    """
    if cli_override is not None:
        n = cli_override
    elif "max_turns" in task:
        n = task["max_turns"]
    else:
        n = DEFAULT_MAX_TURNS.get(task["agent_type"], 8)
    return max(1, min(int(n), SAFETY_CEILING_TURNS))

import json
import re
import time
from openai import OpenAI
from agent.tools import dispatch_tool, clear_namespace
from agent.tracer import Tracer


_CODE_FENCE_RE = re.compile(r"```")
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>", re.IGNORECASE)


def _classify_terminal_outcome(content_text: str) -> str:
    """A terminal assistant message is suspicious when it contains either:
      (a) a fenced code block (```sql / ```python / ...) — model "showed" SQL
          or code instead of executing it, or
      (b) a literal <tool_call> tag — model attempted a tool call but the
          parser couldn't extract it (e.g. malformed JSON), so no tool ran.
    In both cases label as incomplete_no_tool_call so traces don't claim
    "success" for unfinished work."""
    if not content_text:
        return "success"
    if _CODE_FENCE_RE.search(content_text):
        return "incomplete_no_tool_call"
    if _TOOL_CALL_TAG_RE.search(content_text):
        return "incomplete_no_tool_call"
    return "success"

SYSTEM_PROMPTS = {
    "data_analysis": """You are a data analysis assistant with access to a Python execution environment.
State persists between calls — variables, imports, and dataframes created in one call are available in the next.

Work step by step:
1. Load and inspect the data first (print shape, dtypes, head)
2. Transform and engineer features as needed
3. Run your analysis or model
4. Tune your model for better accuracy if needed
5. Interpret results clearly with numbers


Use print() to show intermediate results. When you have a complete answer, respond in plain text without calling any tool.""",

    "sql": """You are a data analyst with access to a SQL execution tool backed by DuckDB.

To run SQL, you MUST call the sql_exec tool. SQL inside a ```sql code block is NEVER executed.
Your last message should be either a tool call, or a plain-text answer reporting results from a tool.

ALWAYS retrieve the schema before writing any query. Do not guess column names.

Work step by step:
1. List tables:               sql_exec("SHOW TABLES")
2. For EACH relevant table, inspect its columns:
                              sql_exec("PRAGMA table_info('<table_name>')")
   (or  sql_exec("DESCRIBE <table_name>")  )
3. Optionally peek at a few rows:
                              sql_exec("SELECT * FROM <table_name> LIMIT 3")
4. Only after you have seen the real columns, write the query that answers the question.
5. If a query errors, re-inspect the relevant table's schema before retrying — never guess.
6. Explain your findings clearly in plain text.

Always call the tool via the proper tool-call interface. Do NOT put SQL inside a ```sql markdown block as your final message — that will not execute.

When you have a complete answer, respond in plain text without calling any tool.""",

    "rag": """You are a research assistant with access to web search and URL fetching tools.

Work step by step:
1. Search for relevant information
2. Fetch and read important pages in detail
3. Search again if you need more specific facts
4. Synthesize into a clear, sourced answer

When you have a complete answer, respond in plain text without calling any tool.""",

    "swe_bench": """You are a software engineer fixing a bug in a Python repository.

Work step by step:
1. Run the failing test first to see the exact error message: bash_exec with pytest -xvs <test>
2. Read the relevant source files around the error (view_file)
3. Search for the function or class that needs to change (search_dir)
4. Apply the minimal fix (edit_file) to SOURCE files only
5. Run the failing test again to confirm it passes

Do not run the full test suite — only the specific failing test(s) listed in the task.
When the test passes, respond with a plain-text summary of what you changed and why.

NO CHEATING. The grader reverts any edits you make to test files before scoring,
so trying to fake a pass will not work. Specifically, do NOT do any of the following:
  - Edit the test file itself (weakening assertions, changing test inputs,
    adding @pytest.mark.skip / xfail, deleting the test).
  - Catch and swallow the failing exception in source so the test "passes"
    without addressing the actual behavior.
  - Hard-code the expected return value in the function under test.
  - Monkey-patch or stub out the code path the test exercises.
  - Add an `if <called from test>: return <expected>` shortcut.
You must fix the actual bug in the SOURCE code (under lib/, src/, astropy/,
etc.) so that the existing test, as written, passes.""",
}

TOOL_SCHEMAS = {
    "data_analysis": [
        {
            "type": "function",
            "function": {
                "name": "python_exec",
                "description": "Execute Python code. State (variables, imports, dataframes) persists across calls within the same task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"}
                    },
                    "required": ["code"],
                },
            },
        }
    ],
    "sql": [
        {
            "type": "function",
            "function": {
                "name": "sql_exec",
                "description": "Run a SQL query against DuckDB. Returns results as a table string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "SQL query to execute"}
                    },
                    "required": ["query"],
                },
            },
        }
    ],
    "rag": [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web and return top results with titles, URLs, and snippets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": "Fetch and return the text content of a URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch"}
                    },
                    "required": ["url"],
                },
            },
        },
    ],
    "swe_bench": [
        {
            "type": "function",
            "function": {
                "name": "bash_exec",
                "description": "Run a shell command in the repository workspace. Use for: pytest, grep, find, git diff, pip install. Timeout 120s.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run"}
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "view_file",
                "description": "Read a source file with line numbers. Use start_line and end_line to read a specific range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root"},
                        "start_line": {"type": "integer", "description": "First line to show (default 1)"},
                        "end_line": {"type": "integer", "description": "Last line to show (default: end of file)"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace an exact string in a file. old_string must match exactly including whitespace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root"},
                        "old_string": {"type": "string", "description": "Exact text to replace"},
                        "new_string": {"type": "string", "description": "Replacement text"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_dir",
                "description": "Search for a pattern (grep) across .py files in the repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Search pattern (grep regex)"},
                        "directory": {"type": "string", "description": "Directory to search in (default: repo root)"},
                        "file_pattern": {"type": "string", "description": "File glob to search (default: *.py)"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# Public entry point — dispatches to HTTP or local backend
# ---------------------------------------------------------------------------

def run_task(task, model, engine="http://localhost:8000/v1", max_turns=None):
    """
    engine : str                 -> HTTP mode (vLLM OpenAI-compatible URL)
             InstrumentedEngine  -> Local mode (real block-manager counts)

    max_turns : None means use adaptive resolution
                (task["max_turns"] if set, else agent-type default).
                An int here overrides both.
    """
    from agent.vllm_engine import InstrumentedEngine
    budget = resolve_max_turns(task, cli_override=max_turns)
    if isinstance(engine, InstrumentedEngine):
        return _run_task_local(task, model, engine, budget)
    return _run_task_http(task, model, vllm_url=engine, max_turns=budget)


# ---------------------------------------------------------------------------
# HTTP backend — streaming to get real first-token timestamp
# ---------------------------------------------------------------------------

def _run_task_http(task, model, vllm_url, max_turns):
    agent_type = task["agent_type"]
    client = OpenAI(base_url=vllm_url, api_key="local-token")
    tracer = Tracer(task, model)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[agent_type]},
        {"role": "user",   "content": task["prompt"]},
    ]
    tools = TOOL_SCHEMAS[agent_type]

    # Messages queued to be prefilled on the next request. First turn: the
    # system+user bootstrap. Subsequent turns: the tool result messages.
    pending_prefill = list(messages)

    for _ in range(max_turns):
        t_request = time.time()

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0,
            stream=True,
            stream_options={"include_usage": True},
        )

        t_first_token = None
        content_parts = []
        tool_call_acc = {}  # index -> partial {"id","name","arguments"}
        finish_reason = None
        usage = None

        for chunk in stream:
            if t_first_token is None:
                t_first_token = time.time()
            if chunk.usage is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
            for tc in (delta.tool_calls or []):
                slot = tool_call_acc.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        t_received = time.time()
        if t_first_token is None:
            t_first_token = t_received  # no tokens arrived; collapse prefill window

        content_text = "".join(content_parts)
        prompt_tokens     = usage.prompt_tokens     if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        tracer.record_prefill(
            t_start=t_request,
            t_end=t_first_token,
            new_messages=pending_prefill,
            prompt_tokens=prompt_tokens,
            kv_snap_before=None,
            kv_snap_after=None,
        )
        pending_prefill = []

        assistant_msg = {"role": "assistant", "content": content_text}
        if tool_call_acc:
            assistant_msg["tool_calls"] = [
                {
                    "id":   slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for _, slot in sorted(tool_call_acc.items())
            ]

        tracer.record_decode(
            t_start=t_first_token,
            t_end=t_received,
            message=assistant_msg,
            completion_tokens=completion_tokens,
            kv_snap_before=None,
            kv_snap_after=None,
        )

        messages.append(assistant_msg)

        if finish_reason == "length":
            tracer.finish("timeout", "context_length_exceeded")
            break

        if not tool_call_acc:
            tracer.finish(_classify_terminal_outcome(content_text), content_text)
            break

        for _, slot in sorted(tool_call_acc.items()):
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            t0 = time.time()
            result = dispatch_tool(slot["name"], args, task)
            t1 = time.time()
            tracer.record_tool_call(
                t_start=t0,
                t_end=t1,
                tool_name=slot["name"],
                tool_args=args,
                tool_result=result,
                tool_call_id=slot["id"],
                kv_snap_before=None,
                kv_snap_after=None,
            )
            tool_msg = {"role": "tool", "tool_call_id": slot["id"], "content": result}
            messages.append(tool_msg)
            pending_prefill.append(tool_msg)
    else:
        tracer.finish("timeout", "")

    clear_namespace(task["id"])
    return tracer.to_dict()


# ---------------------------------------------------------------------------
# Local backend (vLLM as a library, real KV snapshots)
# ---------------------------------------------------------------------------

def _run_task_local(task, model, engine, max_turns):
    """
    KV snapshots are taken:
      - before each inference (baseline for prefill.kv_before)
      - right after inference  (prefill.kv_after = decode.kv_before/after)
      - before / after each tool call (idle-window measurement)
    """
    from agent.vllm_engine import AgentAwareBlockManager

    agent_type = task["agent_type"]
    tracer     = Tracer(task, model,
                        prefix_caching=getattr(engine, "prefix_caching_enabled", None))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[agent_type]},
        {"role": "user",   "content": task["prompt"]},
    ]
    tools = TOOL_SCHEMAS[agent_type]
    pending_prefill = list(messages)
    tool_call_count = 0

    for _ in range(max_turns):
        snap_pre_inf = engine.get_kv_snapshot()
        t_request = time.time()

        tools_for_turn = [] if tool_call_count >= MAX_TOOL_CALLS_PER_TASK else tools
        raw_text, tool_calls, finish_reason, usage, t_first_token, t_first_scheduled = \
            _generate_with_timing(engine, messages, tools_for_turn, t_request)

        t_received    = time.time()
        snap_post_inf = engine.get_kv_snapshot()

        prompt_tokens     = usage.prompt_tokens     if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        if t_first_token is None:
            raise RuntimeError(
                "vLLM RequestOutput.metrics.first_token_time not available. "
                "Enable metrics collection or upgrade vLLM so prefill/decode "
                "split can be measured directly."
            )

        prefill_t_start = t_first_scheduled if t_first_scheduled is not None else t_request
        engine_queue_ms = ((t_first_scheduled - t_request) * 1000.0
                           if t_first_scheduled is not None else None)
        tracer.record_prefill(
            t_start=prefill_t_start,
            t_end=t_first_token,
            new_messages=pending_prefill,
            prompt_tokens=prompt_tokens,
            kv_snap_before=snap_pre_inf,
            kv_snap_after=snap_post_inf,
            engine_queue_ms=engine_queue_ms,
        )
        pending_prefill = []

        assistant_msg = {"role": "assistant", "content": raw_text or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id":   tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                }
                for tc in tool_calls
            ]

        tracer.record_decode(
            t_start=t_first_token,
            t_end=t_received,
            message=assistant_msg,
            completion_tokens=completion_tokens,
            kv_snap_before=snap_post_inf,
            kv_snap_after=snap_post_inf,
        )

        messages.append(assistant_msg)

        if finish_reason == "length":
            tracer.finish("timeout", "context_length_exceeded")
            break

        if not tool_calls:
            tracer.finish(_classify_terminal_outcome(raw_text), raw_text)
            break

        for tc in tool_calls:
            AgentAwareBlockManager.set_predicted_idle(tc["id"], 0.0)

            snap_before = engine.get_kv_snapshot()
            t0 = time.time()
            result = dispatch_tool(tc["name"], tc["arguments"], task)
            t1 = time.time()
            snap_after = engine.get_kv_snapshot()

            AgentAwareBlockManager.clear_prediction(tc["id"])
            tool_call_count += 1

            tracer.record_tool_call(
                t_start=t0,
                t_end=t1,
                tool_name=tc["name"],
                tool_args=tc["arguments"],
                tool_result=result,
                tool_call_id=tc["id"],
                kv_snap_before=snap_before,
                kv_snap_after=snap_after,
            )

            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "content": result}
            messages.append(tool_msg)
            pending_prefill.append(tool_msg)
    else:
        tracer.finish("timeout", "")

    clear_namespace(task["id"])
    return tracer.to_dict()


def _generate_with_timing(engine, messages, tools, t_request):
    """
    Call engine.generate_turn and try to recover scheduling timestamps from
    vLLM RequestOutput.metrics. Returns
        (raw, tool_calls, finish, usage, t_first_token, t_first_scheduled).
    Either timestamp may be None if the metric is unavailable.
    """
    raw, tool_calls, finish, usage = engine.generate_turn(messages, tools)
    t_first_token = None
    t_first_scheduled = None
    metrics = getattr(engine, "_last_metrics", None)
    if metrics is not None:
        ftt = getattr(metrics, "first_token_time", None)
        fst = getattr(metrics, "first_scheduled_time", None)
        if ftt is not None:
            t_first_token = float(ftt)
        if fst is not None:
            t_first_scheduled = float(fst)
    return raw, tool_calls, finish, usage, t_first_token, t_first_scheduled


# ---------------------------------------------------------------------------
# Async agent loop (for concurrent multi-agent batching)
# ---------------------------------------------------------------------------

async def run_task_async(task, model, engine, max_turns=None):
    """
    Async sibling of run_task. Requires `engine` to be an
    AsyncInstrumentedEngine (local async vLLM). Multiple coroutines may run
    this concurrently against the same engine; vLLM continuous-batches them.

    Tool calls are dispatched via asyncio.to_thread so a network-bound tool
    in one agent does not block other agents' inference.
    """
    import asyncio
    from agent.vllm_engine import AgentAwareBlockManager

    budget = resolve_max_turns(task, cli_override=max_turns)
    agent_type = task["agent_type"]
    tracer = Tracer(task, model,
                    prefix_caching=getattr(engine, "prefix_caching_enabled", None))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[agent_type]},
        {"role": "user",   "content": task["prompt"]},
    ]
    tools = TOOL_SCHEMAS[agent_type]
    pending_prefill = list(messages)
    tool_call_count = 0

    for _ in range(budget):
        snap_pre_inf = engine.get_kv_snapshot()
        t_request = time.time()

        tools_for_turn = [] if tool_call_count >= MAX_TOOL_CALLS_PER_TASK else tools
        raw_text, tool_calls, finish_reason, usage, metrics = \
            await engine.generate_turn(messages, tools_for_turn)

        t_received    = time.time()
        snap_post_inf = engine.get_kv_snapshot()

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
            raise RuntimeError(
                "AsyncLLMEngine RequestOutput.metrics.first_token_time "
                "not available; cannot derive prefill/decode split."
            )

        prefill_t_start = t_first_scheduled if t_first_scheduled is not None else t_request
        engine_queue_ms = ((t_first_scheduled - t_request) * 1000.0
                           if t_first_scheduled is not None else None)
        tracer.record_prefill(
            t_start=prefill_t_start,
            t_end=t_first_token,
            new_messages=pending_prefill,
            prompt_tokens=prompt_tokens,
            kv_snap_before=snap_pre_inf,
            kv_snap_after=snap_post_inf,
            engine_queue_ms=engine_queue_ms,
        )
        pending_prefill = []

        assistant_msg = {"role": "assistant", "content": raw_text or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id":   tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                }
                for tc in tool_calls
            ]

        tracer.record_decode(
            t_start=t_first_token,
            t_end=t_received,
            message=assistant_msg,
            completion_tokens=completion_tokens,
            kv_snap_before=snap_post_inf,
            kv_snap_after=snap_post_inf,
        )

        messages.append(assistant_msg)

        if finish_reason == "length":
            tracer.finish("timeout", "context_length_exceeded")
            break

        if not tool_calls:
            tracer.finish(_classify_terminal_outcome(raw_text), raw_text)
            break

        for tc in tool_calls:
            AgentAwareBlockManager.set_predicted_idle(tc["id"], 0.0)

            snap_before = engine.get_kv_snapshot()
            t0 = time.time()
            # Tool dispatch is sync (network/disk/subprocess). Run it in a
            # worker thread so other agents' inference can progress while
            # this one's tool call is waiting.
            result = await asyncio.to_thread(
                dispatch_tool, tc["name"], tc["arguments"], task
            )
            t1 = time.time()
            snap_after = engine.get_kv_snapshot()

            AgentAwareBlockManager.clear_prediction(tc["id"])
            tool_call_count += 1

            tracer.record_tool_call(
                t_start=t0,
                t_end=t1,
                tool_name=tc["name"],
                tool_args=tc["arguments"],
                tool_result=result,
                tool_call_id=tc["id"],
                kv_snap_before=snap_before,
                kv_snap_after=snap_after,
            )

            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "content": result}
            messages.append(tool_msg)
            pending_prefill.append(tool_msg)
    else:
        tracer.finish("timeout", "")

    clear_namespace(task["id"])
    return tracer.to_dict()
