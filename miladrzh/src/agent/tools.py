"""
Tool implementations for all three agent types.
- python_exec: stateful Python execution per task (shared namespace)
- sql_exec: DuckDB query runner
- web_search: Brave Search API (falls back to DuckDuckGo HTML when no key)
- fetch_url: HTTP fetch + HTML strip
"""

import ast
import html as _html
import os
import io
import re
import sys
import time
import threading
import traceback
import contextlib
import subprocess
from urllib.parse import parse_qs, unquote, urlparse

import requests


class _TokenBucket:
    """Thread-safe token bucket. acquire() blocks until a token is available
    or `max_wait` seconds elapse (returns False on timeout)."""

    def __init__(self, rate_per_sec: float, burst: float = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst if burst is not None else rate_per_sec)
        self.tokens = self.capacity
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, max_wait: float = 30.0) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                needed = (1.0 - self.tokens) / self.rate
            if time.monotonic() + needed > deadline:
                return False
            time.sleep(min(needed, max(0.0, deadline - time.monotonic())))


_BRAVE_RPS = float(os.environ.get("BRAVE_RPS", "50"))
_BRAVE_BUCKET = _TokenBucket(rate_per_sec=_BRAVE_RPS, burst=_BRAVE_RPS)


# Per-task Python namespaces. State persists across turns within a task.
_NAMESPACES: dict = {}

_PRELUDE = """
import os, json, math, re, itertools, collections
import numpy as np
import pandas as pd
"""


def _seed_namespace(ns: dict, task: dict):
    """Pre-import common libraries and expose DATA_DIR so the model doesn't need to."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(_PRELUDE, "<prelude>", "exec"), ns)
    except Exception:
        pass
    ns["DATA_DIR"] = task.get("data_dir", os.environ.get("DATA_DIR", ""))


def get_namespace(task_id: str, task: dict = None) -> dict:
    if task_id not in _NAMESPACES:
        ns = {"__builtins__": __builtins__}
        _NAMESPACES[task_id] = ns
        if task is not None:
            _seed_namespace(ns, task)
    return _NAMESPACES[task_id]


def clear_namespace(task_id: str):
    _NAMESPACES.pop(task_id, None)


def python_exec(code: str, task: dict, timeout: int = 300) -> str:
    ns = get_namespace(task["id"], task)
    result: dict = {"out": "", "err": ""}

    def _run():
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        try:
            # Jupyter-style auto-echo: if the last statement is a bare
            # expression, evaluate it separately and print its repr so the
            # model sees the value without having to remember print().
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                try:
                    tree = ast.parse(code, "<agent>", mode="exec")
                except SyntaxError:
                    exec(compile(code, "<agent>", "exec"), ns)
                else:
                    last_expr = None
                    if tree.body and isinstance(tree.body[-1], ast.Expr):
                        last_expr = tree.body.pop()
                    if tree.body:
                        exec(compile(tree, "<agent>", "exec"), ns)
                    if last_expr is not None:
                        expr_mod = ast.Expression(body=last_expr.value)
                        ast.copy_location(expr_mod, last_expr)
                        value = eval(compile(expr_mod, "<agent>", "eval"), ns)
                        if value is not None:
                            print(repr(value))
        except Exception:
            buf_err.write(traceback.format_exc())
        result["out"] = buf_out.getvalue()
        result["err"] = buf_err.getvalue()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        return f"[TIMEOUT after {timeout}s — computation still running]"

    output = result["out"]
    if result["err"]:
        output += f"\n[stderr]\n{result['err']}"
    output = output.strip()
    # Cap output to avoid flooding the context
    if len(output) > 8000:
        output = output[:4000] + "\n... [truncated] ...\n" + output[-2000:]
    return output or "(no output)"


def sql_exec(query: str, task: dict, timeout: int = 120) -> str:
    try:
        import duckdb
    except ImportError:
        return "Error: duckdb not installed. Run: pip install duckdb"

    db_path = task.get("db_path", ":memory:")
    result: dict = {"out": "", "err": ""}

    def _run():
        try:
            conn = duckdb.connect(db_path, read_only=(db_path != ":memory:"))
            df = conn.execute(query).fetchdf()
            conn.close()
            result["out"] = df.to_string(max_rows=50, max_cols=20)
        except Exception as e:
            result["err"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        return f"[TIMEOUT after {timeout}s]"
    if result["err"]:
        return f"SQL Error: {result['err']}"
    return result["out"] or "(empty result)"


def web_search(query: str, task: dict = None, num_results: int = 5) -> str:
    """
    Web search via Brave Search API. Brave is mandatory: if BRAVE_API_KEY
    is missing or any request fails, this hard-exits the process. No
    silent fallback, no error swallowing.
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        sys.stderr.write("FATAL: BRAVE_API_KEY not set; web_search requires Brave.\n")
        sys.exit(2)
    return _search_brave(query, api_key, num_results)


