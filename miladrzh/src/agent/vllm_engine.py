
"""
vLLM embedded as a Python library.

Two classes:

  AgentAwareBlockManager
      Subclass of vLLM's BlockSpaceManager. Override can_swap_out() /
      can_swap_in() here to implement agent-aware KV eviction/prefetch.
      Default behaviour is identical to stock vLLM (pure LRU).

  InstrumentedEngine
      Wraps vLLM's LLM class. Runs in the same process as the agent so we
      can read real GPU block counts before/after every tool-call idle window.
      Also injects AgentAwareBlockManager into the scheduler at startup.

Usage:
    engine = InstrumentedEngine("meta-llama/Llama-3.1-8B-Instruct")
    text, tool_calls = engine.generate_turn(messages, tools)
    snap = engine.get_kv_snapshot()   # {"kv_tokens_used": N, "used_gpu_blocks": M, ...}
"""

import json
import os
import re
import uuid
from typing import Dict, List, Optional, Tuple

# MIG workaround for vLLM 0.7.3: CUDA_VISIBLE_DEVICES is a MIG UUID on
# MIG slices, which vllm.platforms.cuda tries to int() and crashes on.
if "MIG-" in os.environ.get("CUDA_VISIBLE_DEVICES", ""):
    import vllm.platforms.cuda as _vcuda
    _vcuda.device_id_to_physical_device_id = lambda device_id=0: 0


# ---------------------------------------------------------------------------
# Block manager base — version-resilient import
# ---------------------------------------------------------------------------

def _import_block_manager_base():
    """
    Return the vLLM BlockSpaceManager base class.
    Falls back to object if vLLM is not installed — the class is then a
    no-op placeholder. InstrumentedEngine will raise at instantiation time.
    """
    for path in [
        ("vllm.core.block_manager_v1", "BlockSpaceManagerV1"),
        ("vllm.core.block_manager",    "BlockSpaceManagerV1"),
        ("vllm.core.block_manager",    "BlockSpaceManager"),
    ]:
        try:
            mod = __import__(path[0], fromlist=[path[1]])
            return getattr(mod, path[1])
        except (ImportError, AttributeError):
            continue
    return object  # graceful fallback; InstrumentedEngine will fail loudly at __init__


# ---------------------------------------------------------------------------
# Agent-aware block manager (policy hook)
# ---------------------------------------------------------------------------

class AgentAwareBlockManager(_import_block_manager_base()):
    """
    Drop-in replacement for vLLM's BlockSpaceManager.

    The class-level dict `_predicted_idle` maps request_id -> predicted idle
    seconds. The agent loop writes here before dispatching a tool call so the
    scheduler can make informed eviction decisions.

    Extend can_swap_out() to implement agent-aware policy:

        DRAM_THRESHOLD_S = 5.0   # evict to DRAM if idle > 5 s
        SSD_THRESHOLD_S  = 30.0  # evict to SSD  if idle > 30 s

        def can_swap_out(self, seq_group):
            predicted = self._predicted_idle.get(seq_group.request_id, 9999)
            if predicted < DRAM_THRESHOLD_S:
                return False   # too short — keep in GPU
            return super().can_swap_out(seq_group)
    """

    _predicted_idle: Dict[str, float] = {}

    @classmethod
    def set_predicted_idle(cls, request_id: str, seconds: float):
        cls._predicted_idle[request_id] = seconds

    @classmethod
    def clear_prediction(cls, request_id: str):
        cls._predicted_idle.pop(request_id, None)

    # Default: identical to parent (pure LRU).
    # Override here when implementing the agent-aware policy.


# ---------------------------------------------------------------------------
# Instrumented engine
# ---------------------------------------------------------------------------

