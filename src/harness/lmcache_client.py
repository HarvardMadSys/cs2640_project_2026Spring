"""Client for the AdaptiveCache Modal LLMServer.

Supports two modes:
- modal: calls Modal LLMServer functions remotely
- local: calls a local vLLM+LMCache server via OpenAI-compatible HTTP API

Usage:
    # Modal mode (default)
    client = LMCacheClient(mode="modal")
    result = client.generate(messages)

    # Local mode (for testing with a local server)
    client = LMCacheClient(mode="local", base_url="http://localhost:8000")
    result = client.generate(messages)
"""

from __future__ import annotations

from typing import List, Optional


class LMCacheClient:
    """Client for the AdaptiveCache LLMServer.

    Delegates to Modal for GPU inference (modal mode) or a local
    OpenAI-compatible endpoint (local mode). All block-level KV
    operations (delete, pin) are only meaningful in modal mode.
    """

    def __init__(self, mode: str = "modal", base_url: Optional[str] = None):
        """
        Args:
            mode: "modal" to use the Modal LLMServer, "local" for local HTTP server.
            base_url: Base URL for local mode (e.g. "http://localhost:8000").
        """
        self.mode = mode
        self.base_url = base_url
        self._server = None

    def _get_server(self):
        """Lazily get a stub for the deployed Modal LLMServer.

        Uses modal.Cls.from_name() so this works from any Modal function.
        App name defaults to "adaptivecache-unified" (the unified server),
        overridable via ADAPTIVECACHE_KV_SERVER_APP env var.
        """
        if self._server is None and self.mode == "modal":
            import modal, os
            app_name = os.environ.get("ADAPTIVECACHE_KV_SERVER_APP", "adaptivecache-unified")
            LLMServer = modal.Cls.from_name(app_name, "LLMServer")
            self._server = LLMServer()
        return self._server

    def generate(
        self,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict:
        """Generate a response.

        Returns:
            dict with keys: content, prompt_token_ids, prompt_tokens,
            completion_tokens, num_cached_tokens.
        """
        if self.mode == "modal":
            return self._get_server().generate.remote(
                messages, temperature=temperature, max_tokens=max_tokens
            )

        # Local OpenAI-compatible API
        import httpx

        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": "default",
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return {
            "content": choice["message"]["content"],
            "prompt_token_ids": [],  # Not available from OpenAI-compat API
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "num_cached_tokens": (
                usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
            ),
        }

    def delete_kv_blocks(
        self,
        prompt_token_ids: list,
        block_indices: list,
    ) -> int:
        """Delete specific KV cache blocks by chunk index.

        Returns:
            Number of blocks successfully deleted (0 in non-modal modes).
        """
        if self.mode == "modal":
            return self._get_server().delete_kv_blocks.remote(
                prompt_token_ids, block_indices
            )
        return 0  # No-op for non-modal modes

    def pin_kv_blocks(
        self,
        prompt_token_ids: list,
        block_indices: list,
    ) -> int:
        """Pin specific KV cache blocks to prevent eviction.

        Returns:
            Number of blocks successfully pinned (0 in non-modal modes).
        """
        if self.mode == "modal":
            return self._get_server().pin_kv_blocks.remote(
                prompt_token_ids, block_indices
            )
        return 0  # No-op for non-modal modes

    def reset_lmcache(self) -> dict:
        """Clear all LMCache blocks. Call between experiment policies."""
        if self.mode == "modal":
            return self._get_server().reset_lmcache.remote()
        return {}

    def get_stats(self) -> dict:
        """Return LMCache engine statistics."""
        if self.mode == "modal":
            return self._get_server().get_stats.remote()
        return {}
