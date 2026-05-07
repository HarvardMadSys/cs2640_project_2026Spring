"""Phase 5 runner integration test (no GPU).

Drives `run_task` with a mock ChatModel that:
1. First chat: emits a `read_file` tool call.
2. After getting a long obs back, second chat asks for summary, third chat
   issues `recall(memento_id="mem-1")`.
3. Verifies that:
   * The tool_env got the recall tool injected.
   * The system message got the addendum appended.
   * The model's `queue_recall` was called with the original obs text.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

_here = Path(__file__).resolve().parent
_repo_root = _here.parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("PAPER2_TEST_NO_VLLM", "1")

from studies.lifetime_cost.pipeline.benchmarks.base import Tool, ToolEnv, Task
from studies.lifetime_cost.pipeline.runner import run_task
from studies.lifetime_cost.pipeline.types import Usage
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


class FakeChatResponse:
    def __init__(self, content="", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage or Usage(prompt_tokens=100, completion_tokens=20, cached_tokens=80)


class ScriptedModel:
    """A ChatModel stub that walks through a fixed sequence of responses."""

    model_name = "scripted/test-model"

    def __init__(self, script):
        self._script = list(script)
        self._idx = 0
        self.queue_recall_calls = []

    def chat(self, messages, *, tools=None, max_tokens=2048, **kwargs):
        # Stash the most recent rendered tool list so we can inspect it.
        self.last_tools = tools
        self.last_messages = list(messages)
        if self._idx >= len(self._script):
            return FakeChatResponse(content="DONE")
        resp = self._script[self._idx]
        self._idx += 1
        return resp

    def queue_recall(self, obs_text):
        self.queue_recall_calls.append(obs_text)
        return f"obs:fake_{len(self.queue_recall_calls)}"


def _stub_writer():
    w = MagicMock()
    w.write.return_value = ("SUMMARY for big obs", MagicMock(input_tokens=10, output_tokens=5, cost_usd=0.001))
    return w


def test_runner_injects_recall_tool_and_addendum():
    big_obs = "FULL OBS: " + ("X" * 2000)

    # SWE-bench-style tools
    base_tools = [
        Tool(name="read_file", description="Read a file",
             parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
             fn=lambda args: big_obs),
        Tool(name="submit", description="Submit",
             parameters={"type": "object", "properties": {}, "required": []},
             fn=lambda args: "Submitted"),
    ]
    tool_env = ToolEnv(base_tools)
    task = Task(
        id="test_task",
        messages_init=[
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "Find the bug."},
        ],
        tool_env=tool_env,
        max_steps=5,
    )

    # The policy keeps the last 2 tool messages "recent" (recent_skip=2)
    # and only tags older ones, so we need 3 reads before compaction can
    # tag mem-1. Then on step 3 the model sees the memento and recalls it.
    model = ScriptedModel([
        FakeChatResponse(tool_calls=[
            {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "src/foo.py"}'}}
        ]),
        FakeChatResponse(tool_calls=[
            {"id": "c2", "function": {"name": "read_file", "arguments": '{"path": "src/bar.py"}'}}
        ]),
        FakeChatResponse(tool_calls=[
            {"id": "c3", "function": {"name": "read_file", "arguments": '{"path": "src/baz.py"}'}}
        ]),
        FakeChatResponse(tool_calls=[
            {"id": "c4", "function": {"name": "recall", "arguments": '{"memento_id": "mem-1"}'}}
        ]),
        FakeChatResponse(content="Done analyzing."),
    ])

    policy = MementoPolicy(
        min_obs_chars=300,
        recall_tool_enabled=True,
        recall_enabled=False,
        recall_mode="attmask",
        # Force compaction immediately
        trigger_ratio=0.01,
        target_ratio=0.001,
        writer=_stub_writer(),
    )

    traj = run_task(
        task, model, policy,
        benchmark_name="test",
        budget_tokens=100,
        hard_budget_tokens=200,
        max_completion_tokens=128,
    )

    # The tool_env passed to chat() should include recall.
    tool_names = {t["function"]["name"] for t in (model.last_tools or [])}
    assert "recall" in tool_names, f"recall tool missing from {tool_names}"
    assert "read_file" in tool_names, f"read_file tool missing from {tool_names}"

    # The TASK's tool_env must NOT have been mutated (recall is scoped to run).
    assert "recall" not in task.tool_env._tools, \
        "recall leaked into task.tool_env — would leak across variants in the bake"

    # System message addendum should have been added.
    sys_msg = next((m for m in (traj.steps[0].messages_in if traj.steps else [])
                    if m.role == "system"), None)
    if sys_msg:
        assert "memento_id" in sys_msg.content, \
            f"addendum missing from system msg: {sys_msg.content[:120]}"

    # The recall handler should have called model.queue_recall with the
    # original big_obs. Compaction must have fired and tagged at least
    # one memento; mem-1 should map to big_obs.
    assert "mem-1" in policy._recall_table, \
        f"mem-1 not registered (table: {list(policy._recall_table.keys())})"
    assert policy._recall_table["mem-1"] == big_obs

    # The model's recall tool call should have triggered queue_recall(big_obs).
    assert big_obs in model.queue_recall_calls, \
        f"queue_recall not invoked with big_obs (got {len(model.queue_recall_calls)} calls)"


if __name__ == "__main__":
    test_runner_injects_recall_tool_and_addendum()
    print("OK — runner integration smoke passed")