class InstrumentedEngine:
    """
    vLLM embedded as a Python library with block-level instrumentation.

    Parameters
    ----------
    model : HuggingFace model ID, e.g. "meta-llama/Llama-3.1-8B-Instruct"
    dtype : "bfloat16" (default) or "float16"
    max_model_len : context window in tokens
    gpu_memory_utilization : fraction of GPU VRAM given to vLLM (default 0.9)
    tensor_parallel_size : number of GPUs for tensor parallelism
    """

    def __init__(
        self,
        model: str,
        dtype: str = "bfloat16",
        max_model_len: int = 32768,
        gpu_memory_utilization: float = 0.9,
        tensor_parallel_size: int = 1,
        enable_prefix_caching: bool | None = None,
        **kwargs,
    ):
        self._inject_block_manager()

        if enable_prefix_caching is None:
            from agent.config import ENABLE_PREFIX_CACHING
            enable_prefix_caching = ENABLE_PREFIX_CACHING

        from vllm import LLM
        self.llm = LLM(
            model=model,
            dtype=dtype,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            tensor_parallel_size=tensor_parallel_size,
            enable_prefix_caching=enable_prefix_caching,
            **kwargs,
        )
        self.prefix_caching_enabled = enable_prefix_caching
        self.tokenizer = self.llm.get_tokenizer()
        self.model_name = model
        self._block_size: int = self._read_block_size()

    # ------------------------------------------------------------------
    # Inject custom block manager before LLM.__init__ creates the scheduler
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_block_manager():
        """
        Replace the BlockSpaceManager reference in vllm.core.scheduler so
        that when the Scheduler is instantiated it uses AgentAwareBlockManager.
        Must be called before LLM() is created.
        """
        injected = False
        for mod_name in ["vllm.core.scheduler", "vllm.core.scheduler_v2"]:
            try:
                import importlib
                mod = importlib.import_module(mod_name)
                for attr in ["BlockSpaceManager", "BlockSpaceManagerV1",
                             "BlockSpaceManagerV2"]:
                    if hasattr(mod, attr):
                        setattr(mod, attr, AgentAwareBlockManager)
                        injected = True
            except ImportError:
                continue
        if not injected:
            print("[vllm_engine] WARNING: could not inject AgentAwareBlockManager; "
                  "block manager reads will still work but policy hooks are disabled.")

    # ------------------------------------------------------------------
    # Block-level instrumentation
    # ------------------------------------------------------------------

    def _get_scheduler(self):
        s = self.llm.llm_engine.scheduler
        return s[0] if isinstance(s, list) else s

    def _read_block_size(self) -> int:
        try:
            return self._get_scheduler().block_manager.block_size
        except Exception:
            return 16  # vLLM default

    def get_kv_snapshot(self) -> Dict:
        """
        Read real GPU KV-cache block usage from vLLM's allocator.

        With enable_prefix_caching=True, vLLM's PrefixCachingBlockAllocator
        keeps block *contents* mapped on GPU after a sequence finishes, so a
        subsequent request sharing a prefix can reuse them. These cached
        blocks are what holds KV memory during the tool-call idle window.

        Note: `get_num_free_gpu_blocks()` counts cached-but-evictable blocks
        as "free" (they can be reclaimed on demand), so it under-reports the
        blocks physically holding content. The correct idle-window measure
        is the PrefixCachingBlockAllocator's internal `_cached_blocks` map,
        whose size equals the number of blocks currently holding KV content
        (active + cached-evictable).

        Falls back to the classic `total - free` computation for naive
        (non-prefix-caching) allocators.
        """
        try:
            from vllm.utils import Device
            bm = self._get_scheduler().block_manager
            total = getattr(bm, "num_total_gpu_blocks", None)
            if total is None and hasattr(bm, "get_num_total_gpu_blocks"):
                total = bm.get_num_total_gpu_blocks()
            free  = bm.get_num_free_gpu_blocks()
            bs    = self._block_size

            # Reach the GPU sub-allocator. CpuGpuBlockAllocator keeps them in
            # _allocators keyed by Device. Private attr, but stable since
            # vLLM 0.6.x. If the layout changes we fall back to total-free.
            cached = None
            cga = getattr(bm, "block_allocator", None)
            if cga is not None and hasattr(cga, "_allocators"):
                gpu_alloc = cga._allocators.get(Device.GPU)
                if gpu_alloc is not None and hasattr(gpu_alloc, "_cached_blocks"):
                    cached = len(gpu_alloc._cached_blocks)

            if cached is not None:
                used = cached
                source = "real_prefix_cached"
            else:
                used = total - free
                source = "real_total_minus_free"

            # vLLM's own bytes-per-token (ground truth from its cache_config).
            # bytes_per_block / tokens_per_block. Used by the tracer to
            # populate kv_runtime_size, so it can differ from the analytical
            # kv_computed_size if our config dict is wrong.
            bytes_per_token_runtime = None
            try:
                cc = self.llm.llm_engine.cache_config
                bpb = getattr(cc, "cache_block_size", None)
                if bpb and bs:
                    bytes_per_token_runtime = bpb // bs
            except Exception:
                pass

            return {
                "source":           source,
                "total_gpu_blocks": total,
                "free_gpu_blocks":  free,
                "used_gpu_blocks":  used,
                "block_size":       bs,
                "kv_tokens_used":   used * bs,
                "bytes_per_token":  bytes_per_token_runtime,
            }
        except Exception as exc:
            return {"source": "error", "error": str(exc), "kv_tokens_used": 0}

    # ------------------------------------------------------------------
    # Chat generation with tool call parsing
    # ------------------------------------------------------------------

    def generate_turn(
        self,
        messages: List[Dict],
        tools: List[Dict],
        max_tokens: int = 2048,
    ) -> Tuple[str, Optional[List[Dict]]]:
        """
        Run one LLM turn. Formats messages with the model's chat template,
        generates, and parses any tool calls from the output.

        Returns
        -------
        text : raw generated text (stripped of special tokens)
        tool_calls : list of {"id", "name", "arguments"} or None
        finish_reason : "stop" | "length"
        """
        from vllm import SamplingParams

        prompt = self.tokenizer.apply_chat_template(
            messages,
            tools=tools if tools else None,
            add_generation_prompt=True,
            tokenize=False,
        )

        params = SamplingParams(
            temperature=0,
            max_tokens=max_tokens,
            stop_token_ids=self._stop_token_ids(),
            stop=["</tool_call>"],
            include_stop_str_in_output=True,
        )
        outputs = self.llm.generate([prompt], params)
        req_out = outputs[0]
        output  = req_out.outputs[0]
        raw     = output.text.strip()
        finish  = output.finish_reason   # "stop" | "length"

        # Expose RequestOutput.metrics (first_token_time, arrival_time, etc.)
        # so the agent loop can derive a real prefill/decode split when
        # vLLM populates them. Attribute may be None on older vLLM builds.
        self._last_metrics = getattr(req_out, "metrics", None)

        # Token counts for the tracer. vLLM RequestOutput exposes the prompt
        # token_ids on the request and the completion token_ids on each output.
        prompt_tokens     = len(getattr(req_out, "prompt_token_ids", []) or [])
        completion_tokens = len(getattr(output, "token_ids", []) or [])
        usage = type("Usage", (), {
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      prompt_tokens + completion_tokens,
        })()

        # Always try to parse. Even on length truncation, an earlier complete
        # <tool_call>...</tool_call> block may be recoverable.
        tool_calls = _parse_tool_calls(raw)
        return raw, tool_calls, finish, usage

    def _stop_token_ids(self) -> Optional[List[int]]:
        vocab = self.tokenizer.get_vocab()
        ids = [
            vocab[tok]
            for tok in ["<|eot_id|>", "<|eom_id|>", "<|end_of_text|>", "<|im_end|>"]
            if tok in vocab
        ]
        return ids or None


