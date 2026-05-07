"""Patch lmcache 0.4.3 source for vLLM 0.8.5 compatibility.

vLLM 0.9+ passes engine_id to the KV connector; vLLM 0.8.5 doesn't.
These patches allow lmcache to run in single-node degraded mode without engine_id.
"""
import re

SRC = "/tmp/lmcache_src"


def patch_file(path, old, new, description):
    with open(path) as f:
        content = f.read()
    if old not in content:
        print(f"  WARNING: pattern not found in {path}: {old!r}")
        return
    patched = content.replace(old, new)  # replace ALL occurrences
    with open(path, "w") as f:
        f.write(patched)
    print(f"  OK: {description}")


def patch_file_regex(path, pattern, replacement, description):
    with open(path) as f:
        content = f.read()
    patched, n = re.subn(pattern, replacement, content)
    if n == 0:
        print(f"  WARNING: pattern not found in {path}: {pattern!r}")
        return
    with open(path, "w") as f:
        f.write(patched)
    print(f"  OK: {description} ({n} replacements)")


# -------------------------------------------------------------------
# Patch 1: factory.py — replace ALL engine_id assertions with a
# fallback that generates a default engine_id when None.
# Covers both the top-level factory and _create_zmq_server_transport.
# -------------------------------------------------------------------
FACTORY = f"{SRC}/lmcache/v1/lookup_client/factory.py"

patch_file_regex(
    FACTORY,
    # Match any line containing the engine_id assertion (with any indentation)
    r"( *)(assert metadata\.engine_id is not None.*)",
    # Replace with: set default engine_id, then keep the assert
    r"\1metadata.engine_id = metadata.engine_id or 'default-engine-0'  # compat-vllm085\n\1\2",
    "factory.py: default engine_id for ALL assertions",
)

# -------------------------------------------------------------------
# Patch 2: vllm_v1_adapter.py — graceful return when lookup_client is None.
# Without this, vLLM's scheduler crashes when asking for matched tokens.
# -------------------------------------------------------------------
ADAPTER = f"{SRC}/lmcache/integration/vllm/vllm_v1_adapter.py"

patch_file_regex(
    ADAPTER,
    r"( *)(assert self\.lookup_client is not None.*)",
    r"\1if self.lookup_client is None:  # compat-vllm085: degraded mode\n\1    return 0, request_data\n\1\2",
    "vllm_v1_adapter.py: graceful None lookup_client",
)

print("All patches applied.")
