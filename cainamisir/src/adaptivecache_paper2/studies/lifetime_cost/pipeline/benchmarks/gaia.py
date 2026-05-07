"""GAIA adapter.

GAIA (General AI Assistants benchmark, Mialon et al. 2023) — multi-step
reasoning over the open web. Levels 1/2/3; Levels 2/3 require many tool
calls (search, browse, file reading, code execution). Total agent context
on Level 3 routinely exceeds 50K tokens, making this a natural fit for
the lifetime-cost study.

Dataset: gaia-benchmark/GAIA on Hugging Face (gated; needs HF auth).

This adapter:
  - Loads the dataset via `datasets.load_dataset("gaia-benchmark/GAIA")`
  - Wires four basic tools: web_search, browse, read_file, python
  - Tools are stubbed/wrapped with light implementations; for production
    eval, plug in a real browser (e.g., Playwright via browser-use)

For the lifetime-cost study, what matters is the *cost shape* of a long
trajectory, not whether we score SOTA. We can run with stub tools that
return canned/empty observations and still get a real cliff measurement
(though resolve rate will be 0).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from .base import Benchmark, Task, Tool, ToolEnv


class GAIA(Benchmark):
    name = "gaia"

    def __init__(
        self,
        level: int = 1,
        split: str = "validation",
        max_tasks: int = 30,
        tool_backend: str = "stub",        # "stub" | "real"
        attachments_dir: Optional[str] = None,
    ):
        if level not in (1, 2, 3):
            raise ValueError("level must be 1, 2, or 3")
        self.level = level
        self.split = split
        self.max_tasks = max_tasks
        self.tool_backend = tool_backend
        self.attachments_dir = attachments_dir

    def _load_dataset(self):
        try:
            from datasets import load_dataset
        except ImportError as e:
            raise ImportError("Install with `pip install datasets`") from e
        try:
            ds = load_dataset("gaia-benchmark/GAIA", f"2023_level{self.level}", split=self.split)
        except Exception as e:
            raise RuntimeError(
                "Failed to load GAIA. The dataset is gated; run `huggingface-cli login` "
                "and accept the dataset's terms at https://huggingface.co/datasets/gaia-benchmark/GAIA"
            ) from e
        return ds

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _build_tools(self, attachment_path: Optional[str]) -> ToolEnv:
        if self.tool_backend == "real":
            return self._real_tools(attachment_path)
        return self._stub_tools(attachment_path)

    def _stub_tools(self, attachment_path: Optional[str]) -> ToolEnv:
        """Minimal tools that return realistic but uninformative observations.
        Sufficient for cost-shape measurement, not for solving GAIA."""

        def web_search(args):
            q = args.get("query", "")
            return f"[stub] 5 search results for {q!r}: result1.html result2.html ..."

        def browse(args):
            url = args.get("url", "")
            return f"[stub] Page contents for {url}: " + ("Lorem ipsum " * 200)

        def read_file(args):
            if not attachment_path:
                return "[no attachment for this task]"
            try:
                return Path(attachment_path).read_text()[:10000]
            except Exception as e:
                return f"[read_file error] {e}"

        def python(args):
            code = args.get("code", "")
            return f"[stub] would run: {code[:200]}"

        return ToolEnv([
            Tool("web_search", "Search the web", {
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }, web_search),
            Tool("browse", "Fetch a URL", {
                "type": "object", "properties": {"url": {"type": "string"}},
                "required": ["url"],
            }, browse),
            Tool("read_file", "Read the task attachment", {
                "type": "object", "properties": {}, "required": [],
            }, read_file),
            Tool("python", "Execute Python code", {
                "type": "object", "properties": {"code": {"type": "string"}},
                "required": ["code"],
            }, python),
        ])

    def _real_tools(self, attachment_path: Optional[str]) -> ToolEnv:
        """Real tools: requires `pip install duckduckgo-search trafilatura`."""
        try:
            from duckduckgo_search import DDGS  # noqa
            import trafilatura  # noqa
        except ImportError as e:
            raise ImportError(
                "Real GAIA tools require `pip install duckduckgo-search trafilatura`."
            ) from e

        def web_search(args):
            from duckduckgo_search import DDGS
            with DDGS() as ddg:
                hits = list(ddg.text(args.get("query", ""), max_results=8))
            return "\n".join(f"- {h['title']}: {h['href']}\n  {h['body']}" for h in hits)

        def browse(args):
            import trafilatura, urllib.request
            url = args["url"]
            with urllib.request.urlopen(url, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            return trafilatura.extract(html) or "(no extractable text)"

        def read_file(args):
            if not attachment_path:
                return "[no attachment for this task]"
            return Path(attachment_path).read_text()[:50000]

        def python(args):
            code = args.get("code", "")
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(code)
                path = f.name
            try:
                r = subprocess.run(
                    ["python", path], capture_output=True, text=True, timeout=15,
                )
                return (r.stdout + r.stderr)[:8000]
            finally:
                Path(path).unlink(missing_ok=True)

        return ToolEnv([
            Tool("web_search", "Search the web", {
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }, web_search),
            Tool("browse", "Fetch a URL", {
                "type": "object", "properties": {"url": {"type": "string"}},
                "required": ["url"],
            }, browse),
            Tool("read_file", "Read the task attachment", {
                "type": "object", "properties": {}, "required": [],
            }, read_file),
            Tool("python", "Execute Python code in a sandbox", {
                "type": "object", "properties": {"code": {"type": "string"}},
                "required": ["code"],
            }, python),
        ])

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def tasks(self) -> Iterable[Task]:
        ds = self._load_dataset()
        for i, row in enumerate(ds):
            if i >= self.max_tasks:
                break
            attachment = row.get("file_path") or None
            yield Task(
                id=f"gaia-l{self.level}-{row.get('task_id', i)}",
                messages_init=[
                    {"role": "system", "content": (
                        "You are a research assistant. Use the tools to answer the question. "
                        "When you have the final answer, respond with it directly without further tool calls."
                    )},
                    {"role": "user", "content": row["Question"]},
                ],
                tool_env=self._build_tools(attachment),
                metadata={
                    "level": self.level,
                    "expected_answer": row.get("Final answer"),
                    "annotator_metadata": row.get("Annotator Metadata"),
                },
                max_steps=40,
                evaluator=lambda traj, exp=row.get("Final answer"): self._evaluate(traj, exp),
            )

    @staticmethod
    def _evaluate(trajectory, expected) -> bool:
        if not expected or not trajectory.final_answer:
            return False
        # GAIA's official scorer is exact match after normalization.
        return _gaia_normalize(trajectory.final_answer) == _gaia_normalize(expected)


def _gaia_normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())
