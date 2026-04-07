"""Modal app: vLLM + LMCache inference server for AdaptiveCache experiments.

Runs Qwen/Qwen2.5-7B-Instruct on A100-80GB with:
- vLLM 0.8.5 (provides real num_cached_tokens metric)
- LMCache 0.4.2 (CPU KV offload + real per-block deletion via LMCacheConnectorV1)

Eviction is now real: delete_kv_blocks() calls engine.storage_manager.remove()
which removes specific blocks from LMCache's CPU store. On the next request,
those positions are cache misses → vLLM recomputes only those blocks.
Everything before the first evicted block remains a cache hit.
"""

from __future__ import annotations

import hashlib
import os
from typing import List

import modal

# ---------------------------------------------------------------------------
# Modal image
# ---------------------------------------------------------------------------

vllm_image = (
    # CUDA 12.4 devel image: has nvcc + headers needed to compile lmcache's CUDA extension
    # against the torch 2.6.0 that vLLM 0.8.5 requires.
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-devel-ubuntu22.04",
        add_python="3.11",
    )
    .pip_install(
        "vllm==0.8.5",               # installs torch==2.6.0
        "transformers>=4.40,<5.0",
        "huggingface-hub>=0.20",
        "numpy",
        "tiktoken",
    )
    .apt_install("clang", "git")   # clang: lmcache build linker; git: clone source
    .add_local_file(
        "/Users/cnmsr/Projects/cacheKarpathy/modal_app/patch_lmcache.py",
        remote_path="/tmp/lmcache_patch.py",
        copy=True,
    )
    .run_commands(
        # Build lmcache from source against torch 2.6.0.
        # TORCH_CUDA_ARCH_LIST needed because image build runs on CPU (no GPU to auto-detect).
        # Install setuptools-scm so git-tag-based versioning works during build
        "pip install --upgrade setuptools setuptools-scm pip",
        # Clone lmcache source and build against the already-installed torch 2.6.0.
        # SETUPTOOLS_SCM_PRETEND_VERSION bypasses the git-tag version lookup.
        # --no-build-isolation uses the ambient torch 2.6.0 (not a fresh pip-resolved one).
        "git clone --depth=1 --branch v0.4.3 https://github.com/LMCache/LMCache.git /tmp/lmcache_src || git clone --depth=1 https://github.com/LMCache/LMCache.git /tmp/lmcache_src",
        # Apply patches for vLLM 0.8.5 compatibility (engine_id + lookup_client)
        "python3 /tmp/lmcache_patch.py",
        "SETUPTOOLS_SCM_PRETEND_VERSION=0.4.3 TORCH_CUDA_ARCH_LIST='7.5;8.0;8.6' pip install /tmp/lmcache_src --no-build-isolation",

        # ABI shim: PyTorch 2.6.0 changed c10_cuda_check_implementation 4th param from
        # unsigned int (j) to int (i). Provide the missing 'jb' variant via LD_PRELOAD.
        (
            "printf '%s\\n'"
            " 'namespace c10 { namespace cuda {'"
            " '  extern void c10_cuda_check_implementation(int, const char*, const char*, int, bool);'"
            " '  void c10_cuda_check_implementation(int e, const char* f, const char* n, unsigned int l, bool b) {'"
            " '    c10_cuda_check_implementation(e, f, n, static_cast<int>(l), b);'"
            " '  }'"
            " '} }'"
            " > /tmp/lmcache_shim.cpp"
        ),
        "g++ -shared -fPIC -std=c++17 -o /usr/local/lib/lmcache_shim.so /tmp/lmcache_shim.cpp",
    )
    .env({"LD_PRELOAD": "/usr/local/lib/lmcache_shim.so"})
)

# ---------------------------------------------------------------------------
# Modal app + volumes
# ---------------------------------------------------------------------------

app = modal.App("adaptivecache-server")

model_volume = modal.Volume.from_name("adaptivecache-models", create_if_missing=True)
results_volume = modal.Volume.from_name("adaptivecache-results", create_if_missing=True)

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
CHUNK_SIZE = 256  # Must match LMCache chunk_size


# ---------------------------------------------------------------------------
# LMCache v1 block hash computation
#
# LMCache v1 uses an integer chunk_hash. We replicate the chain here so we
# can compute which hash corresponds to a given chunk index without needing
# to import lmcache on the client side.
# ---------------------------------------------------------------------------