def _search_brave(query: str, api_key: str, num_results: int) -> str:
    if not _BRAVE_BUCKET.acquire(max_wait=30.0):
        return f"Search error: Brave rate limiter timeout (>30s wait at {_BRAVE_RPS} rps cap)"
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": num_results},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        # Return the error to the agent so it can try a different query.
        # Missing API key still panics (handled in web_search before we get here).
        return f"Search error: {e}"
    items = r.json().get("web", {}).get("results", [])
    if not items:
        return "No results found."
    parts = [
        f"Title: {item['title']}\nURL: {item['url']}\nSnippet: {item.get('description', '')}"
        for item in items
    ]
    return "\n---\n".join(parts)


_DDG_LINK_RE    = re.compile(r'class="result__a"\s*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DDG_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE         = re.compile(r"<[^>]+>")


def _search_duckduckgo(query: str, num_results: int) -> str:
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        r.raise_for_status()
        links    = _DDG_LINK_RE.findall(r.text)
        snippets = _DDG_SNIPPET_RE.findall(r.text)
        if not links:
            return "No results found."
        parts = []
        for (href, title), snip in zip(links[:num_results], snippets[:num_results]):
            # DDG wraps the real URL in /l/?uddg=<encoded>
            decoded_href = _html.unescape(href)
            qs  = parse_qs(urlparse(decoded_href).query)
            url = unquote(qs.get("uddg", [decoded_href])[0])
            title_txt   = _html.unescape(_TAG_RE.sub("", title)).strip()
            snippet_txt = _html.unescape(_TAG_RE.sub("", snip)).strip()
            parts.append(f"Title: {title_txt}\nURL: {url}\nSnippet: {snippet_txt}")
        return "\n---\n".join(parts)
    except Exception as e:
        return f"Search error (duckduckgo fallback): {e}"


def fetch_url(url: str, task: dict = None, max_chars: int = 64000) -> str:
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "research-agent/1.0"},
        )
        r.raise_for_status()
        text = _strip_html(r.text)
        return text[:max_chars] if len(text) > max_chars else text
    except Exception as e:
        return f"Fetch error ({url}): {e}"


