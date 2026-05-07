"""
Writes per-task JSON traces as a flat list of events.

Schema:
  metadata header (trace_id, agent_type, benchmark, task_id, model, outcome,
                   total_wall_time_ms, start_ts, kv_bytes_per_token_config)
  events[] — each event is one of:
      type="prefill"   : processed one batch of new messages into KV
      type="decode"    : generated assistant response (may contain tool calls)
      type="tool_call" : executed a tool call between decodes

Every event carries the same minimal token/KV block (only fields we are
confident about — runtime / snapshot fields will be added back in a future
session once we have direct mid-request measurement):

  turn_id, type, duration_ms, start_ts, end_ts, rel_start_ms, rel_end_ms

  event_token              — tokens this event contributed:
                               prefill   = input tokens prefilled
                               decode    = tokens generated
                               tool_call = 0 (no GPU work)
  total_token              — running sum of event_token through this event
  kv_cache_computed_event  — event_token × bytes/token from MODEL_KV_GEOMETRY
  kv_cache_computed_total  — total_token × bytes/token from MODEL_KV_GEOMETRY

Plus type-specific fields: messages (prefill), message (decode),
tool_name/tool_subtype/tool_call_id/request/response (tool_call).
"""

import time
from datetime import datetime, timezone

from agent.config import kv_bytes_per_token


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _subtype(tool_name: str, args: dict) -> str:
    if tool_name == "python_exec":
        code = args.get("code", "").lower()
        if any(k in code for k in ["read_parquet", "read_csv", "read_json", "open("]):
            return "data_load"
        if any(k in code for k in [".fit(", "train(", "xgboost", "randomforest",
                                    "gradientboosting", "lgbm"]):
            return "compute"
        if any(k in code for k in ["groupby", "merge", "pivot", "resample", "apply("]):
            return "compute"
        return "inspect"
    if tool_name == "sql_exec":
        q = args.get("query", "").upper()
        if any(k in q for k in ["GROUP BY", "JOIN", "HAVING", "WINDOW"]):
            return "aggregate"
        return "query"
    if tool_name == "web_search":
        return "search"
    if tool_name == "fetch_url":
        return "fetch"
    if tool_name == "bash_exec":
        cmd = args.get("command", "").lower()
        if "pytest" in cmd or "python -m pytest" in cmd:
            return "test_run"
        if "pip install" in cmd:
            return "install"
        return "shell"
    if tool_name == "view_file":
        return "read"
    if tool_name == "edit_file":
        return "write"
    if tool_name == "search_dir":
        return "search"
    return "other"


class Tracer:
    def __init__(self, task: dict, model: str, prefix_caching=None):
        self.task             = task
        self.model            = model
        # True/False for local engine; None when unknown (e.g. HTTP mode where
        # the server's caching config is not introspected).
        self.prefix_caching   = prefix_caching
        # Raises KeyError if model not in MODEL_KV_GEOMETRY — intentional.
        self.kv_bytes_per_tok = kv_bytes_per_token(model)
        self.start_time       = time.time()
        self.events: list     = []
        self._next_turn_id    = 0
        self._total_token     = 0
        self.outcome          = "failure"
        self.final_answer     = ""

    def _base_event(self, type_, t_start, t_end):
        tid = self._next_turn_id
        self._next_turn_id += 1
        return {
            "turn_id":      tid,
            "type":         type_,
            "duration_ms":  int((t_end - t_start) * 1000),
            "start_ts":     _iso(t_start),
            "end_ts":       _iso(t_end),
            "rel_start_ms": int((t_start - self.start_time) * 1000),
            "rel_end_ms":   int((t_end   - self.start_time) * 1000),
        }

    def _record(self, type_, t_start, t_end, event_token, **extras):
        et = int(event_token)
        self._total_token += et
        bpt = self.kv_bytes_per_tok

        ev = self._base_event(type_, t_start, t_end)
        ev["event_token"]             = et
        ev["total_token"]             = self._total_token
        ev["kv_cache_computed_event"] = et * bpt
        ev["kv_cache_computed_total"] = self._total_token * bpt
        ev.update(extras)
        self.events.append(ev)
        return ev

    def record_prefill(self, t_start, t_end, new_messages, prompt_tokens,
                       kv_snap_before=None, kv_snap_after=None,
                       engine_queue_ms=None):
        # t_start should be vLLM's first_scheduled_time when available, so
        # duration_ms == real prefill compute time (not including time the
        # request spent waiting in the engine's FIFO queue). engine_queue_ms
        # captures that wait separately: from request submission (t_request)
        # to first_scheduled_time. None means we don't have the metric.
        extras = {"messages": new_messages}
        if engine_queue_ms is not None:
            extras["engine_queue_ms"] = float(engine_queue_ms)
        return self._record(
            "prefill", t_start, t_end, prompt_tokens, **extras,
        )

    def record_decode(self, t_start, t_end, message, completion_tokens,
                      kv_snap_before=None, kv_snap_after=None):
        return self._record(
            "decode", t_start, t_end, completion_tokens,
            message=message,
        )

    def record_tool_call(self, t_start, t_end, tool_name, tool_args,
                         tool_result, tool_call_id,
                         kv_snap_before=None, kv_snap_after=None):
        return self._record(
            "tool_call", t_start, t_end, 0,
            tool_name=tool_name,
            tool_subtype=_subtype(tool_name, tool_args or {}),
            tool_call_id=tool_call_id,
            request={"args": tool_args},
            response={"content": tool_result},
        )

    def finish(self, outcome: str, final_answer: str):
        self.outcome      = outcome
        self.final_answer = final_answer

    def to_dict(self) -> dict:
        ts_iso = time.strftime("%dT%H%M%S", time.gmtime(self.start_time))
        task   = self.task
        cache_tag = "no_cache_" if self.prefix_caching is False else ""
        return {
            "trace_id":                 f"{cache_tag}{ts_iso}_{task['agent_type']}_{task['benchmark']}_{task['id']}",
            "agent_type":               task["agent_type"],
            "benchmark":                task["benchmark"],
            "task_id":                  task["id"],
            "model":                    self.model,
            "prefix_caching":           self.prefix_caching,
            "kv_bytes_per_token_config": self.kv_bytes_per_tok,
            "outcome":                  self.outcome,
            "correctness_grader":       None,
            "final_answer":             self.final_answer,
            "start_ts":                 _iso(self.start_time),
            "total_wall_time_ms":       int((time.time() - self.start_time) * 1000),
            "events":                   self.events,
        }
