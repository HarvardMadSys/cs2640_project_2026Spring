"""Backend-agnostic LLM client with cache hit logging.

Supports SGLang, Anthropic, and any OpenAI-compatible API.
Extracts prefix cache statistics from response usage objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI


@dataclass
class CacheStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        if self.prompt_tokens == 0:
            return 0.0
        return self.cached_tokens / self.prompt_tokens


def create_client(
    backend: str = "sglang",
    base_url: str | None = None,
    api_key: str | None = None,
) -> OpenAI:
    """Create an OpenAI-compatible client for the specified backend.

    Backends:
        sglang:    Local SGLang server (default port 30000)
                   Start with: python -m sglang.launch_server \\
                       --model Qwen/Qwen2.5-7B-Instruct --port 30000
        anthropic: Anthropic API via OpenAI-compat proxy
        openai:    OpenAI API directly
    """
    defaults = {
        "sglang": ("http://localhost:30000/v1", "EMPTY"),
        "anthropic": ("https://api.anthropic.com/v1", None),
        "openai": ("https://api.openai.com/v1", None),
    }

    default_url, default_key = defaults.get(backend, defaults["sglang"])
    return OpenAI(
        base_url=base_url or default_url,
        api_key=api_key or default_key,
    )


def extract_cache_stats(response) -> CacheStats:
    """Extract cache statistics from an OpenAI-format response.

    Works with SGLang, Anthropic, and OpenAI responses.
    """
    usage = response.usage
    if usage is None:
        return CacheStats()

    stats = CacheStats(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )

    # SGLang: cached_tokens field
    if hasattr(usage, "cached_tokens"):
        stats.cached_tokens = usage.cached_tokens or 0
    # Anthropic: cache_read_input_tokens
    elif hasattr(usage, "cache_read_input_tokens"):
        stats.cached_tokens = usage.cache_read_input_tokens or 0
    # OpenAI: prompt_tokens_details.cached_tokens
    elif hasattr(usage, "prompt_tokens_details"):
        details = usage.prompt_tokens_details
        if details and hasattr(details, "cached_tokens"):
            stats.cached_tokens = details.cached_tokens or 0

    return stats
