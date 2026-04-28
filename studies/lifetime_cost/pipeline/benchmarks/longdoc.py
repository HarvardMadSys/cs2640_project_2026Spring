"""Synthetic long-document agent benchmark.

Designed specifically for compaction stress-testing: a single very long
document (50K-500K tokens) is hidden behind a `read_section(start, end)`
tool. The agent must find K facts scattered through the document, combine
them, and answer a multi-hop question. This forces:
  - Many tool calls (each `read_section` returns a chunk; can't be one-shot)
  - Each observation is large (a chunk of the document)
  - Total context blows past any reasonable budget within ~10-15 steps

Why this benchmark exists: τ-bench/GAIA depend on external state (DB,
web). For ablations and reproducibility we need a controlled long-context
agent task. This is a clean, deterministic, no-external-deps benchmark
where compaction MUST fire.

Construction:
  - Take any HF document corpus (default: cnn_dailymail or wikitext)
  - Concatenate articles to target length
  - Insert K "needle" sentences at known positions, each carrying a
    distinct fact (X is at position Y, Z is the value of W, ...)
  - Ask a question that requires retrieving and combining all K needles
"""

from __future__ import annotations

import hashlib
import random
from typing import Iterable, List, Optional

from .base import Benchmark, Task, Tool, ToolEnv


