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


class _ChainedEnv:
    """Wraps `chain_size` τ-bench envs into one logical session.

    Forwards `step` to the active env; when the user simulator ends a
    conversation (resp.done), records the customer's reward and advances
    to the next env. The handoff is presented to the agent as a synthetic
    suffix on the last `respond` observation: "[Customer N hung up.
    Customer N+1 is now calling.]\\n\\n<next env's reset observation>".

    Mirrors the (subset of) tau_bench EnvResponse interface that the
    runner needs: .observation (str), .done (bool), .reward (float),
    .info (dict). Also exposes .calculate_reward() returning a mock
    reward object whose `.reward` is the *mean* per-customer reward
    (unfinished customers count as 0.0).
    """

    class _Resp:
        __slots__ = ("observation", "done", "reward", "info")

    def __init__(self, envs, action_cls, initial_observations):
        self._envs = envs
        self._Action = action_cls
        self._idx = 0
        self._rewards = []
        self._initial_obs = initial_observations
        self._all_done = False

    @property
    def current(self):
        return self._envs[self._idx]

    @property
    def wiki(self):
        return self._envs[0].wiki

    @property
    def tools_info(self):
        return self._envs[0].tools_info

    @property
    def task(self):
        return self._envs[self._idx].task

    def step(self, action):
        if self._all_done:
            r = _ChainedEnv._Resp()
            r.observation = "[session over: all customers handled] ###STOP###"
            r.done = True
            r.reward = 0.0
            r.info = {}
            return r
        resp = self.current.step(action)
        # τ-bench end-of-conversation is signaled in two redundant ways: the
        # env's `resp.done` flag, AND the literal "###STOP###" marker the
        # user-sim emits in its last reply. The runner only checks the
        # marker (string match in observation). For non-final customers we
        # MUST strip the marker; otherwise the runner terminates the whole
        # trajectory before our handoff observation reaches the agent.
        ended = bool(resp.done) or "###STOP###" in (resp.observation or "")
        if not ended:
            return resp

        # Customer ended → record reward
        try:
            rw = self.current.calculate_reward()
            self._rewards.append(float(rw.reward if hasattr(rw, "reward") else rw))
        except Exception:
            self._rewards.append(0.0)

        # All customers done? Pass the original observation (with STOP) through.
        if self._idx >= len(self._envs) - 1:
            self._all_done = True
            return resp

        # Advance to next customer; synthesize handoff observation, with the
        # STOP marker stripped so the runner does NOT terminate.
        self._idx += 1
        next_obs = self._initial_obs[self._idx] or ""
        cleaned = (resp.observation or "").replace("###STOP###", "").rstrip()
        out = _ChainedEnv._Resp()
        out.observation = (
            cleaned
            + f"\n\n---\n[Customer {self._idx} has hung up. "
            f"Customer {self._idx + 1} of {len(self._envs)} is now calling.]\n\n"
            + next_obs
        )
        out.done = False
        out.reward = 0.0
        out.info = {}
        return out

    def calculate_reward(self):
        # Pad unfinished customers with 0.0 so the mean reflects coverage.
        rewards = list(self._rewards)
        # If the chain ended mid-customer (e.g., max_steps hit), try to
        # pull a reward from the in-progress env so partial credit still
        # counts. Otherwise this customer scores 0.
        if len(rewards) < len(self._envs) and not self._all_done:
            try:
                rw = self.current.calculate_reward()
                rewards.append(float(rw.reward if hasattr(rw, "reward") else rw))
            except Exception:
                rewards.append(0.0)
        while len(rewards) < len(self._envs):
            rewards.append(0.0)
        mean_r = sum(rewards) / len(rewards)
        out = _ChainedEnv._Resp()
        out.reward = mean_r
        out.info = {"per_customer_rewards": rewards}
        return out