# ---------------------------------------------------------------------------
# Async instrumented engine (for concurrent multi-agent batching)
# ---------------------------------------------------------------------------

class AsyncInstrumentedEngine:
    """
    Async sibling of InstrumentedEngine. Wraps vLLM's AsyncLLMEngine so
    multiple agent loops can call generate_turn() concurrently and have
    vLLM's continuous batcher serve them in one engine.

    KV snapshot reads go through async_engine.engine (the underlying
    LLMEngine), same code path as the sync class.

    Per-request metrics (RequestOutput.metrics.first_token_time,
    arrival_time, finished_time) are returned alongside the parsed output
    so the tracer can record a real prefill/decode split per agent under
    contention.
    """

    def __init__(
        self,
        model: str,
        dtype: str = "bfloat16",
        max_model_len: int = 32768,
        gpu_memory_utilization: float = 0.9,
        tensor_parallel_size: int = 1,
        enable_prefix_caching: bool | None = None,
        max_num_seqs: int | None = None,
        scheduling_policy: str | None = None,
        **kwargs,
    ):
        InstrumentedEngine._inject_block_manager()

        if enable_prefix_caching is None:
            from agent.config import ENABLE_PREFIX_CACHING
            enable_prefix_caching = ENABLE_PREFIX_CACHING

        from vllm import AsyncLLMEngine, AsyncEngineArgs
        engine_args_kwargs = dict(
            model=model,
            dtype=dtype,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            tensor_parallel_size=tensor_parallel_size,
            enable_prefix_caching=enable_prefix_caching,
            disable_log_stats=False,  # keep metrics populated
        )
        if max_num_seqs is not None:
            engine_args_kwargs["max_num_seqs"] = max_num_seqs
        if scheduling_policy is not None:
            engine_args_kwargs["scheduling_policy"] = scheduling_policy
        engine_args_kwargs.update(kwargs)

        self.async_engine = AsyncLLMEngine.from_engine_args(
            AsyncEngineArgs(**engine_args_kwargs)
        )
        self.prefix_caching_enabled = enable_prefix_caching
        self.model_name = model

        # Resolve tokenizer via the underlying LLMEngine (sync); avoids
        # async-context gymnastics in __init__.
        self.tokenizer = self.async_engine.engine.get_tokenizer()
        self._block_size: int = self._read_block_size()

    # ------------------------------------------------------------------
    # Reach into the underlying LLMEngine for KV snapshots
    # ------------------------------------------------------------------

    def _underlying_engine(self):
        # AsyncLLMEngine.engine is the LLMEngine in vLLM 0.7.x
        return self.async_engine.engine

    def _get_scheduler(self):
        s = self._underlying_engine().scheduler
        return s[0] if isinstance(s, list) else s

    def _read_block_size(self) -> int:
        try:
            return self._get_scheduler().block_manager.block_size
        except Exception:
            return 16

    def get_kv_snapshot(self) -> Dict:
        """Same shape and semantics as InstrumentedEngine.get_kv_snapshot."""
        try:
            from vllm.utils import Device
            bm = self._get_scheduler().block_manager
            total = getattr(bm, "num_total_gpu_blocks", None)
            if total is None and hasattr(bm, "get_num_total_gpu_blocks"):
                total = bm.get_num_total_gpu_blocks()
            free  = bm.get_num_free_gpu_blocks()
            bs    = self._block_size

            cached = None
            cga = getattr(bm, "block_allocator", None)
            if cga is not None and hasattr(cga, "_allocators"):
                gpu_alloc = cga._allocators.get(Device.GPU)
                if gpu_alloc is not None and hasattr(gpu_alloc, "_cached_blocks"):
                    cached = len(gpu_alloc._cached_blocks)

            if cached is not None:
                used = cached
                source = "real_prefix_cached"
            else:
                used = total - free
                source = "real_total_minus_free"

            bytes_per_token_runtime = None
            try:
                cc = self._underlying_engine().cache_config
                bpb = getattr(cc, "cache_block_size", None)
                if bpb and bs:
                    bytes_per_token_runtime = bpb // bs
            except Exception:
                pass

            return {
                "source":           source,
                "total_gpu_blocks": total,
                "free_gpu_blocks":  free,
                "used_gpu_blocks":  used,
                "block_size":       bs,
                "kv_tokens_used":   used * bs,
                "bytes_per_token":  bytes_per_token_runtime,
            }
        except Exception as exc:
            return {"source": "error", "error": str(exc), "kv_tokens_used": 0}

    # ------------------------------------------------------------------
    # Async chat generation
    # ------------------------------------------------------------------

    async def generate_turn(
        self,
        messages: List[Dict],
        tools: List[Dict],
        max_tokens: int = 2048,
        request_id: Optional[str] = None,
        priority: int = 0,
    ):
        """
        Async one-turn generation. Multiple coroutines may call this
        concurrently; vLLM continuous-batches them.

        Returns: (raw, tool_calls, finish, usage, metrics)
            - metrics is the RequestOutput.metrics object (or None).
              The async loop reads .first_token_time off it for the
              prefill/decode split.
        """
        from vllm import SamplingParams

        prompt = self.tokenizer.apply_chat_template(
            messages,
            tools=tools if tools else None,
            add_generation_prompt=True,
            tokenize=False,
        )

        params = SamplingParams(
            temperature=0,
            max_tokens=max_tokens,
            stop_token_ids=self._stop_token_ids(),
            stop=["</tool_call>"],
            include_stop_str_in_output=True,
        )

        rid = request_id or uuid.uuid4().hex
        final = None
        async for out in self.async_engine.generate(prompt, params, rid, priority=priority):
            final = out
        if final is None:
            raise RuntimeError(f"AsyncLLMEngine produced no output for request {rid}")

        output = final.outputs[0]
        raw = output.text.strip()
        finish = output.finish_reason

        prompt_tokens     = len(getattr(final, "prompt_token_ids", []) or [])
        completion_tokens = len(getattr(output, "token_ids", []) or [])
        usage = type("Usage", (), {
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      prompt_tokens + completion_tokens,
        })()

        metrics = getattr(final, "metrics", None)
        tool_calls = _parse_tool_calls(raw)
        return raw, tool_calls, finish, usage, metrics

    def _stop_token_ids(self) -> Optional[List[int]]:
        vocab = self.tokenizer.get_vocab()
        ids = [
            vocab[tok]
            for tok in ["<|eot_id|>", "<|eom_id|>", "<|end_of_text|>", "<|im_end|>"]
            if tok in vocab
        ]
        return ids or None


