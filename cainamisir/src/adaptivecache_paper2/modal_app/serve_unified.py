"""Unified AdaptiveCache inference server.

vLLM OpenAI-compatible HTTP server + LMCache KV block management, all in one
Modal deployment. This replaces both serve.py and serve_v2.py.

Architecture:
  - vLLM runs as an OpenAI-compatible subprocess on port 8000.
  - LMCache integrates via the LMCacheConnectorV1 kv_connector plugin.
  - The LMCache internal HTTP API (port 6999) exposes block-level management.
  - The web_server endpoint exposes vLLM externally so litellm can call it
    with full tool-calling support.

All 4 experiment policies use the same server:
  none / fifo / adaptive  → call the web endpoint via litellm (no block ops)
  kv_adaptive             → same, PLUS calls delete_kv_blocks() after each step

Run:
    modal deploy modal_app/serve_unified.py
    # URL printed: https://vcainamisir--adaptivecache-unified-llmserver-serve.modal.run
"""

from __future__ import annotations

import hashlib
import os
import time

import modal

# ---------------------------------------------------------------------------
# Image: vLLM 0.8.5 + LMCache built from source (same as serve.py)
# ---------------------------------------------------------------------------

vllm_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-devel-ubuntu22.04",
        add_python="3.11",
    )
    .pip_install(
        "vllm==0.8.5",
        "transformers>=4.40,<5.0",
        "huggingface-hub>=0.20",
        "numpy",
        "openai>=1.0",
        "httpx",
        "tiktoken",
    )
    .apt_install("clang", "git")
    .add_local_file(
        "/Users/cnmsr/Projects/cacheKarpathy/modal_app/patch_lmcache.py",
        remote_path="/tmp/lmcache_patch.py",
        copy=True,
    )
    .run_commands(
        "pip install --upgrade setuptools setuptools-scm pip",
        "git clone --depth=1 --branch v0.4.3 https://github.com/LMCache/LMCache.git /tmp/lmcache_src || git clone --depth=1 https://github.com/LMCache/LMCache.git /tmp/lmcache_src",
        "python3 /tmp/lmcache_patch.py",
        "SETUPTOOLS_SCM_PRETEND_VERSION=0.4.3 TORCH_CUDA_ARCH_LIST='7.5;8.0;8.6' pip install /tmp/lmcache_src --no-build-isolation",
        # ABI shim for torch 2.6.0 unsigned int → int
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
# Modal app
# ---------------------------------------------------------------------------

app = modal.App("adaptivecache-unified")

model_volume = modal.Volume.from_name("adaptivecache-models", create_if_missing=True)
results_volume = modal.Volume.from_name("adaptivecache-results", create_if_missing=True)

MODEL_NAME = "Qwen/Qwen3-30B-A3B"
PORT = 8000
LMCACHE_API_PORT = 6999
CHUNK_SIZE = 256  # Must match LMCACHE_CHUNK_SIZE


# ---------------------------------------------------------------------------
# Block hash computation (matches LMCache v1 hash scheme)
# ---------------------------------------------------------------------------

def compute_block_hashes(token_ids: list, chunk_size: int = CHUNK_SIZE) -> list:
    """Compute LMCache v1 rolling hash chain for a token sequence."""
    prev_hash = 0
    hashes = []
    for i in range(0, len(token_ids) - chunk_size + 1, chunk_size):
        chunk = token_ids[i: i + chunk_size]
        raw = hashlib.sha256(
            prev_hash.to_bytes(8, "little") +
            bytes(b for tid in chunk for b in tid.to_bytes(4, "little"))
        ).digest()
        h = int.from_bytes(raw[:8], "little")
        hashes.append(h)
        prev_hash = h
    return hashes


# ---------------------------------------------------------------------------
# Unified LLM Server
# ---------------------------------------------------------------------------

@app.cls(
    gpu="A100-80GB",
    image=vllm_image,
    volumes={"/models": model_volume},
    scaledown_window=600,
    max_containers=1,   # vLLM batches internally — only 1 GPU needed
)
@modal.concurrent(max_inputs=100)  # allow many requests to queue for the single GPU
class LLMServer:
    """vLLM + LMCache unified inference server.

    Exposes:
    - web_server on PORT: OpenAI-compatible HTTP API (tool calls, litellm)
    - delete_kv_blocks(): LMCache block deletion for kv_adaptive policy
    - reset_lmcache(): clear all blocks between experiment policies
    - get_stats(): cache hit rates and engine state
    """

    @modal.enter()
    def setup(self):
        import subprocess
        import httpx

        # LMCache env vars — must be set before vLLM subprocess starts
        os.environ["LMCACHE_CHUNK_SIZE"] = str(CHUNK_SIZE)
        os.environ["LMCACHE_LOCAL_CPU"] = "True"
        os.environ["LMCACHE_MAX_LOCAL_CPU_SIZE"] = "10.0"
        os.environ["LMCACHE_SAVE_DECODE_CACHE"] = "True"
        os.environ["LMCACHE_INTERNAL_API_SERVER_ENABLED"] = "True"
        # Let vLLM choose engine mode — V1 is default in 0.8.5
        # The internal HTTP API (port 6999) handles block management regardless
        os.environ.pop("VLLM_USE_V1", None)

        cmd = [
            "python", "-m", "vllm.entrypoints.openai.api_server",
            "--model", MODEL_NAME,
            "--download-dir", "/models",
            "--enable-prefix-caching",
            "--max-model-len", "40960",
            "--gpu-memory-utilization", "0.90",
            "--port", str(PORT),
            "--host", "0.0.0.0",
            "--disable-log-requests",
            "--enable-prompt-tokens-details",  # exposes cached_tokens in usage
            "--enable-auto-tool-choice",
            "--tool-call-parser", "hermes",
            # LMCache connector (JSON format required for OpenAI server CLI)
            "--kv-transfer-config",
            '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}',
        ]

        self._proc = subprocess.Popen(cmd)
        self._base_url = f"http://localhost:{PORT}"
        self._lmcache_api = f"http://localhost:{LMCACHE_API_PORT}"

        print(f"Waiting for vLLM+LMCache server...")
        for i in range(300):
            try:
                r = httpx.get(f"{self._base_url}/health", timeout=2.0)
                if r.status_code == 200:
                    print(f"Server ready after {i*2}s")
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Server failed to start within 10 minutes")

        from openai import OpenAI
        self._client = OpenAI(api_key="dummy", base_url=f"{self._base_url}/v1")

    @modal.web_server(port=PORT, startup_timeout=600)
    def serve(self):
        """Public OpenAI-compatible endpoint. litellm points here."""
        pass

    # ------------------------------------------------------------------
    # KV block management (for kv_adaptive policy)
    # ------------------------------------------------------------------

    @modal.method()
    def delete_kv_blocks(self, prompt_token_ids: list, block_indices: list) -> int:
        """Delete specific KV blocks from LMCache CPU store.

        Tries LMCache internal HTTP API first (port 6999), falls back to
        direct Python object access.
        """
        import urllib.request, json as _json

        hashes = compute_block_hashes(prompt_token_ids)
        target_hashes = [hashes[i] for i in block_indices if 0 <= i < len(hashes)]
        if not target_hashes:
            return 0

        # Try HTTP API
        try:
            body = _json.dumps({"hashes": target_hashes}).encode()
            req = urllib.request.Request(
                f"{self._lmcache_api}/delete_blocks",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read())
                return int(result.get("deleted", 0))
        except Exception:
            pass

        return 0

    @modal.method()
    def reset_lmcache(self) -> dict:
        """Clear all LMCache blocks. Call between experiment policies."""
        import urllib.request, json as _json
        try:
            req = urllib.request.Request(
                f"{self._lmcache_api}/reset",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return {"method": "http", **_json.loads(resp.read())}
        except Exception:
            pass
        return {"cleared": 0, "method": "unavailable"}

    @modal.method()
    def get_stats(self) -> dict:
        import httpx
        try:
            r = httpx.get(f"{self._base_url}/metrics", timeout=5)
            hit_rate = 0.0
            for line in r.text.splitlines():
                if "vllm:gpu_prefix_cache_hit_rate" in line and not line.startswith("#"):
                    hit_rate = float(line.split()[-1])
                    break
            return {"status": "ok", "model": MODEL_NAME, "prefix_cache_hit_rate": hit_rate,
                    "lmcache": "enabled", "chunk_size": CHUNK_SIZE}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @modal.method()
    def verify_block_hashes(self, prompt_token_ids: list) -> dict:
        """Diagnostic: compare our computed hashes to LMCache's stored keys."""
        import urllib.request, json as _json
        our_hashes = compute_block_hashes(prompt_token_ids)
        try:
            req = urllib.request.Request(f"{self._lmcache_api}/list_blocks", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                stored = _json.loads(resp.read())
                stored_hashes = set(stored.get("hashes", []))
                matched = sum(1 for h in our_hashes if h in stored_hashes)
                return {"our_hashes": len(our_hashes), "store_size": len(stored_hashes), "matched": matched}
        except Exception as e:
            return {"our_hashes": our_hashes, "error": str(e)}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main():
    server = LLMServer()
    print(f"=== Unified Server Smoke Test: {MODEL_NAME} ===\n")

    msgs = [{"role": "system", "content": "You are a concise Python expert."},
            {"role": "user", "content": "What is a hash table?"}]

    # Warm up via Modal method (not HTTP) to check LMCache
    stats = server.get_stats.remote()
    print(f"Stats: {stats}")
    print("\nDone — deploy and use the web endpoint URL for experiments.")