class TauBench(Benchmark):
    name = "taubench"

    def __init__(
        self,
        domain: str = "airline",
        split: str = "test",
        max_tasks: int = 50,
        user_model: str = "claude-haiku-4-5",
        user_provider: str = "anthropic",
        chain_size: int = 1,
        chain_max_steps_per_customer: int = 40,
    ):
        if domain not in ("airline", "retail"):
            raise ValueError("domain must be 'airline' or 'retail'")
        if chain_size < 1:
            raise ValueError("chain_size must be >= 1")
        self.domain = domain
        self.split = split
        self.max_tasks = max_tasks
        self.user_model = user_model
        self.user_provider = user_provider
        # Chain mode: bundle `chain_size` consecutive customer tasks into one
        # extended trajectory. The agent's messages persist across customers;
        # the env state resets per customer. Forces compaction at chains
        # ≥ 3 by accumulating ~40-50 turns and ~30K+ tokens per customer.
        self.chain_size = chain_size
        self.chain_max_steps_per_customer = chain_max_steps_per_customer

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
        # Note: tau-bench's base env defaults task_index to
        # random.randint(0, len(tasks)) which is INCLUSIVE on both ends and
        # can return len(tasks) → IndexError. We pin to 0 here; the caller
        # always reset()s to the desired index immediately after.
        return get_env(
            env_name=self.domain,
            user_strategy="llm",
            user_model=self.user_model,
            user_provider=self.user_provider,
            task_split=self.split,
            task_index=0,
        )

    def tasks(self) -> Iterable[Task]:
        get_env, Action = self._import_tau()

        # Fresh env per task: env.step() mutates state, and our harness
        # materializes all tasks before running them. Each Task closure
        # captures its own env so evaluators see the right post-run state.
        first_env = self._build_env(get_env)
        total = len(first_env.tasks)
        n = min(self.max_tasks, total)

        if self.chain_size <= 1:
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
            return

        # Chain mode — bundle `chain_size` consecutive customers into one Task.
        # We yield ⌈n / chain_size⌉ chain Tasks. n is the upper bound on
        # number of customers consumed, NOT chains.
        for chain_start in range(0, n, self.chain_size):
            envs = []
            initial_obs = []
            user_ids = []
            for j in range(self.chain_size):
                idx = chain_start + j
                if idx >= n:
                    break
                e = self._build_env(get_env)
                reset_resp = e.reset(task_index=idx)
                envs.append(e)
                initial_obs.append(reset_resp.observation)
                user_ids.append(e.task.user_id)
            if not envs:
                continue

            chained = _ChainedEnv(envs, Action, initial_obs)
            tools = self._wrap_chained_tools(chained, Action)

            opening = (
                f"You are a customer-service agent. You will serve {len(envs)} "
                f"customers in sequence. When a customer hangs up, the next will "
                f"start automatically — keep using the same tools and respond with "
                f"the `respond` tool. Customer 1 of {len(envs)} is now calling.\n\n"
                f"{initial_obs[0]}"
            )
            messages_init = [
                {"role": "system", "content": chained.wiki},
                {"role": "user", "content": opening},
            ]
            chain_id = f"taubench-{self.domain}-chain-{chain_start:03d}-{chain_start + len(envs) - 1:03d}"

            def make_eval(c=chained):
                def evaluator(traj):
                    rw = c.calculate_reward()
                    return float(rw.reward) >= 0.99
                return evaluator

            yield Task(
                id=chain_id,
                messages_init=messages_init,
                tool_env=tools,
                metadata={
                    "domain": self.domain,
                    "chain_size": len(envs),
                    "task_indices": list(range(chain_start, chain_start + len(envs))),
                    "user_ids": user_ids,
                },
                max_steps=len(envs) * self.chain_max_steps_per_customer,
                evaluator=make_eval(),
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

    def _wrap_chained_tools(self, chained: "_ChainedEnv", Action) -> ToolEnv:
        """Wrap a `_ChainedEnv` so tool calls are routed through it.

        Schemas come from envs[0] (identical across customers in the
        same domain). We re-bind tool calls so they hit `chained.step`,
        which routes to the active env and auto-advances on `done`.
        """
        wrapped = []
        for tool_spec in chained.tools_info:
            fn_meta = tool_spec["function"]
            name = fn_meta["name"]

            def make_fn(tool_name=name, c=chained):
                def call(args):
                    try:
                        resp = c.step(Action(name=tool_name, kwargs=args))
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

        def respond_fn(args, c=chained):
            text = args.get("content", "")
            try:
                resp = c.step(Action(name="respond", kwargs={"content": text}))
                return resp.observation
            except Exception as e:
                return f"[tool error] {type(e).__name__}: {e}"

        wrapped.append(Tool(
            name="respond",
            description="Send a message to the current customer (user). Use this whenever you want to talk; the customer will reply.",
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
