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
    patched = content.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(patched)
    print(f"  OK: {description}")


# -------------------------------------------------------------------
# Patch 1: factory.py — generate a default engine_id when not set.
# Without this, the ZMQ transport creation crashes with AssertionError.
# -------------------------------------------------------------------
FACTORY = f"{SRC}/lmcache/v1/lookup_client/factory.py"

# Find the indentation of the assert by scanning the file
with open(FACTORY) as f:
    for line in f:
        if "assert metadata.engine_id is not None" in line:
            indent = len(line) - len(line.lstrip())
            IND = " " * indent
            break
    else:
        IND = "        "

patch_file(
    FACTORY,
    f"{IND}assert metadata.engine_id is not None",
    f"{IND}metadata.engine_id = metadata.engine_id or \"default-0\"  # compat-vllm085\n{IND}assert metadata.engine_id is not None",
    "factory.py: default engine_id when None",
)

# -------------------------------------------------------------------
# Patch 2: vllm_v1_adapter.py — graceful return when lookup_client is None.
# Without this, vLLM's scheduler crashes when asking for matched tokens.
# -------------------------------------------------------------------
ADAPTER = f"{SRC}/lmcache/integration/vllm/vllm_v1_adapter.py"

with open(ADAPTER) as f:
    for line in f:
        if "assert self.lookup_client is not None" in line:
            indent = len(line) - len(line.lstrip())
            IND2 = " " * indent
            break
    else:
        IND2 = "        "

patch_file(
    ADAPTER,
    f"{IND2}assert self.lookup_client is not None",
    (
        f"{IND2}if self.lookup_client is None:  # compat-vllm085: degraded mode\n"
        f"{IND2}    return 0, request_data\n"
        f"{IND2}assert self.lookup_client is not None"
    ),
    "vllm_v1_adapter.py: graceful None lookup_client",
)

print("All patches applied.")