class LongDocAgent(Benchmark):
    name = "longdoc"

    def __init__(
        self,
        n_tasks: int = 20,
        target_doc_tokens: int = 80_000,
        n_needles: int = 4,
        chunk_chars: int = 4_000,
        seed: int = 42,
        corpus: str = "wikitext",            # "wikitext" | "cnn_dailymail" | "lorem"
    ):
        self.n_tasks = n_tasks
        self.target_doc_tokens = target_doc_tokens
        self.n_needles = n_needles
        self.chunk_chars = chunk_chars
        self.seed = seed
        self.corpus = corpus

    def _load_corpus(self) -> str:
        """Return one big string of filler text approximately
        `target_doc_tokens * 4` characters long."""
        # Roughly 4 chars per token; this gives us a doc whose tokenized
        # length lands close to target_doc_tokens.
        target_chars = self.target_doc_tokens * 4

        if self.corpus == "lorem":
            unit = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            n_units = max(target_chars // len(unit), 1)
            return unit * n_units

        try:
            from datasets import load_dataset
        except ImportError:
            return "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 50_000

        if self.corpus == "wikitext":
            ds = load_dataset("wikitext", "wikitext-103-raw-v1", split="train", streaming=True)
            buf = []
            n = 0
            target = self.target_doc_tokens * 5  # rough chars
            for row in ds:
                t = row["text"]
                if not t.strip():
                    continue
                buf.append(t)
                n += len(t)
                if n >= target:
                    break
            return "\n\n".join(buf)

        if self.corpus == "cnn_dailymail":
            ds = load_dataset("cnn_dailymail", "3.0.0", split="train", streaming=True)
            buf = []
            n = 0
            target = self.target_doc_tokens * 5
            for row in ds:
                t = row["article"]
                buf.append(t)
                n += len(t)
                if n >= target:
                    break
            return "\n\n".join(buf)

        raise ValueError(f"Unknown corpus {self.corpus!r}")

    # ------------------------------------------------------------------
    # Per-task synthesis
    # ------------------------------------------------------------------

    def _generate_task(self, rng: random.Random, base_doc: str, idx: int):
        # Pick K needles. Each needle is a (key, value) pair that the
        # multi-hop question will require.
        keys = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
        rng.shuffle(keys)
        needles = []
        for j in range(self.n_needles):
            k = keys[j]
            v = rng.randint(1000, 9999)
            needles.append((k, v))

        # Insert each needle at a roughly even position in the document
        chars = list(base_doc)
        n_chars = len(chars)
        for j, (k, v) in enumerate(needles):
            pos = int(n_chars * (j + 1) / (self.n_needles + 1))
            sentence = f" The secret value of {k} is {v}. "
            chars[pos:pos] = sentence
        doc = "".join(chars)

        # Multi-hop question: sum of needle values
        question = (
            f"The document below contains, hidden in its text, the secret values of "
            f"{', '.join(k for k, _ in needles)}. Each appears in a sentence of the form "
            f"'The secret value of <name> is <number>.'. Use the read_section tool to "
            f"locate all of them. Then return the SUM of these {self.n_needles} numbers as "
            f"your final answer. Do not guess — find each value first."
        )
        expected = sum(v for _, v in needles)

        # Build tool: read_section(start, end) — limited slice
        def read_section(args):
            start = int(args.get("start", 0))
            end = int(args.get("end", start + self.chunk_chars))
            end = min(end, len(doc))
            start = max(0, min(start, len(doc)))
            if end <= start:
                return "[empty range]"
            slice_ = doc[start:end]
            return f"[doc bytes {start}:{end} of {len(doc)}]\n{slice_}"

        def search(args):
            q = args.get("query", "")
            # Naive substring search — agent could use this as an oracle if smart
            idxs = []
            i = 0
            while True:
                j = doc.find(q, i)
                if j < 0 or len(idxs) >= 8:
                    break
                idxs.append(j)
                i = j + 1
            return f"Found {len(idxs)} matches at positions: {idxs}"

        def submit(args):
            return f"[submitted: {args.get('answer')}]"

        tools = ToolEnv([
            Tool("read_section",
                 f"Read a slice of the document. The document has {len(doc)} characters. "
                 f"Pass start and end character offsets; max chunk size is {self.chunk_chars}.",
                 {"type": "object",
                  "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
                  "required": ["start", "end"]},
                 read_section),
            Tool("search",
                 "Search the document for a substring. Returns matching positions. "
                 "Useful for jumping to needles once you know what to look for.",
                 {"type": "object",
                  "properties": {"query": {"type": "string"}},
                  "required": ["query"]},
                 search),
            Tool("submit",
                 "Submit your final numeric answer.",
                 {"type": "object",
                  "properties": {"answer": {"type": "integer"}},
                  "required": ["answer"]},
                 submit),
        ])

        system = (
            "You are a long-document analyst. Use read_section and search to find "
            "specific values in a long document, then submit your final answer."
        )

        task_hash = hashlib.sha256(f"{idx}-{self.seed}".encode()).hexdigest()[:8]
        return Task(
            id=f"longdoc-{task_hash}",
            messages_init=[
                {"role": "system", "content": system},
                {"role": "user", "content": question + f"\n\nDocument length: {len(doc)} characters."},
            ],
            tool_env=tools,
            metadata={
                "needles": needles,
                "expected_sum": expected,
                "doc_chars": len(doc),
            },
            max_steps=40,
            evaluator=lambda traj, exp=expected: self._evaluate(traj, exp),
        )

    def tasks(self) -> Iterable[Task]:
        rng = random.Random(self.seed)
        base_doc = self._load_corpus()
        for i in range(self.n_tasks):
            yield self._generate_task(rng, base_doc, i)

    @staticmethod
    def _evaluate(trajectory, expected_sum) -> bool:
        # The submit tool is the ground truth signal; otherwise scan final_answer
        for step in reversed(trajectory.steps):
            tcs = step.response.tool_calls or []
            for tc in tcs:
                if tc.get("function", {}).get("name") == "submit":
                    try:
                        args = tc["function"]["arguments"]
                        if isinstance(args, str):
                            import json
                            args = json.loads(args)
                        return int(args.get("answer", -1)) == expected_sum
                    except Exception:
                        return False
        if trajectory.final_answer:
            try:
                import re
                m = re.search(r"-?\d+", trajectory.final_answer)
                if m:
                    return int(m.group(0)) == expected_sum
            except Exception:
                pass
        return False
