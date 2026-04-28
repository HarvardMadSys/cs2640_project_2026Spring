"""τ-bench adapter (Sierra Research).

τ-bench has two domains: airline (50 test tasks) and retail (~115 tasks).
Each task is a customer-service conversation between a user simulator and
the agent, with tool calls against a domain database. Conversations are
typically 20-50 turns with substantial DB-output observations.

Why this benchmark for AdaptiveCache: the system prompt (env.wiki) is
~10-15K tokens of domain rules; tool observations are JSON DB rows;
conversations grow into 30-80K-token contexts where compaction must fire.

Repo: https://github.com/sierra-research/tau-bench
Install: `pip install git+https://github.com/sierra-research/tau-bench`

The user simulator is itself an LLM (litellm-backed). Set
ANTHROPIC_API_KEY (or OPENAI_API_KEY) and pass user_model/user_provider to
match. The user-sim cost is *not* included in our trajectory's lifetime
cost — it's a fixed overhead per task, like an evaluator.
"""

from __future__ import annotations

from typing import Iterable

from .base import Benchmark, Task, Tool, ToolEnv


class TauBench(Benchmark):
    name = "taubench"

    def __init__(
        self,
        domain: str = "airline",
        split: str = "test",
        max_tasks: int = 50,
        user_model: str = "claude-haiku-4-5",
        user_provider: str = "anthropic",
    ):
        if domain not in ("airline", "retail"):
            raise ValueError("domain must be 'airline' or 'retail'")
        self.domain = domain
        self.split = split
        self.max_tasks = max_tasks
        self.user_model = user_model
        self.user_provider = user_provider

    def _import_tau(self):
        try:
            from tau_bench.envs import get_env
            from tau_bench.types import Action
            return get_env, Action
        except ImportError as e:
            raise ImportError(
                "tau-bench is not installed. Install with "
                "`pip install git+https://github.com/sierra-research/tau-bench`."
            ) from e

    def _build_env(self, get_env):
        return get_env(
            env_name=self.domain,
            user_strategy="llm",
            user_model=self.user_model,
            user_provider=self.user_provider,
            task_split=self.split,
        )

    def tasks(self) -> Iterable[Task]:
        get_env, Action = self._import_tau()

        # Fresh env per task: env.step() mutates state, and our harness
        # materializes all tasks before running them. Each Task closure
        # captures its own env so evaluators see the right post-run state.
        first_env = self._build_env(get_env)
        n = min(self.max_tasks, len(first_env.tasks))

        for idx in range(n):
            task_env = self._build_env(get_env)
            reset_resp = task_env.reset(task_index=idx)
            tools = self._wrap_env_tools(task_env, Action)

            messages_init = [
                {"role": "system", "content": task_env.wiki},
                {"role": "user", "content": reset_resp.observation},
            ]

            yield Task(
                id=f"taubench-{self.domain}-{idx}",
                messages_init=messages_init,
                tool_env=tools,
                metadata={
                    "domain": self.domain,
                    "task_index": idx,
                    "user_id": task_env.task.user_id,
                },
                max_steps=40,
                evaluator=lambda traj, e=task_env: self._evaluate(e),
            )

    def _wrap_env_tools(self, env, Action) -> ToolEnv:
        """Convert tau-bench's tool registry into our Tool objects.

        Each domain tool becomes a Tool whose .fn calls env.step(Action(...)).
        We also synthesize a `respond` tool that talks to the user simulator.
        """
        wrapped = []

        for tool_spec in env.tools_info:
            fn_meta = tool_spec["function"]
            name = fn_meta["name"]

            def make_fn(tool_name=name, env=env, Action=Action):
                def call(args):
                    try:
                        resp = env.step(Action(name=tool_name, kwargs=args))
                        return resp.observation
                    except Exception as e:
                        return f"[tool error] {type(e).__name__}: {e}"
                return call

            wrapped.append(Tool(
                name=name,
                description=fn_meta.get("description", ""),
                parameters=fn_meta.get("parameters", {"type": "object", "properties": {}}),
                fn=make_fn(),
            ))

        def respond_fn(args, env=env, Action=Action):
            text = args.get("content", "")
            try:
                resp = env.step(Action(name="respond", kwargs={"content": text}))
                return resp.observation
            except Exception as e:
                return f"[tool error] {type(e).__name__}: {e}"

        wrapped.append(Tool(
            name="respond",
            description="Send a message to the customer (user). Use this whenever you want to talk; the customer will reply.",
            parameters={
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Message text to send to the customer."}},
                "required": ["content"],
            },
            fn=respond_fn,
        ))

        return ToolEnv(wrapped)

    def _evaluate(self, env) -> bool:
        try:
            reward = env.calculate_reward()
            # tau-bench reward is in [0, 1] — 1.0 means task fully solved.
            return float(reward.reward if hasattr(reward, "reward") else reward) >= 1.0
        except Exception:
            return False
