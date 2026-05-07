"""Empirical test for the documented hybrid-attn prefix-cache tool-call bug.

Hypothesis (from issues mlx-lm#980, omlx#825, vLLM hybrid-cache design doc):
on hybrid linear+full attention models, reusing the same KV blocks across
two requests can corrupt structured outputs (e.g., Hermes-style
<tool_call>...) because GatedDeltaNet's recurrent state encodes
context-specific gating dynamics that don't transfer across requests.

Protocol
--------
We send pairs of requests A, B that share a long common prefix (a bulky
tool definition + 1 turn of dialogue). Request A *forces* a tool call
(by prompting "search for X"). Request B does the same, with a slight
suffix variation. We send each pair under two conditions:

  * cold:   restart the prefix cache between A and B  (no reuse)
  * warm:   keep the cache, so B reuses A's prefix tokens

Failure modes we score:
  * tool_call_emitted:   did the response include a parseable
                         <tool_call>{"name":..., "arguments":...}</tool_call>?
  * args_well_formed:    JSON parses; required keys present.
  * deviation_score:     edit distance from the cold-condition baseline.

The bug, if it manifests, looks like:
  - cold:  warm:  emits  emits   plain text (or malformed JSON)
  - cold tps higher than warm? (no — warm should be faster on the
    full-attn layers; the *quality* is the question).

This script talks to a vLLM OpenAI-compatible server over HTTP. Launch
the server with paper/paper2/scripts/qwen36_serve_vllm.sh first.

Usage:
  python prefix_cache_toolcall_probe.py \
      --base-url http://localhost:9876/v1 \
      --model Qwen/Qwen3.6-27B-FP8 \
      --out paper/paper2/out/probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise SystemExit("pip install openai") from e


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def search_tool_def() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Use this when the "
                "user asks about recent events, prices, scores, or any "
                "fact that may have changed since training."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    }


def long_preamble(n_repeats: int = 40) -> str:
    """A long, deterministic preamble to bulk up the shared prefix so the
    prefix cache actually gets a meaningful hit (vLLM's block size is 16
    by default; we want 100s of tokens of overlap)."""
    para = (
        "You are a careful research assistant. When the user asks for "
        "information that requires up-to-date knowledge, you must call "
        "the web_search tool rather than answering from memory. Be concise. "
        "Always show your reasoning before calling a tool. "
    )
    return para * n_repeats


def build_messages(query_suffix: str, system_extra: str = "") -> list[dict]:
    return [
        {"role": "system", "content": long_preamble() + system_extra},
        {"role": "user",   "content":
            f"What is the current population of Tokyo? {query_suffix}"},
    ]


def parse_response(resp_msg: Any) -> dict[str, Any]:
    """Extract tool-call signal whether vLLM emits structured tool_calls
    (when --enable-auto-tool-choice + --tool-call-parser hermes is set)
    or just raw text containing <tool_call>...</tool_call>."""
    content = getattr(resp_msg, "content", "") or ""
    tool_calls = getattr(resp_msg, "tool_calls", None) or []
    structured = []
    for tc in tool_calls:
        try:
            args_raw = tc.function.arguments
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            structured.append({"name": tc.function.name, "arguments": args})
        except Exception as e:
            structured.append({"name": getattr(tc.function, "name", "?"),
                               "error": f"{type(e).__name__}: {e}"})
    raw_calls = []
    for m in TOOL_CALL_RE.finditer(content):
        try:
            raw_calls.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            raw_calls.append({"_raw": m.group(1), "_parse_error": True})
    return {
        "content": content,
        "structured_tool_calls": structured,
        "raw_tool_calls_in_text": raw_calls,
        "tool_call_emitted": bool(structured or raw_calls),
        "tool_call_parsed":  any(
            "name" in c and "arguments" in c and isinstance(c.get("arguments"), dict)
            for c in structured + raw_calls
        ),
    }


def run_pair(client: OpenAI, model: str, suffix_a: str, suffix_b: str
             ) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, suffix in (("a", suffix_a), ("b", suffix_b)):
        t0 = time.time()
        r = client.chat.completions.create(
            model=model,
            messages=build_messages(suffix),
            tools=[search_tool_def()],
            tool_choice="auto",
            temperature=0.0,
            max_tokens=256,
        )
        dt = time.time() - t0
        msg = r.choices[0].message
        usage = r.usage
        cached = getattr(usage, "prompt_tokens_details", None)
        cached_n = getattr(cached, "cached_tokens", None) if cached else None
        out[label] = {
            **parse_response(msg),
            "latency_s": dt,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "cached_prompt_tokens": cached_n,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:9876/v1")
    ap.add_argument("--api-key", default="EMPTY")
    ap.add_argument("--model", default="Qwen/Qwen3.6-27B-FP8")
    ap.add_argument("--out", default="paper/paper2/out/probe.jsonl")
    ap.add_argument("--n-pairs", type=int, default=10)
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    # Diverse suffixes to give B's request enough variation that it differs
    # in the *generation* but shares the *prefill* with A.
    suffixes = [
        ("Please double-check.",            "Please cite a source."),
        ("Use the latest census.",          "Use city-proper figures."),
        ("Round to the nearest million.",   "Use the official 2024 estimate."),
        ("",                                "Now."),
        ("(I'm a journalist.)",             "(I'm writing a report.)"),
    ] * (args.n_pairs // 5 + 1)
    suffixes = suffixes[: args.n_pairs]

    n_emit = {"a": 0, "b": 0}
    n_parsed = {"a": 0, "b": 0}
    cached_b: list[int] = []

    with open(args.out, "w") as fh:
        for i, (sa, sb) in enumerate(suffixes):
            row = run_pair(client, args.model, sa, sb)
            row["pair_idx"] = i
            fh.write(json.dumps(row) + "\n")
            for k in ("a", "b"):
                if row[k]["tool_call_emitted"]:
                    n_emit[k] += 1
                if row[k]["tool_call_parsed"]:
                    n_parsed[k] += 1
            if row["b"]["cached_prompt_tokens"] is not None:
                cached_b.append(row["b"]["cached_prompt_tokens"])
            print(f"  pair {i}: a_emit={row['a']['tool_call_emitted']} "
                  f"b_emit={row['b']['tool_call_emitted']} "
                  f"b_cached={row['b']['cached_prompt_tokens']}")
    print()
    print(f"summary over {args.n_pairs} pairs:")
    print(f"  a: emit={n_emit['a']}/{args.n_pairs}  parsed={n_parsed['a']}/{args.n_pairs}")
    print(f"  b: emit={n_emit['b']}/{args.n_pairs}  parsed={n_parsed['b']}/{args.n_pairs}")
    if cached_b:
        avg = sum(cached_b) / len(cached_b)
        print(f"  b avg cached_prompt_tokens = {avg:.1f}  "
              f"(prefix-cache hit indicator)")


if __name__ == "__main__":
    main()
