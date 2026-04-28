"""Model adapters. All adapters expose the ChatModel interface so the
runner is provider-agnostic. cached_tokens semantics are normalized
across providers."""

from .base import ChatModel, ChatResponse
from .openai_compat import OpenAICompatible
from .anthropic_native import AnthropicNative


REGISTRY = {
    "openai_compat": OpenAICompatible,
    "anthropic": AnthropicNative,
}


def build_model(model_name: str, *, adapter: str = "auto", **kwargs) -> ChatModel:
    """Build a ChatModel by name. adapter='auto' picks based on model_name prefix.

    'hf_local' loads a model in-process via HuggingFace transformers — used
    for cluster-local runs without setting up an HTTP server.
    """
    if adapter == "auto":
        if model_name.startswith("anthropic/"):
            adapter = "anthropic"
        elif model_name.startswith("hf:") or model_name.startswith(("Qwen/", "meta-llama/", "microsoft/", "mistralai/")):
            adapter = "hf_local"
        else:
            adapter = "openai_compat"
    if adapter == "hf_local":
        from .hf_local import HFLocalModel
        actual = model_name.removeprefix("hf:")
        return HFLocalModel(model_name=actual, **kwargs)
    cls = REGISTRY[adapter]
    return cls(model_name=model_name, **kwargs)


__all__ = ["ChatModel", "ChatResponse", "OpenAICompatible", "AnthropicNative", "build_model", "REGISTRY"]
