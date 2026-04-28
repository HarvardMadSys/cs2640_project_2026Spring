"""Model-agnostic tokenization.

Each model's tokenizer is identified by a string in pricing.yaml:
  - "tiktoken:<encoding>"     → openai's tiktoken
  - "hf:<repo_id>"            → transformers AutoTokenizer
  - "anthropic"               → anthropic SDK count_tokens (network call)

We cache tokenizer instances. Token counts are used for budget enforcement
and for computing the prefix-cache hit ceiling in replay; they don't have
to match the provider's billing tokenizer to the byte (the cliff is order-
of-magnitude, not basis-point).
"""

from __future__ import annotations

import functools
from typing import Iterable, List, Protocol


class Tokenizer(Protocol):
    def encode(self, text: str) -> List[int]: ...
    def count(self, text: str) -> int: ...


class _TiktokenAdapter:
    def __init__(self, encoding_name: str):
        import tiktoken
        self._enc = tiktoken.get_encoding(encoding_name)

    def encode(self, text: str) -> List[int]:
        return self._enc.encode(text, disallowed_special=())

    def count(self, text: str) -> int:
        return len(self.encode(text))


class _HFAdapter:
    def __init__(self, repo_id: str):
        from transformers import AutoTokenizer
        self._tok = AutoTokenizer.from_pretrained(repo_id, trust_remote_code=True)

    def encode(self, text: str) -> List[int]:
        return self._tok.encode(text, add_special_tokens=False)

    def count(self, text: str) -> int:
        return len(self.encode(text))


class _AnthropicAdapter:
    """Anthropic doesn't expose a local tokenizer; approximate with cl100k_base
    which is within ~5% in our spot checks. Override by setting
    ANTHROPIC_USE_NETWORK=1 to call the SDK's count_tokens (slow, billed as
    nothing but counts as a request)."""

    def __init__(self):
        import os
        self._network = os.environ.get("ANTHROPIC_USE_NETWORK") == "1"
        if self._network:
            import anthropic
            self._client = anthropic.Anthropic()
        else:
            import tiktoken
            self._enc = tiktoken.get_encoding("cl100k_base")

    def encode(self, text: str) -> List[int]:
        if self._network:
            # Network counter doesn't return ids; fake it for byte-prefix work
            return list(range(self.count(text)))
        return self._enc.encode(text, disallowed_special=())

    def count(self, text: str) -> int:
        if self._network:
            r = self._client.messages.count_tokens(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": text}],
            )
            return r.input_tokens
        return len(self._enc.encode(text, disallowed_special=()))


@functools.lru_cache(maxsize=16)
def get_tokenizer(spec: str) -> Tokenizer:
    """Return a Tokenizer for a spec string from pricing.yaml."""
    if spec.startswith("tiktoken:"):
        return _TiktokenAdapter(spec.split(":", 1)[1])
    if spec.startswith("hf:"):
        return _HFAdapter(spec.split(":", 1)[1])
    if spec == "anthropic":
        return _AnthropicAdapter()
    raise ValueError(f"Unknown tokenizer spec: {spec!r}")


def count_messages(messages: Iterable[dict], tokenizer: Tokenizer) -> int:
    """Approximate token count for a chat message list. Includes ~3 tokens
    of role/separator overhead per message which matches the OpenAI rule.

    If a message carries a `_token_count` field (set by the AC simulator's
    identity model), use it directly and skip tokenization of `content`."""
    total = 0
    for m in messages:
        total += 3
        if "_token_count" in m and m["_token_count"] is not None:
            total += int(m["_token_count"])
            # Still account for tool_calls / name when present, but skip content
            for k, v in m.items():
                if k in ("_token_count", "_msg_id", "role", "content", "tool_call_id"):
                    continue
                if isinstance(v, str):
                    total += tokenizer.count(v)
                elif isinstance(v, list):
                    import json
                    total += tokenizer.count(json.dumps(v))
            continue
        for v in m.values():
            if isinstance(v, str):
                total += tokenizer.count(v)
            elif isinstance(v, list):
                import json
                total += tokenizer.count(json.dumps(v))
    return total + 3  # priming tokens
