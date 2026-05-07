"""SWE-bench Lite live agent benchmark — no docker required.

Drops the agent into a real cloned-and-checked-out repo with read/edit/grep/
test tools. The agent has to find the relevant file(s), make the fix, and
verify. Context grows naturally to 30-50K tokens because real codebases are
big and the agent has to explore.

Resolve metric (oracle-overlap, not docker-grade):
  - We compare the agent's git diff against SWE-bench's `patch` (gold).
  - The agent is "resolved" iff (a) it modified the same files as gold, and
    (b) the union of modified line ranges in agent's diff overlaps with
    gold's modified line ranges by ≥ MIN_LINE_OVERLAP_RATIO.
  - This is a coarse oracle. It can give false positives (agent edited the
    right region with a wrong fix) and false negatives (agent fixed the bug
    but in a different region than gold). For policy *ranking* — which is
    what we actually need — it's adequate. Stronger eval (real test_patch
    execution) requires SWE-bench's docker harness; out of scope here.

Why this benchmark for compaction:
  - Tool obs (file reads) are 2K-15K tokens each. Agent reads 3-10 files.
    Total context easily reaches 30-50K — exactly the regime where
    compaction is forced and (good) compaction can win.
  - Each file read is independent: once the agent has digested it, the raw
    obs is mostly redundant. Pure-eviction policies should shine here.

Pre-requisite: clone the relevant repos to `cache_dir` once. We do this in
the smoke-runner setup, not lazily, to avoid surprise cold starts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .base import Benchmark, Task, Tool, ToolEnv


MIN_LINE_OVERLAP_RATIO = 0.3
MAX_FILE_READ_BYTES = 80_000      # full small/medium files fit; agent doesn't loop re-reading
MAX_LIST_FILES = 200               # cap directory listings
MAX_GREP_HITS = 30
MAX_TEST_OUTPUT = 8_000


_REPO_URLS = {
    "psf/requests": "https://github.com/psf/requests",
    "pallets/flask": "https://github.com/pallets/flask",
    "django/django": "https://github.com/django/django",
    "sympy/sympy": "https://github.com/sympy/sympy",
    "pylint-dev/pylint": "https://github.com/pylint-dev/pylint",
    "pytest-dev/pytest": "https://github.com/pytest-dev/pytest",
    "matplotlib/matplotlib": "https://github.com/matplotlib/matplotlib",
    "scikit-learn/scikit-learn": "https://github.com/scikit-learn/scikit-learn",
}


class SWEBenchLive(Benchmark):
    name = "swebench_live"

    def __init__(
        self,
        instance_ids: List[str] | None = None,
        cache_dir: str = "/scratch/swebench_repos",
        max_tasks: int | None = None,
        max_steps_per_task: int = 40,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.instance_ids = instance_ids
        self.max_tasks = max_tasks
        self.max_steps_per_task = max_steps_per_task

    def _load_instances(self) -> List[Dict[str, Any]]:
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        if self.instance_ids:
            wanted = set(self.instance_ids)
            inst = [x for x in ds if x["instance_id"] in wanted]
            # Preserve user-specified order
            order = {iid: i for i, iid in enumerate(self.instance_ids)}
            inst.sort(key=lambda x: order[x["instance_id"]])
        else:
            inst = list(ds)
        if self.max_tasks is not None:
            inst = inst[: self.max_tasks]
        return inst

    def _ensure_repo(self, repo: str) -> Path:
        """Make sure `cache_dir/<repo-tail>` exists as a bare-ish clone we can
        check out from. The smoke driver pre-clones small repos; this method
        is a backstop for missed ones."""
        tail = repo.split("/", 1)[1]
        local = self.cache_dir / tail
        if not (local / ".git").exists():
            url = _REPO_URLS.get(repo)
            if not url:
                raise RuntimeError(f"No clone URL configured for {repo!r}")
            subprocess.run(
                ["git", "clone", "--quiet", "--no-checkout", url, str(local)],
                check=True,
            )
        return local

    def tasks(self) -> Iterable[Task]:
        for inst in self._load_instances():
            yield self._make_task(inst)

    def _make_task(self, inst: Dict[str, Any]) -> Task:
        repo_cache = self._ensure_repo(inst["repo"])
        base_commit = inst["base_commit"]
        instance_id = inst["instance_id"]
        problem = inst["problem_statement"]
        gold_patch = inst["patch"]

        # Per-task workspace: copy the cache repo into a tempdir, then
        # check out the base commit. The agent operates here.
        workspace = Path(tempfile.mkdtemp(prefix=f"swelive_{instance_id}_"))
        # Clone (with the cache as a local "remote") — this is fast (~1s).
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", str(repo_cache), str(workspace / "repo")],
            check=True,
        )
        repo_dir = workspace / "repo"
        subprocess.run(
            ["git", "checkout", "--quiet", base_commit],
            cwd=repo_dir, check=True,
        )

        tools = self._build_tools(repo_dir)

        # System: brief tool guide + acknowledgement of the task framing.
        # The user message is the SWE-bench problem statement.
        sys_msg = (
            "You are a coding agent fixing a bug in a Python repository. "
            "You have these tools:\n"
            "  - list_files(path): list files under a relative path (omit for repo root)\n"
            "  - read_file(path): read a file's contents\n"
            "  - search(pattern): regex grep across the repo (returns file:line snippets)\n"
            "  - edit_file(path, old_string, new_string): exact-string replacement; old_string MUST appear exactly once\n"
            "  - run_tests(test_path): run pytest on a file or directory; returns truncated output\n"
            "  - submit(): call when you believe the fix is complete\n"
            "Strategy: explore enough to understand the bug, make the minimal targeted edit, run tests if you can find them, then submit. "
            "Do NOT read the same file twice unnecessarily — the prior read is still in context."
        )

        return Task(
            id=instance_id,
            messages_init=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": problem},
            ],
            tool_env=tools,
            metadata={
                "instance_id": instance_id,
                "repo": inst["repo"],
                "base_commit": base_commit,
                "_repo_dir": str(repo_dir),
                "_gold_patch": gold_patch,
                "_workspace": str(workspace),
            },
            max_steps=self.max_steps_per_task,
            evaluator=lambda traj, rd=str(repo_dir), gp=gold_patch: _evaluate(rd, gp),
        )

    def _build_tools(self, repo_dir: Path) -> ToolEnv:
        return ToolEnv([
            Tool(
                name="list_files",
                description="List files under a relative path in the repo. Omit `path` for the root. Excludes .git and __pycache__.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative directory path; default repo root."}},
                    "required": [],
                },
                fn=lambda args, rd=repo_dir: _list_files(rd, args.get("path", ".")),
            ),
            Tool(
                name="read_file",
                description=f"Read a file's contents. Capped at {MAX_FILE_READ_BYTES} bytes.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative file path."}},
                    "required": ["path"],
                },
                fn=lambda args, rd=repo_dir: _read_file(rd, args.get("path", "")),
            ),
            Tool(
                name="search",
                description=f"Regex search across the repo (Python files). Returns up to {MAX_GREP_HITS} matches as `path:line: snippet`.",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string", "description": "Python regex."}},
                    "required": ["pattern"],
                },
                fn=lambda args, rd=repo_dir: _search(rd, args.get("pattern", "")),
            ),
            Tool(
                name="edit_file",
                description="Exact-string replacement. `old_string` MUST appear exactly once in the file. Returns OK or an error.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                fn=lambda args, rd=repo_dir: _edit_file(rd, args.get("path", ""), args.get("old_string", ""), args.get("new_string", "")),
            ),
            Tool(
                name="run_tests",
                description=f"Run pytest. `test_path` can be a file or directory; defaults to the repo. Output truncated to {MAX_TEST_OUTPUT} bytes.",
                parameters={
                    "type": "object",
                    "properties": {"test_path": {"type": "string", "description": "Relative path; default repo root."}},
                    "required": [],
                },
                fn=lambda args, rd=repo_dir: _run_tests(rd, args.get("test_path", ".")),
            ),
            Tool(
                name="submit",
                description="Signal that you believe the fix is complete. Stops the agent loop.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=lambda args: "Submitted. ###STOP###",
            ),
        ])


# ---------------------------------------------------------------------------
# Tool implementations (pure functions over the workspace)
# ---------------------------------------------------------------------------

def _safe_join(repo: Path, rel: str) -> Path:
    """Resolve `rel` against `repo`, refusing to escape the repo root."""
    target = (repo / rel).resolve()
    if not str(target).startswith(str(repo.resolve())):
        raise ValueError(f"path escapes repo: {rel!r}")
    return target


def _list_files(repo: Path, rel: str) -> str:
    try:
        target = _safe_join(repo, rel or ".")
        if not target.exists():
            return f"[no such path: {rel}]"
        if not target.is_dir():
            return f"[not a directory: {rel}]"
        entries = []
        for p in sorted(target.iterdir()):
            if p.name in (".git", "__pycache__", ".pytest_cache"):
                continue
            kind = "/" if p.is_dir() else ""
            entries.append(p.name + kind)
            if len(entries) >= MAX_LIST_FILES:
                entries.append(f"... ({MAX_LIST_FILES}+ entries)")
                break
        return "\n".join(entries) if entries else "[empty]"
    except Exception as e:
        return f"[list_files error: {type(e).__name__}: {e}]"


def _read_file(repo: Path, rel: str) -> str:
    try:
        target = _safe_join(repo, rel)
        if not target.exists() or not target.is_file():
            return f"[no such file: {rel}]"
        data = target.read_bytes()
        truncated = ""
        if len(data) > MAX_FILE_READ_BYTES:
            data = data[:MAX_FILE_READ_BYTES]
            truncated = f"\n[... truncated to {MAX_FILE_READ_BYTES} bytes]"
        return data.decode("utf-8", errors="replace") + truncated
    except Exception as e:
        return f"[read_file error: {type(e).__name__}: {e}]"


def _search(repo: Path, pattern: str) -> str:
    if not pattern:
        return "[search: empty pattern]"
    try:
        cre = re.compile(pattern)
    except re.error as e:
        return f"[search: bad regex: {e}]"
    hits: List[str] = []
    try:
        for p in repo.rglob("*.py"):
            if any(part in (".git", "__pycache__", ".pytest_cache") for part in p.parts):
                continue
            try:
                for ln, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if cre.search(line):
                        rel = p.relative_to(repo)
                        snippet = line.strip()[:200]
                        hits.append(f"{rel}:{ln}: {snippet}")
                        if len(hits) >= MAX_GREP_HITS:
                            return "\n".join(hits) + f"\n[... cut at {MAX_GREP_HITS} matches]"
            except Exception:
                continue
        return "\n".join(hits) if hits else "[no matches]"
    except Exception as e:
        return f"[search error: {type(e).__name__}: {e}]"


def _edit_file(repo: Path, rel: str, old: str, new: str) -> str:
    if not old:
        return "[edit_file error: old_string is empty]"
    try:
        target = _safe_join(repo, rel)
        if not target.exists() or not target.is_file():
            return f"[no such file: {rel}]"
        text = target.read_text(errors="replace")
        n = text.count(old)
        if n == 0:
            return f"[edit_file error: old_string not found in {rel}]"
        if n > 1:
            return f"[edit_file error: old_string appears {n} times in {rel}; make it unique]"
        target.write_text(text.replace(old, new, 1))
        return f"OK: edited {rel} (1 replacement)"
    except Exception as e:
        return f"[edit_file error: {type(e).__name__}: {e}]"


def _run_tests(repo: Path, rel: str) -> str:
    try:
        target = _safe_join(repo, rel or ".")
        if not target.exists():
            return f"[no such path: {rel}]"
        # Use the project's own pytest invocation. Tight timeout so a
        # broken test setup doesn't burn the whole smoke. -x stops at first
        # failure to keep output readable.
        proc = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-x", "-q",
             "--no-header", "--disable-warnings", str(target)],
            cwd=repo, capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if len(out) > MAX_TEST_OUTPUT:
            out = out[:MAX_TEST_OUTPUT] + f"\n[... truncated at {MAX_TEST_OUTPUT} bytes]"
        return f"[exit={proc.returncode}]\n{out}"
    except subprocess.TimeoutExpired:
        return "[run_tests: timed out after 60s]"
    except Exception as e:
        return f"[run_tests error: {type(e).__name__}: {e}]"


# ---------------------------------------------------------------------------
# Evaluation: compare agent's diff to gold
# ---------------------------------------------------------------------------

def _parse_patched_files(patch: str) -> Dict[str, set]:
    """From a unified diff, return {path: set of line numbers in the new file
    that were added or modified}. Crude — counts every `+` line."""
    out: Dict[str, set] = {}
    cur_path = None
    cur_new_line = 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            cur_path = line[len("+++ b/"):].strip()
            out.setdefault(cur_path, set())
        elif line.startswith("@@"):
            # @@ -a,b +c,d @@
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                cur_new_line = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            if cur_path is not None:
                out[cur_path].add(cur_new_line)
            cur_new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            pass  # removal — don't advance new line counter
        else:
            cur_new_line += 1
    return out


def _evaluate(repo_dir: str, gold_patch: str) -> bool:
    """Resolve == agent's diff covers a meaningful overlap with gold's diff.

    Definition: agent's diff and gold's diff must share at least one file,
    and within each shared file the agent's modified-line set must overlap
    gold's modified-line set by at least MIN_LINE_OVERLAP_RATIO of gold's
    set. Robust against tiny formatting differences but NOT against a
    completely wrong fix in the right region. Adequate for ranking
    compaction policies; not adequate for absolute resolve-rate claims.
    """
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, timeout=10,
        )
        agent_patch = proc.stdout or ""
    except Exception:
        return False
    if not agent_patch.strip():
        return False
    agent_files = _parse_patched_files(agent_patch)
    gold_files = _parse_patched_files(gold_patch)
    if not gold_files:
        return False
    shared = set(agent_files.keys()) & set(gold_files.keys())
    if not shared:
        return False
    for path in shared:
        a = agent_files[path]
        g = gold_files[path]
        if not g:
            continue
        # Check overlap by neighborhood: any agent line within ±3 of a
        # gold line counts as "near".
        near = sum(1 for gl in g if any(abs(gl - al) <= 3 for al in a))
        if near / max(len(g), 1) >= MIN_LINE_OVERLAP_RATIO:
            return True
    return False
