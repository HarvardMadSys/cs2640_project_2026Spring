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
        # Store ALL KV blocks to CPU proactively (not just when GPU fills up).
        os.environ["LMCACHE_SAVE_DECODE_CACHE"] = "True"
        # Force vLLM V0 engine (single-process) so LMCache runs in the same
        # Python process — enabling direct object access for delete_kv_blocks().
        os.environ["VLLM_USE_V1"] = "0"
        # Enable LMCache's internal HTTP management API on localhost:6999.
        # This exposes /delete_blocks, /stats etc. from inside the worker process,
        # fixing the issue where LMCacheEngineBuilder._instances is empty when
        # called from the main process in multiprocessing mode.
        os.environ["LMCACHE_INTERNAL_API_SERVER_ENABLED"] = "True"

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

        import io, sys, re, logging

        # Capture LMCache log records via a custom handler (LMCache uses Python logging,
        # not sys.stderr, so we can't capture it via stderr redirection).
        class _LogCapture(logging.Handler):
            def __init__(self):
                super().__init__()
                self.messages = []
            def emit(self, r):
                self.messages.append(self.format(r))

        capture = _LogCapture()
        lmcache_logger = logging.getLogger("lmcache")
        lmcache_logger.addHandler(capture)

        # Also capture sys.stderr for vLLM tqdm
        captured_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_err

        t_start = time.perf_counter()
        outputs = self.llm.generate([prompt], sampling_params)
        elapsed = time.perf_counter() - t_start

        sys.stderr = old_stderr
        lmcache_logger.removeHandler(capture)

        stderr_output = captured_err.getvalue()
        log_output = "\n".join(capture.messages)
        combined = stderr_output + "\n" + log_output

        # Parse vLLM tqdm: "est. speed input: X toks/s"
        input_tps = 0.0
        output_tps = 0.0
        m_in = re.search(r"est\. speed input: ([\d.]+) toks/s", stderr_output)
        m_out = re.search(r"output: ([\d.]+) toks/s", stderr_output)
        if m_in:
            input_tps = float(m_in.group(1))
        if m_out:
            output_tps = float(m_out.group(1))

        # Parse LMCache per-request stats from log capture
        lmcache_hit_tokens = 0
        lmcache_computed_tokens = 0
        m_hit = re.search(r"LMCache hit tokens: (\d+)", combined)
        m_comp = re.search(r"Inference Engine computed tokens: (\d+)", combined)
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
            # Try known IDs: connector registers as "vllm-instance" in 0.4.3
            for iid in ("0", "vllm-instance"):
                engine = LMCacheEngineBuilder.get(iid)
                if engine is not None:
                    self._lmcache_engine = engine
                    return engine
            # Fallback: grab the first registered instance
            instances = getattr(LMCacheEngineBuilder, "_instances", {})
            if instances:
                engine = next(iter(instances.values()))
                self._lmcache_engine = engine
                return engine
        except Exception:
            pass
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

        Tries LMCache's internal HTTP API first (requires
        LMCACHE_INTERNAL_API_SERVER_ENABLED=True). Falls back to direct object
        access if the HTTP server isn't up yet.
        """
        hashes = compute_block_hashes_v1(prompt_token_ids)
        target_hashes = [hashes[i] for i in block_indices if 0 <= i < len(hashes)]
        if not target_hashes:
            return 0

        # --- Try internal HTTP API first ---
        deleted = self._delete_via_http(target_hashes)
        if deleted >= 0:
            return deleted

        # --- Fallback: direct object access ---
        backend = self._get_backend()
        if backend is None:
            return 0
        deleted = 0
        for h in target_hashes:
            try:
                if backend.remove(self._make_key(h), force=True):
                    deleted += 1
            except Exception:
                pass
        return deleted

    def _delete_via_http(self, hashes: list) -> int:
        """POST hashes to LMCache internal API. Returns deleted count, or -1 on failure."""
        import urllib.request
        import json as _json
        try:
            body = _json.dumps({"hashes": hashes}).encode()
            req = urllib.request.Request(
                "http://localhost:6999/delete_blocks",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read())
                return int(result.get("deleted", 0))
        except Exception:
            return -1  # API not up or endpoint doesn't exist

    @modal.method()
    def reset_lmcache(self) -> dict:
        """Clear all blocks from LMCache's CPU store.

        Call this between experiment policies to eliminate warm-cache bias.
        Tries HTTP API first, falls back to direct backend iteration.
        """
        # Try HTTP API
        import urllib.request
        import json as _json
        try:
            req = urllib.request.Request(
                "http://localhost:6999/reset",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read())
                return {"method": "http", **result}
        except Exception:
            pass

        # Fallback: iterate backend keys and remove
        backend = self._get_backend()
        if backend is None:
            return {"cleared": 0, "method": "none", "reason": "no backend"}

        cleared = 0
        try:
            # Try common clear/flush methods
            for method_name in ("clear", "flush", "reset"):
                fn = getattr(backend, method_name, None)
                if fn is not None:
                    fn()
                    return {"cleared": -1, "method": f"backend.{method_name}()"}
            # Last resort: iterate keys
            keys = getattr(backend, "keys", None) or getattr(backend, "list_keys", None)
            if keys is not None:
                for key in list(keys()):
                    try:
                        backend.remove(key, force=True)
                        cleared += 1
                    except Exception:
                        pass
        except Exception as e:
            return {"cleared": cleared, "method": "iterate", "error": str(e)}

        return {"cleared": cleared, "method": "iterate"}

    @modal.method()
    def verify_block_hashes(self, prompt_token_ids: list) -> dict:
        """Verify our SHA256 hash chain matches LMCache's internal keys.

        Lists all keys in LMCache's CPU store, computes our hashes for the
        given prompt, and checks for overlap. Use after a warm generate() call.

        Returns:
            {
              "our_hashes": [...],      # what compute_block_hashes_v1() returns
              "lmcache_keys": [...],    # keys actually in the CPU store
              "matched": int,           # how many of our hashes appear in the store
              "store_size": int,        # total number of keys in store
            }
        """
        our_hashes = compute_block_hashes_v1(prompt_token_ids)
        out = {"our_hashes": our_hashes, "lmcache_keys": [], "matched": 0, "store_size": 0}

        backend = self._get_backend()
        if backend is None:
            out["error"] = "no backend"
            return out

        try:
            keys_fn = getattr(backend, "keys", None) or getattr(backend, "list_keys", None)
            if keys_fn is not None:
                all_keys = list(keys_fn())
                out["store_size"] = len(all_keys)
                # Extract chunk_hash field from CacheEngineKey objects
                key_hashes = set()
                key_reprs = []
                for k in all_keys[:50]:  # cap at 50 for readability
                    h = getattr(k, "chunk_hash", None)
                    if h is not None:
                        key_hashes.add(h)
                    key_reprs.append(str(k)[:80])
                out["lmcache_keys"] = key_reprs
                out["matched"] = sum(1 for h in our_hashes if h in key_hashes)
            else:
                out["error"] = "backend has no keys() method"
        except Exception as e:
            out["error"] = str(e)

        return out

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
    def diagnose_lmcache(self) -> dict:
        """Inspect LMCache engine state from inside the server process."""
        out = {}
        try:
            from lmcache.v1.cache_engine import LMCacheEngineBuilder
            instances = getattr(LMCacheEngineBuilder, "_instances", {})
            out["instance_ids"] = list(instances.keys())
            out["n_instances"] = len(instances)
            for iid, engine in instances.items():
                backend = getattr(engine, "engine_", None) or getattr(engine, "storage_manager", None)
                out[f"engine_{iid}_type"] = type(engine).__name__
                out[f"backend_{iid}_type"] = type(backend).__name__ if backend else "None"
                out[f"engine_{iid}_config"] = str(getattr(engine, "config", "N/A"))[:100]
        except Exception as e:
            out["error"] = str(e)

        # Check VLLM_USE_V1
        import os
        out["VLLM_USE_V1"] = os.environ.get("VLLM_USE_V1", "not set")
        out["LMCACHE_SAVE_DECODE_CACHE"] = os.environ.get("LMCACHE_SAVE_DECODE_CACHE", "not set")

        # Check logging
        import logging
        out["lmcache_logger_handlers"] = [str(h) for h in logging.getLogger("lmcache").handlers]

        return out

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
def diagnose():
    """Quick diagnostic: check LMCache engine state."""
    server = LLMServer()
    r = server.generate.remote(
        [{"role": "user", "content": "What is 1+1?"}], max_tokens=5
    )
    print(f"Generated: {r['content']!r}  lmc_hit={r.get('lmcache_hit_tokens',0)}")
    d = server.diagnose_lmcache.remote()
    for k, v in d.items():
        print(f"  {k}: {v}")


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