def _strip_html(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&[a-z]+;", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def bash_exec(command: str, task: dict, timeout: int = 120) -> str:
    workspace = task.get("workspace_dir", "/tmp")
    env = os.environ.copy()
    venv_bin = task.get("venv_bin")
    if venv_bin:
        # Prepend the per-repo conda env so `python`, `pytest`, `pip` resolve
        # to the SWE-bench env, not the harness's outer `rag` env.
        env["PATH"] = venv_bin + ":" + env.get("PATH", "")
        env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
        env.pop("PYTHONHOME", None)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"Error: {e}"

    out = out.strip()
    if len(out) > 8000:
        out = out[:4000] + "\n... [truncated] ...\n" + out[-2000:]
    return out or "(no output)"


def view_file(path: str, task: dict, start_line: int = 1, end_line: int = None) -> str:
    workspace = task.get("workspace_dir", "")
    full_path = path if os.path.isabs(path) else os.path.join(workspace, path)
    try:
        with open(full_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"

    start = max(0, start_line - 1)
    end = end_line if end_line else len(lines)
    selected = lines[start:end]
    numbered = "".join(f"{start + i + 1:4d} | {line}" for i, line in enumerate(selected))
    if len(numbered) > 8000:
        numbered = numbered[:8000] + "\n... [truncated] ..."
    return numbered


def edit_file(path: str, old_string: str, new_string: str, task: dict) -> str:
    workspace = task.get("workspace_dir", "")
    full_path = path if os.path.isabs(path) else os.path.join(workspace, path)
    try:
        with open(full_path) as f:
            content = f.read()
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"

    if old_string not in content:
        return f"Error: string not found in {path}. Check the exact text including whitespace."
    new_content = content.replace(old_string, new_string, 1)
    with open(full_path, "w") as f:
        f.write(new_content)
    return f"Edited {path} successfully."


def search_dir(pattern: str, task: dict, directory: str = ".", file_pattern: str = "*.py") -> str:
    workspace = task.get("workspace_dir", "/tmp")
    search_path = os.path.join(workspace, directory) if not os.path.isabs(directory) else directory
    try:
        proc = subprocess.run(
            ["grep", "-r", "-n", "--include", file_pattern, pattern, search_path],
            capture_output=True, text=True, timeout=30,
        )
        out = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return "[TIMEOUT: search took >30s]"
    except Exception as e:
        return f"Search error: {e}"

    if not out:
        return "No matches found."
    # Strip workspace prefix to keep paths short
    out = out.replace(workspace + "/", "")
    if len(out) > 6000:
        out = out[:6000] + "\n... [truncated] ..."
    return out


def dispatch_tool(tool_name: str, args, task: dict) -> str:
    # Some tool-call parsers pass `arguments` through as a JSON-encoded string.
    # Parse it here so downstream tools always get a dict.
    if isinstance(args, str):
        import json as _json
        try:
            args = _json.loads(args)
        except _json.JSONDecodeError:
            return f"ERROR: tool arguments were not valid JSON: {args[:400]}"
    if not isinstance(args, dict):
        return f"ERROR: tool arguments must be a dict, got {type(args).__name__}"

    def _need(key, *aliases):
        # Return the first present value among key and aliases, or a polite
        # error string the agent can see and retry with.
        for k in (key, *aliases):
            if k in args and args[k] is not None:
                return args[k]
        return None

    try:
        if tool_name == "python_exec":
            code = _need("code", "source", "script", "python", "_raw_arguments")
            if code is None:
                return f"ERROR: python_exec needs a 'code' argument. Got keys: {list(args.keys())}"
            return python_exec(code, task)
        if tool_name == "sql_exec":
            query = _need("query", "sql", "_raw_arguments")
            if query is None:
                return f"ERROR: sql_exec needs a 'query' argument. Got keys: {list(args.keys())}"
            return sql_exec(query, task)
        if tool_name == "web_search":
            q = _need("query", "q", "_raw_arguments")
            if q is None:
                return f"ERROR: web_search needs a 'query' argument. Got keys: {list(args.keys())}"
            return web_search(q, task)
        if tool_name == "fetch_url":
            url = _need("url", "_raw_arguments")
            if url is None:
                return f"ERROR: fetch_url needs a 'url' argument. Got keys: {list(args.keys())}"
            return fetch_url(url, task)
        if tool_name == "bash_exec":
            cmd = _need("command", "cmd", "_raw_arguments")
            if cmd is None:
                return f"ERROR: bash_exec needs a 'command' argument. Got keys: {list(args.keys())}"
            return bash_exec(cmd, task)
        if tool_name == "view_file":
            path = _need("path", "file", "filename")
            if path is None:
                return f"ERROR: view_file needs a 'path' argument. Got keys: {list(args.keys())}"
            return view_file(path, task, args.get("start_line", 1), args.get("end_line"))
        if tool_name == "edit_file":
            return edit_file(args["path"], args["old_string"], args["new_string"], task)
    except Exception as e:
        import traceback
        return f"ERROR running {tool_name}: {e}\n{traceback.format_exc()}"
    if tool_name == "search_dir":
        return search_dir(args["pattern"], task, args.get("directory", "."), args.get("file_pattern", "*.py"))
    return f"Unknown tool: {tool_name}"