# ---------------------------------------------------------------------------
# Tool call parser — Llama 3.1 / 3.3 JSON function call format
# ---------------------------------------------------------------------------

def _parse_tool_calls(text: str) -> Optional[List[Dict]]:
    """
    Parse tool calls from raw model output.

    Llama 3.1/3.3 emits tool calls as JSON when the chat template includes
    tool schemas. Handles:
      - bare JSON object:  {"name": "...", "arguments": {...}}
      - JSON array:        [{"name": ...}, ...]
      - markdown fenced:   ```json\n{...}\n```
      - python_tag prefix: <|python_tag|>{...}
    Returns list of {"id", "name", "arguments"} dicts or None.
    """
    # Strip Llama special tokens
    text = re.sub(r"<\|[^|]+\|>", "", text).strip()
    if not text:
        return None

    # Unwrap Qwen/Hermes <tool_call>...</tool_call> tags (may be multiple).
    # Also handle incomplete blocks where model hit EOS before </tool_call>:
    # model outputs "<tool_call>\n{...}\n</tool_" then stops on <|im_end|>.
    tc_blocks = re.findall(r"<tool_call>\s*([\s\S]*?)\s*</tool_call>", text)
    if not tc_blocks:
        incomplete = re.findall(r"<tool_call>\s*([\s\S]+?)$", text)
        # Strip any trailing partial closing tag fragment (e.g. "</tool_", "</")
        cleaned = [re.sub(r"\s*</?[a-z_]*$", "", b).strip() for b in incomplete]
        tc_blocks = [b for b in cleaned if b]
    if tc_blocks:
        parsed = []
        for block in tc_blocks:
            r = _try_parse(block.strip())
            if r:
                parsed.extend(r)
        if parsed:
            return parsed

    # Unwrap markdown code fence
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()

    # Try direct parse first
    result = _try_parse(text)
    if result is not None:
        return result

    # Try finding embedded JSON object (model may prefix with reasoning text)
    for match in re.finditer(r'(\{[\s\S]*?\})', text):
        result = _try_parse(match.group(1))
        if result is not None:
            return result

    return None


