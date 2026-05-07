"""
Pre-download HF model weights into the local HF cache so vLLM doesn't
stream them at load time. Skips .bin if safetensors are present.
"""

from huggingface_hub import snapshot_download

MODELS = [
    "Qwen/Qwen2.5-3B-Instruct",
    "meta-llama/Llama-3.2-3B-Instruct",
]

ALLOW = ["*.safetensors", "*.json", "*.txt", "tokenizer*", "*.model"]
IGNORE = ["*.bin", "*.pt", "*.msgpack", "*.h5", "*.onnx", "original/*"]


def main():
    for m in MODELS:
        print(f"\n[pull] {m}")
        path = snapshot_download(
            repo_id=m,
            allow_patterns=ALLOW,
            ignore_patterns=IGNORE,
        )
        print(f"[pull] -> {path}")


if __name__ == "__main__":
    main()
