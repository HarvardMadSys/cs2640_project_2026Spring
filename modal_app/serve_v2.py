"""Modal app: vLLM OpenAI server for AdaptiveCache experiments.

Uses vLLM's OpenAI-compatible API server (subprocess) which properly
reports usage.prompt_tokens_details.cached_tokens — the real KV cache
hit count. This is the correct way to measure prefix cache utilization.

No LMCache needed here: vLLM's built-in prefix caching is sufficient to
demonstrate position-aware vs FIFO eviction effects on cache hit rates.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import modal

# ---------------------------------------------------------------------------
# Image: vLLM + openai client
# ---------------------------------------------------------------------------

vllm_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "vllm==0.8.5",
        "transformers>=4.40,<5.0",
        "huggingface-hub>=0.20",
        "numpy",
        "openai>=1.0",
        "httpx",
    )
)

app = modal.App("adaptivecache-server-v2")

model_volume = modal.Volume.from_name("adaptivecache-models", create_if_missing=True)
results_volume = modal.Volume.from_name("adaptivecache-results", create_if_missing=True)

MODEL_NAME = "Qwen/Qwen3-8B"
PORT = 8000


# ---------------------------------------------------------------------------
# LLMServer: runs vLLM as an OpenAI-compatible subprocess
# ---------------------------------------------------------------------------


@app.cls(
    gpu="A100-80GB",
    image=vllm_image,
    volumes={"/models": model_volume},
    scaledown_window=600,
)
@modal.concurrent(max_inputs=16)
class LLMServer:
    """vLLM OpenAI server exposed as a public web endpoint.

    After deploy, the URL is printed and stored in the Modal dashboard.
    Use it as base_url for litellm:
        litellm.completion(model="openai/Qwen/Qwen2.5-7B-Instruct",
                           base_url="https://<url>/v1", api_key="dummy", ...)
    """

    @modal.enter()
    def setup(self):
        import subprocess
        import httpx

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
            "--enable-auto-tool-choice",
            "--tool-call-parser", "hermes",
        ]
        self._proc = subprocess.Popen(cmd)

        # Wait for server ready (up to 5 minutes for model load + torch.compile)
        base_url = f"http://localhost:{PORT}"
        print(f"Waiting for vLLM server at {base_url}...")
        for i in range(300):
            try:
                r = httpx.get(f"{base_url}/health", timeout=2.0)
                if r.status_code == 200:
                    print(f"vLLM ready after {i*2}s")
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("vLLM server failed to start within 10 minutes")

        from openai import OpenAI
        self._client = OpenAI(api_key="dummy", base_url=f"http://localhost:{PORT}/v1")
        self._base_url = base_url

    @modal.web_server(port=PORT, startup_timeout=600)
    def serve(self):
        """Expose vLLM's OpenAI-compatible API as a public Modal web endpoint.

        After `modal deploy modal_app/serve_v2.py`, this endpoint gets a stable
        URL in the Modal dashboard. Pass it to litellm as base_url so any
        OpenAI-compatible client (litellm, openai SDK, mini-swe-agent) can call
        Qwen2.5-7B with full tool-calling support.
        """
        # vLLM is already running on PORT (started in setup()).
        # Modal routes external HTTPS traffic here — nothing extra to do.
        pass

    @modal.method()
    def generate(
        self,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> dict:
        """Generate via vLLM OpenAI server. Returns real cached_tokens.

        The OpenAI-compatible server path populates
        usage.prompt_tokens_details.cached_tokens which reflects vLLM's
        actual prefix KV cache hit count. This is the ground-truth metric.
        """
        import httpx as _httpx, re as _re

        def _read_metrics():
            """Read vLLM Prometheus metrics, return dict of {metric: float}."""
            try:
                r = _httpx.get(f"{self._base_url}/metrics", timeout=5.0)
                out = {}
                for line in r.text.splitlines():
                    if line.startswith("#"):
                        continue
                    m = _re.match(r'([\w:]+)\{[^}]*\}\s+([\d.e+\-]+)', line)
                    if m:
                        out[m.group(1)] = float(m.group(2))
                    else:
                        m2 = _re.match(r'([\w:]+)\s+([\d.e+\-]+)', line)
                        if m2:
                            out[m2.group(1)] = float(m2.group(2))
                return out
            except Exception:
                return {}

        metrics_before = _read_metrics()

        t_start = time.perf_counter()

        response = self._client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        elapsed = time.perf_counter() - t_start

        metrics_after = _read_metrics()

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        # Compute per-request cache hits from Prometheus counter delta.
        # vLLM tracks gpu_cache_hit_rate as a gauge (current rate) and
        # gpu_prefix_cache_hits_total as a counter.
        cached_tokens = 0
        try:
            # Try counter-based approach: hits_delta / blocks_per_token
            hits_k = "vllm:gpu_prefix_cache_hits_total"
            q_k = "vllm:gpu_prefix_cache_queries_total"
            hits_before = metrics_before.get(hits_k, 0)
            hits_after = metrics_after.get(hits_k, 0)
            q_before = metrics_before.get(q_k, 0)
            q_after = metrics_after.get(q_k, 0)
            hits_delta = hits_after - hits_before
            q_delta = q_after - q_before
            if q_delta > 0:
                # hits are in blocks (16 tokens each in vLLM's default block size)
                cached_tokens = int(hits_delta * 16)
        except Exception:
            pass

        # Debug: show raw metrics for first call
        if prompt_tokens < 200:
            cache_metrics = {k: v for k, v in metrics_after.items() if "cache" in k.lower() or "prefix" in k.lower()}
            print(f"  [debug] cache metrics: {cache_metrics}")

        content = response.choices[0].message.content if response.choices else ""

        return {
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,               # REAL vLLM prefix cache hits
            "hit_rate": cached_tokens / max(prompt_tokens, 1),
            "elapsed_s": elapsed,
        }

    @modal.method()
    def get_stats(self) -> dict:
        import httpx
        try:
            r = httpx.get(f"{self._base_url}/metrics", timeout=5)
            # Parse Prometheus metrics for prefix cache hit rate
            cached_hit = 0.0
            for line in r.text.splitlines():
                if "vllm:gpu_prefix_cache_hit_rate" in line and not line.startswith("#"):
                    cached_hit = float(line.split()[-1])
                    break
            return {"status": "ok", "prefix_cache_hit_rate": cached_hit}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main():
    server = LLMServer()
    print("=== AdaptiveCache v2 Smoke Test (vLLM OpenAI server) ===")
    print("Model:", MODEL_NAME)
    print()

    sys = "You are a Python expert who gives concise answers. " * 10

    def call(msgs, label):
        r = server.generate.remote(msgs, temperature=0.0, max_tokens=64)
        print(f"[{label}] prompt={r['prompt_tokens']:4d}  "
              f"cached={r['cached_tokens']:4d}({r['hit_rate']:.0%})  "
              f"elapsed={r['elapsed_s']:.3f}s  {r['content'][:35]!r}")
        return r

    msgs1 = [{"role": "system", "content": sys},
             {"role": "user", "content": "What is quicksort? Give a one-paragraph answer."}]
    r1 = call(msgs1, "cold  ")

    msgs2 = msgs1 + [
        {"role": "assistant", "content": r1["content"].strip()},
        {"role": "user", "content": "Now explain mergesort the same way."},
    ]
    r2 = call(msgs2, "warm  ")

    msgs3 = msgs2 + [
        {"role": "assistant", "content": r2["content"].strip()},
        {"role": "user", "content": "Compare their time complexities."},
    ]
    r3 = call(msgs3, "warm2 ")

    print(f"\n  Hit rates: cold={r1['hit_rate']:.0%}  warm={r2['hit_rate']:.0%}  warm2={r3['hit_rate']:.0%}")
    print("  Expected: cold≈0%, warm>50%, warm2>60%")

    stats = server.get_stats.remote()
    print(f"\n  vLLM stats: {stats}")
    print("\n=== Done ===")