def _try_parse(text: str) -> Optional[List[Dict]]:
    try:
        # strict=False tolerates raw newlines/tabs inside string values, which
        # the model frequently emits for multi-line python_exec / sql_exec args.
        obj = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Small models (e.g. Qwen2.5-3B) frequently emit `\'` inside JSON
        # strings — an invalid JSON escape that leaked from Python source
        # training data. Recover by mapping `\'` -> `'` and retrying once.
        if r"\'" in text:
            try:
                obj = json.loads(text.replace(r"\'", "'"), strict=False)
            except json.JSONDecodeError:
                return None
        else:
            return None

    raw_calls = obj if isinstance(obj, list) else ([obj] if isinstance(obj, dict) else [])
    result = []
    for c in raw_calls:
        name = c.get("name") or c.get("function")
        if not name:
            continue
        args = c.get("arguments") or c.get("parameters") or {}
        if isinstance(args, str):
            try:
                # strict=False tolerates raw control chars (newlines, tabs)
                # inside JSON string values, which the model frequently emits
                # for multi-line SQL / Python tool arguments.
                args = json.loads(args, strict=False)
            except json.JSONDecodeError:
                # Keep the raw string under a sentinel key so tools can see it.
                args = {"_raw_arguments": args}
        if not isinstance(args, dict):
            args = {"_raw_arguments": args}
        result.append({
            "id":        f"call_{uuid.uuid4().hex[:8]}",
            "name":      name,
            "arguments": args,
        })
    return result or None