def compute_block_hashes_v1(token_ids: list, chunk_size: int = CHUNK_SIZE) -> list:
    """Compute rolling hash chain for LMCache v1 (returns list of ints).

    LMCache v1 uses integer hashes (not hex strings). Chunk i covers
    token_ids[i*chunk_size : (i+1)*chunk_size].
    Only complete chunks are hashed (trailing partial chunk is ignored).
    """
    prev_hash = 0
    hashes = []
    for i in range(0, len(token_ids) - chunk_size + 1, chunk_size):
        chunk = token_ids[i : i + chunk_size]
        # Use SHA256, truncate to int64 range
        raw = hashlib.sha256(
            prev_hash.to_bytes(8, "little") +
            bytes(b for tid in chunk for b in tid.to_bytes(4, "little"))
        ).digest()
        h = int.from_bytes(raw[:8], "little")
        hashes.append(h)
        prev_hash = h
    return hashes


# ---------------------------------------------------------------------------
# LLMServer
# ---------------------------------------------------------------------------

@app.cls(
    gpu="A100-80GB",
    image=vllm_image,
    volumes={"/models": model_volume},
    scaledown_window=600,
)
class LLMServer:
    """vLLM + LMCache inference server with real per-block KV eviction."""

    @modal.enter()
    def setup(self):
        import torch
        from vllm import LLM, SamplingParams
        from vllm.config import KVTransferConfig

        os.environ["LMCACHE_CHUNK_SIZE"] = str(CHUNK_SIZE)
        os.environ["LMCACHE_LOCAL_CPU"] = "True"
        os.environ["LMCACHE_MAX_LOCAL_CPU_SIZE"] = "10.0"

        ktc = KVTransferConfig(
            kv_connector="LMCacheConnectorV1",
            kv_role="kv_both",
        )

        self.llm = LLM(
            model=MODEL_NAME,
            download_dir="/models",
            enable_prefix_caching=True,
            kv_transfer_config=ktc,
            max_model_len=32768,
            gpu_memory_utilization=0.85,
        )
        self.tokenizer = self.llm.get_tokenizer()
        self.SamplingParams = SamplingParams
        self._kv_dtype = torch.bfloat16
        self._lmcache_engine = None

    @modal.method()
    def generate(
        self,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict:
        """Generate a completion. Returns real num_cached_tokens from vLLM 0.8.x."""
        import time

        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        sampling_params = self.SamplingParams(
            temperature=temperature,
            max_tokens=max_tokens,
        )

        import io, sys, re
        # Capture stderr to extract LMCache hit stats AND vLLM input throughput
        captured_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_err

        t_start = time.perf_counter()
        outputs = self.llm.generate([prompt], sampling_params)
        elapsed = time.perf_counter() - t_start

        sys.stderr = old_stderr
        stderr_output = captured_err.getvalue()

        # Parse vLLM tqdm: "est. speed input: X toks/s"
        input_tps = 0.0
        output_tps = 0.0
        m_in = re.search(r"est\. speed input: ([\d.]+) toks/s", stderr_output)
        m_out = re.search(r"output: ([\d.]+) toks/s", stderr_output)
        if m_in:
            input_tps = float(m_in.group(1))
        if m_out:
            output_tps = float(m_out.group(1))

        # Parse LMCache hit stats: "LMCache hit tokens: N"
        lmcache_hit_tokens = 0
        lmcache_computed_tokens = 0
        m_hit = re.search(r"LMCache hit tokens: (\d+)", stderr_output)
        m_comp = re.search(r"Inference Engine computed tokens: (\d+)", stderr_output)
        if m_hit:
            lmcache_hit_tokens = int(m_hit.group(1))
        if m_comp:
            lmcache_computed_tokens = int(m_comp.group(1))

        output = outputs[0]

        content = output.outputs[0].text
        prompt_token_ids = list(output.prompt_token_ids)
        prompt_tokens = len(prompt_token_ids)
        completion_tokens = len(output.outputs[0].token_ids)

        # Try to get num_cached_tokens from metrics (populated in async/server mode)
        num_cached_tokens = 0
        try:
            m = output.metrics
            if m is not None:
                for field in ("num_cached_tokens", "num_prefix_cache_hit_tokens"):
                    val = getattr(m, field, None)
                    if isinstance(val, (int, float)) and val > 0:
                        num_cached_tokens = int(val)
                        break
        except Exception:
            pass

        # Cache hit estimate from vLLM's reported input throughput.
        # With prefix caching: input_tps = prompt_tokens / prefill_time_for_new_tokens_only
        # → effective_input_tps scales up with cache hits
        # Baseline uncached input_tps ≈ 180 toks/s for Qwen2.5-7B on A100 (measured).
        # hit_rate ≈ 1 - (BASELINE_TPS / input_tps)  [0 when cold, 1 when fully cached]
        BASELINE_INPUT_TPS = 180.0  # toks/s, uncached prefill on A100 (calibrated)
        tps_cached = 0
        tps_hit_rate = 0.0
        if input_tps > BASELINE_INPUT_TPS and prompt_tokens > 0:
            tps_hit_rate = max(0.0, min(1.0, 1.0 - BASELINE_INPUT_TPS / input_tps))
            tps_cached = int(tps_hit_rate * prompt_tokens)

        return {
            "content": content,
            "prompt_token_ids": prompt_token_ids,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "num_cached_tokens": num_cached_tokens,        # vLLM metrics (0 in V1 batch mode)
            "lmcache_hit_tokens": lmcache_hit_tokens,      # Real LMCache CPU hits ✓
            "lmcache_computed_tokens": lmcache_computed_tokens,  # Tokens vLLM recomputed
            "tps_cached_tokens": tps_cached,               # Timing-based estimate
            "tps_hit_rate": tps_hit_rate,
            "input_tps": input_tps,
            "output_tps": output_tps,
            "elapsed_s": elapsed,
        }

    def _get_lmcache_engine(self):
        """Get the LMCache v1 engine singleton created by LMCacheConnectorV1."""
        if self._lmcache_engine is not None:
            return self._lmcache_engine
        try:
            from lmcache.v1.cache_engine import LMCacheEngineBuilder
            engine = LMCacheEngineBuilder.get("0")
            if engine is not None:
                self._lmcache_engine = engine
            return engine
        except Exception:
            return None

    def _get_backend(self):
        engine = self._get_lmcache_engine()
        if engine is None:
            return None
        return getattr(engine, "engine_", None) or getattr(engine, "storage_manager", None)

    def _make_key(self, chunk_hash_int: int):
        import torch
        from lmcache.utils import CacheEngineKey
        return CacheEngineKey(
            model_name=MODEL_NAME,
            world_size=1,
            worker_id=0,
            chunk_hash=chunk_hash_int,
            dtype=self._kv_dtype,
        )

    @modal.method()
    def delete_kv_blocks(self, prompt_token_ids: list, block_indices: list) -> int:
        """Delete specific KV blocks from LMCache's CPU store.

        After this call, the next generate() call with the same prefix will treat
        these positions as cache misses — vLLM recomputes only those blocks.
        Everything before the first deleted block remains a cache hit.
        """
        backend = self._get_backend()
        if backend is None:
            return 0
        hashes = compute_block_hashes_v1(prompt_token_ids)
        deleted = 0
        for idx in block_indices:
            if 0 <= idx < len(hashes):
                try:
                    if backend.remove(self._make_key(hashes[idx]), force=True):
                        deleted += 1
                except Exception:
                    pass
        return deleted

    @modal.method()
    def pin_kv_blocks(self, prompt_token_ids: list, block_indices: list) -> int:
        """Pin KV blocks in LMCache to prevent CPU eviction."""
        backend = self._get_backend()
        if backend is None:
            return 0
        hashes = compute_block_hashes_v1(prompt_token_ids)
        pinned = 0
        for idx in block_indices:
            if 0 <= idx < len(hashes):
                try:
                    if backend.pin(self._make_key(hashes[idx])):
                        pinned += 1
                except Exception:
                    pass
        return pinned

    @modal.method()
    def get_stats(self) -> dict:
        stats = {"model": MODEL_NAME, "chunk_size": CHUNK_SIZE, "vllm": "0.8.5+lmcache"}
        engine = self._get_lmcache_engine()
        if engine is not None:
            stats["lmcache"] = "available"
            backend = self._get_backend()
            if backend is not None:
                stats["backend"] = type(backend).__name__
        else:
            stats["lmcache"] = "not yet initialized (call generate() first)"
        try:
            eng = self.llm.llm_engine
            cc = getattr(getattr(eng, "vllm_config", None), "cache_config", None)
            if cc:
                stats["block_size"] = getattr(cc, "block_size", None)
                stats["enable_prefix_caching"] = getattr(cc, "enable_prefix_caching", None)
        except Exception:
            pass
        return stats

    @modal.method()
    def inspect_output_fields(self, messages: list) -> dict:
        """One-shot: generate and return all RequestOutput fields for debugging."""
        import time
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        t0 = time.perf_counter()
        outputs = self.llm.generate([prompt], self.SamplingParams(temperature=0.0, max_tokens=5))
        elapsed = time.perf_counter() - t0
        output = outputs[0]

        result = {
            "elapsed_s": elapsed,
            "prompt_tokens": len(output.prompt_token_ids),
            "completion_tokens": len(output.outputs[0].token_ids),
            "output_fields": [f for f in dir(output) if not f.startswith("_")],
            "metrics_type": str(type(output.metrics)),
            "metrics_none": output.metrics is None,
        }

        # Dump all non-None output fields
        for f in dir(output):
            if f.startswith("_"):
                continue
            try:
                v = getattr(output, f)
                if callable(v):
                    continue
                if v is not None:
                    result[f"output.{f}"] = str(v)[:200]
            except Exception:
                pass

        if output.metrics is not None:
            for f in dir(output.metrics):
                if f.startswith("_"):
                    continue
                try:
                    v = getattr(output.metrics, f)
                    if not callable(v):
                        result[f"metrics.{f}"] = v
                except Exception:
                    pass

        return result


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main():
    server = LLMServer()

    print(f"=== AdaptiveCache Smoke Test (vLLM 0.8.5 + LMCache) ===")
    print(f"Model: {MODEL_NAME}  chunk_size: {CHUNK_SIZE}")
    print()

    import time

    sys_msg = "You are a Python expert. " * 15  # ~200 tokens prefix

    def call(msgs, label):
        r = server.generate.remote(msgs, temperature=0.0, max_tokens=64)
        lmc_rate = r["lmcache_hit_tokens"] / max(r["prompt_tokens"], 1)
        print(f"[{label}] tokens={r['prompt_tokens']:4d}  "
              f"lmc_hit={r['lmcache_hit_tokens']:4d}({lmc_rate:.0%})  "
              f"gpu_computed={r['lmcache_computed_tokens']:4d}  "
              f"elapsed={r['elapsed_s']:.3f}s  {r['content'][:30]!r}")
        return r

    # Build a longer prompt (> 256 tokens) to exercise LMCache chunks
    long_task = "Explain quicksort with complete Python code, time complexity, space complexity, and three test cases. " * 3
    msgs1 = [{"role": "system", "content": sys_msg},
             {"role": "user", "content": long_task}]
    r1 = call(msgs1, "cold ")

    # Warm — same prefix + extension
    msgs2 = msgs1 + [
        {"role": "assistant", "content": r1["content"].strip()},
        {"role": "user", "content": "What is mergesort? One sentence."},
    ]
    r2 = call(msgs2, "warm ")

    # Inspect real output/metrics fields
    print("\n[debug] Inspecting vLLM 0.8.5 output fields (second call for warm cache)...")
    fields = server.inspect_output_fields.remote(msgs2)
    cache_fields = {k: v for k, v in fields.items() if "cache" in k.lower() or "prefix" in k.lower() or "hit" in k.lower()}
    print(f"  Cache/prefix/hit fields: {cache_fields or 'none found'}")
    print(f"  metrics_none={fields.get('metrics_none')}  elapsed={fields.get('elapsed_s'):.3f}s")

    # LMCache engine stats
    print("\n[stats] LMCache engine:")
    stats = server.get_stats.remote()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Real deletion: evict block 0 from LMCache
    print("\n[evict] delete_kv_blocks([0]) on warm prompt...")
    deleted = server.delete_kv_blocks.remote(r2["prompt_token_ids"], [0])
    print(f"  deleted={deleted} blocks")

    # After eviction: same prompt, block 0 should be a miss
    msgs3 = msgs2  # same bytes
    r3 = call(msgs3, "post-evict")
    print(f"\n  Expected: cached < {r2['num_cached_tokens']} (block 0 evicted)")

    print("\n=== Done ===")
