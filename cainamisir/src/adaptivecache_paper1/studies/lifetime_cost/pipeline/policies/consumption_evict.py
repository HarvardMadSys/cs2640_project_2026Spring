"""Action-graph-based supersession: evict only what the agent has CONSUMED.

The novelty vs every other compaction policy in this codebase: instead of
*scoring* observations and evicting the lowest, we look at the agent's own
subsequent tool calls and ask "did this later action make this earlier obs
verifiably stale?" If yes → tag the obs as consumed. We only ever drop
consumed obs. If nothing's consumed at compaction time, we tie `none`.

Consumption rules implemented (coding-agent specific, swebench_live):

  (a) Same-target edit. After `edit_file(path=X, ...)`, any earlier
      `read_file(path=X)` is stale (file state changed). Tag.
  (b) Search → read. After `read_file(path=X)`, any earlier `search`
      result that mentioned X in its hits is consumed (the agent
      followed the lead).
  (c) Run-tests cascade. After a fresh `run_tests`, prior `run_tests`
      outputs are stale. Keep only the most recent.
  (d) List-files consumed. After the agent reads any file under
      directory D, an earlier `list_files(path=D)` is consumed.
  (e) Duplicate read. After a later `read_file(path=X)`, any earlier
      `read_file(path=X)` is stale (we have a fresher copy of the same
      file).

Eviction policy:
  - Tag consumed obs with `_consumed=True` metadata (mutates messages).
  - Accumulate the tagged byte count.
  - When tagged bytes ≥ drop_threshold_tokens, fire one compaction event
    that holes all tagged obs (one cliff, ≥ drop_threshold tokens of
    confirmed exhaust justifying it).
  - If the threshold is never met, never compact (clean tie with `none`).

Cost properties:
  - No LLM call.
  - No model attention access.
  - Continuous tagging (every step, ~free).
  - Batched eviction (one cliff per ≥ drop_threshold of confirmed exhaust).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..types import CompactionEvent
from .base import CompactionContext, CompactionPolicy


# Match a path-like token in tool obs content. Catches `requests/utils.py`,
# `src/flask/cli.py`, etc. Conservative: requires a slash + a `.py` or `.md`
# or `.txt` ending. Avoids false-positives on URLs.
_PATH_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_\-/]*\.(py|md|txt|cfg|toml|yaml|yml|json|rst)\b")


class ConsumptionEvict(CompactionPolicy):
    name = "consumption_evict"

    def __init__(
        self,
        drop_threshold_tokens: int = 5000,
        protect_recent: int = 3,
        min_obs_tokens: int = 200,
        trigger_ratio: float = 0.85,
        use_hard_budget_trigger: bool = False,
        preserve_facts: bool = False,
        outline_mode: bool = False,
    ):
        super().__init__(
            drop_threshold_tokens=drop_threshold_tokens,
            protect_recent=protect_recent,
            min_obs_tokens=min_obs_tokens,
            trigger_ratio=trigger_ratio,
            use_hard_budget_trigger=use_hard_budget_trigger,
            preserve_facts=preserve_facts,
            outline_mode=outline_mode,
        )
        self.drop_threshold_tokens = drop_threshold_tokens
        self.protect_recent = protect_recent
        self.min_obs_tokens = min_obs_tokens
        self.trigger_ratio = trigger_ratio
        self.use_hard_budget_trigger = use_hard_budget_trigger
        self.preserve_facts = preserve_facts
        # Outline mode (mutually exclusive with preserve_facts): placeholder
        # contains a multi-line structural outline of the original file
        # (line# → header) plus an explicit "re-read to act" instruction.
        # Designed to give the agent location breadcrumbs without inducing
        # the function-name commitment loop seen in preserve_facts.
        self.outline_mode = outline_mode

    def maybe_compact(
        self, messages: List[dict], ctx: CompactionContext
    ) -> Tuple[List[dict], Optional[CompactionEvent]]:
        # Lazy trigger: don't fire until the budget bites
        budget_for_trigger = ctx.hard_budget if self.use_hard_budget_trigger else ctx.budget
        n_tok_before = self._token_count(messages, ctx.tokenizer)
        if n_tok_before < self.trigger_ratio * budget_for_trigger:
            return messages, None

        # Walk the action graph and tag consumed obs. We tag fresh each call
        # because the action graph grows monotonically with each step.
        consumed_idxs = self._find_consumed(messages)

        # Don't evict the most recent few tool messages even if "consumed"
        # by an even-newer call — protect_recent guards against thrash near
        # the agent's working set.
        tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        protected = set(tool_idxs[-self.protect_recent:]) if tool_idxs else set()
        consumed_idxs = [i for i in consumed_idxs if i not in protected]

        # Filter to msgs large enough to bother evicting + still have content
        # (skip already-holed marker messages).
        tagged: List[Tuple[int, int]] = []  # (idx, n_tokens)
        for i in consumed_idxs:
            m = messages[i]
            content = m.get("content") or ""
            if not isinstance(content, str):
                continue
            if content.startswith("[evicted") or content.startswith("[microcompacted"):
                continue
            tok = ctx.tokenizer.count(content)
            if tok < self.min_obs_tokens:
                continue
            tagged.append((i, tok))

        # Threshold check: only fire a compaction event when we've accumulated
        # enough confirmed exhaust to justify the single cliff.
        total_bytes = sum(tok for _, tok in tagged)
        if total_bytes < self.drop_threshold_tokens:
            return messages, None

        # Drop all tagged messages in one event
        new_messages = list(messages)
        evicted_count = 0
        for i, tok in tagged:
            holed = dict(new_messages[i])
            reason = self._reason_for(messages, i)
            if self.outline_mode:
                outline = self._extract_outline(messages, i)
                if outline:
                    holed["content"] = (
                        f"[evicted body (~{tok} tokens), consumed by {reason}; "
                        f"re-read with read_file to act on this content]\n"
                        f"{outline}"
                    )
                else:
                    holed["content"] = (
                        f"[evicted: ~{tok} tokens, consumed by {reason}, "
                        f"consumption_evict — re-read with read_file to act]"
                    )
            elif self.preserve_facts:
                fact = self._extract_fact(messages, i)
                fact_str = f" — fact: {fact}" if fact else ""
                holed["content"] = (
                    f"[evicted: ~{tok} tokens, consumed by {reason}, consumption_evict{fact_str}]"
                )
            else:
                holed["content"] = (
                    f"[evicted: ~{tok} tokens, consumed by {reason}, consumption_evict]"
                )
            new_messages[i] = holed
            evicted_count += 1

        n_tok_after = self._token_count(new_messages, ctx.tokenizer)
        return new_messages, CompactionEvent(
            step=ctx.step,
            policy=self.name,
            msgs_before=len(messages),
            msgs_after=len(new_messages),
            tokens_before=n_tok_before,
            tokens_after=n_tok_after,
            compaction_input_cached_tokens=0,
            compaction_input_uncached_tokens=0,
            compaction_output_tokens=0,
        )

    # ------------------------------------------------------------------
    # consumption analysis
    # ------------------------------------------------------------------

    def _find_consumed(self, messages: List[dict]) -> List[int]:
        """Walk the messages, tag each tool message that's been consumed by a
        later action. Returns sorted indices of consumed tool messages."""
        # Build a map: tool_call_id → (assistant_msg_idx, tool_name, args, tool_msg_idx)
        # This lets us quickly find what produced any given tool message.
        produced_by: Dict[int, Tuple[str, dict]] = {}  # tool_msg_idx → (tool_name, args)
        # Walk pairs (assistant with tool_calls, tool_msg following)
        for i, m in enumerate(messages):
            if m.get("role") != "tool":
                continue
            tcid = m.get("tool_call_id", "")
            if not tcid:
                continue
            # Find the assistant turn that issued this tool_call
            for j in range(i - 1, -1, -1):
                prev = messages[j]
                if prev.get("role") != "assistant":
                    continue
                for tc in (prev.get("tool_calls") or []):
                    if tc.get("id") == tcid:
                        fn = tc.get("function") or {}
                        produced_by[i] = (fn.get("name", ""), fn.get("arguments") or {})
                        break
                break

        # Walk forward through ALL future tool_calls (not just messages); for each,
        # check if it consumes any earlier obs.
        consumed: set = set()
        # Pre-compute list of (tool_msg_idx_after, future_tool_name, future_args)
        # for each future tool call we encounter (so we can do the matchups in O(N²))
        for fut_assist_idx, fut_msg in enumerate(messages):
            if fut_msg.get("role") != "assistant":
                continue
            for tc in (fut_msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                fut_name = fn.get("name", "")
                fut_args = fn.get("arguments") or {}
                # For each earlier tool obs, check if THIS future call consumes it.
                for tool_msg_idx, (prev_name, prev_args) in produced_by.items():
                    if tool_msg_idx >= fut_assist_idx:
                        continue  # not actually earlier
                    if tool_msg_idx in consumed:
                        continue  # already tagged
                    if self._consumes(
                        prev_name=prev_name,
                        prev_args=prev_args,
                        prev_msg=messages[tool_msg_idx],
                        fut_name=fut_name,
                        fut_args=fut_args,
                    ):
                        consumed.add(tool_msg_idx)

        return sorted(consumed)

    @staticmethod
    def _consumes(
        *,
        prev_name: str,
        prev_args: dict,
        prev_msg: dict,
        fut_name: str,
        fut_args: dict,
    ) -> bool:
        """Return True if `fut` makes `prev` verifiably stale/exhaust."""
        prev_path = (prev_args or {}).get("path") if isinstance(prev_args, dict) else None
        fut_path = (fut_args or {}).get("path") if isinstance(fut_args, dict) else None

        # Rule (a): edit_file(X) consumes earlier read_file(X)
        if prev_name == "read_file" and fut_name == "edit_file":
            if prev_path and fut_path and prev_path == fut_path:
                return True
        # Rule (e): later read_file(X) consumes earlier read_file(X)
        if prev_name == "read_file" and fut_name == "read_file":
            if prev_path and fut_path and prev_path == fut_path:
                return True
        # Rule (c): later run_tests consumes earlier run_tests
        if prev_name == "run_tests" and fut_name == "run_tests":
            return True
        # Rule (b): future read_file(X) consumes earlier search whose hits include X
        if prev_name == "search" and fut_name == "read_file":
            if fut_path:
                content = prev_msg.get("content") or ""
                if isinstance(content, str):
                    hit_paths = set(_PATH_RE.findall(content))  # returns extension only
                    # _PATH_RE has groups; get full matches via finditer
                    full_hits = {m.group(0) for m in _PATH_RE.finditer(content)}
                    if fut_path in full_hits:
                        return True
        # Rule (d): future read_file(X) consumes earlier list_files(D) if X is under D
        if prev_name == "list_files" and fut_name == "read_file":
            if fut_path and prev_path is not None:
                # "" or "." mean repo root → list of all dirs; consumed when any read happens
                listed = prev_path or "."
                if listed in (".", "") or fut_path.startswith(listed.rstrip("/") + "/"):
                    return True
        return False

    @staticmethod
    def _extract_fact(messages: List[dict], idx: int) -> str:
        """Pull a one-line structural summary from the obs's content.

        Step-B mechanism: the [evicted ...] placeholder retains the *value*
        of the obs at ~50-100 tokens instead of zero. The agent can still
        reason about "I read file X, it had functions a/b/c" even after
        the full content is gone.
        """
        m = messages[idx]
        tcid = m.get("tool_call_id", "")
        tool_name = "?"
        args: Dict = {}
        for j in range(idx - 1, -1, -1):
            prev = messages[j]
            if prev.get("role") != "assistant":
                continue
            for tc in (prev.get("tool_calls") or []):
                if tc.get("id") == tcid:
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "?")
                    raw_args = fn.get("arguments") or {}
                    if isinstance(raw_args, dict):
                        args = raw_args
                    break
            break

        content = m.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            return ""

        if tool_name == "read_file":
            # Extract function/class headers from content
            defs = []
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith(("def ", "class ", "async def ")):
                    defs.append(stripped[:80])
                    if len(defs) >= 5:
                        break
            path = args.get("path", "?")
            if defs:
                return f"read {path!r} → defs: " + " | ".join(defs)
            first_lines = [l.strip() for l in content.split("\n") if l.strip()][:2]
            return f"read {path!r} → " + " ".join(first_lines)[:160]

        if tool_name == "search":
            hits = [l.strip() for l in content.split("\n") if l.strip()][:3]
            return f"search {args.get('pattern','?')!r} → " + " | ".join(hits)[:200]

        if tool_name == "list_files":
            items = [x for x in content.split("\n") if x.strip()][:8]
            return f"list_files {args.get('path','.')!r} → " + " ".join(items)[:160]

        if tool_name == "run_tests":
            first = (content.split("\n", 1)[0] or "")[:80]
            tail = "\n".join(content.split("\n")[-3:])[:160].replace("\n", " ")
            return f"run_tests {args.get('test_path','.')!r} → {first} … {tail}"

        if tool_name == "edit_file":
            return f"edit {args.get('path','?')!r} applied"

        return ""

    @staticmethod
    def _extract_outline(messages: List[dict], idx: int) -> str:
        """Build a structural outline of a `read_file` obs.

        Returns a multi-line string of `lineN: <header>` entries (top-level
        defs/classes only, capped at 30 entries). Token cost is ~150-300
        for a typical 2K-token file. For non-read_file tools, returns ''.

        Outline-mode design rationale (from Phase D v2 mechanism analysis):
        plain placeholder forced re-fetch and won; facts placeholder
        anchored agent on flat function-name list and lost. This outline
        is a middle ground — provides location breadcrumbs (line numbers
        for re-read targeting) without offering a flat list to commit to.
        """
        m = messages[idx]
        tcid = m.get("tool_call_id", "")
        tool_name = "?"
        for j in range(idx - 1, -1, -1):
            prev = messages[j]
            if prev.get("role") != "assistant":
                continue
            for tc in (prev.get("tool_calls") or []):
                if tc.get("id") == tcid:
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "?")
                    break
            break

        if tool_name != "read_file":
            return ""

        content = m.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            return ""

        outline_lines: List[str] = []
        for ln_no, line in enumerate(content.split("\n"), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if (
                stripped.startswith(("def ", "class ", "async def "))
                or stripped.startswith(("@",))
                or (stripped.startswith("from ") or stripped.startswith("import "))
            ):
                outline_lines.append(f"  L{ln_no}: {stripped[:100]}")
                if len(outline_lines) >= 30:
                    outline_lines.append("  ... (outline truncated)")
                    break
        if not outline_lines:
            return ""
        return "outline:\n" + "\n".join(outline_lines)

    @staticmethod
    def _reason_for(messages: List[dict], idx: int) -> str:
        """Human-readable tag for the [evicted: ...] marker."""
        # Walk backward to find the assistant turn that issued the tool_call,
        # then return its name. Used purely for the marker text.
        m = messages[idx]
        tcid = m.get("tool_call_id", "")
        for j in range(idx - 1, -1, -1):
            prev = messages[j]
            if prev.get("role") != "assistant":
                continue
            for tc in (prev.get("tool_calls") or []):
                if tc.get("id") == tcid:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "?")
                    args = fn.get("arguments") or {}
                    if isinstance(args, dict) and args.get("path"):
                        return f"later action on {args['path']!r}"
                    return f"later {name}"
            break
        return "later action"
