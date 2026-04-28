#!/bin/bash
# One-shot creator for a sibling micromamba env that supports Qwen3.6.
#
# WHY this exists: the project's current `lora` env is pinned to
# `transformers==4.57.1` and `vllm==0.10.0`, neither of which know
# about the `qwen3_5` model_type. Upgrading `lora` in place would
# break the main agent's Paper 1 work mid-run. This script creates a
# parallel env named `lora-tf5` so Paper 2 work is independent.
#
# Run on a login node (no GPU needed for env creation).
# Time: ~5-15 minutes including pip resolve + torch/vllm download.

set -euo pipefail

ENV_NAME=${ENV_NAME:-lora-tf5}
PY_VER=${PY_VER:-3.11}

echo "[$(date)] creating micromamba env $ENV_NAME (python=$PY_VER)"

# Use the user's existing micromamba root.
MM=/n/home06/vcainamisir/micromamba/bin/micromamba
$MM create -n "$ENV_NAME" -c conda-forge -y python="$PY_VER" pip

PY=/n/home06/vcainamisir/micromamba/envs/$ENV_NAME/bin/python
$PY -m pip install --upgrade pip wheel

# Torch first; everything else pins against it.
$PY -m pip install \
    "torch>=2.7,<2.9" \
    --index-url https://download.pytorch.org/whl/cu126

# Then transformers (needs >= 5.6 for qwen3_5) and vllm (needs >= 0.18 for
# Qwen3_5ForConditionalGeneration + the hybrid KV cache manager).
$PY -m pip install \
    "transformers>=5.6.0,<6" \
    "huggingface_hub>=0.36" \
    "accelerate>=1.0" \
    "openai>=1.50" \
    sentencepiece tiktoken pillow safetensors

# vLLM last — it has the most aggressive deps. Note: vllm wheels
# generally pin a torch version; if pip backtracks, that's a sign we
# need a different vllm pin. Latest tested: 0.18.x for Qwen3_5.
$PY -m pip install "vllm>=0.18,<0.20"

echo "[$(date)] env $ENV_NAME ready."
echo "Activate: micromamba activate $ENV_NAME"
$PY -c "
import transformers, torch, vllm
print('  transformers:', transformers.__version__)
print('  torch:', torch.__version__)
print('  vllm:', vllm.__version__)
from transformers.models.auto.configuration_auto import CONFIG_MAPPING
print('  qwen3_5 in CONFIG_MAPPING:', 'qwen3_5' in CONFIG_MAPPING)
print('  qwen3_5_text in CONFIG_MAPPING:', 'qwen3_5_text' in CONFIG_MAPPING)
from vllm.model_executor.models.registry import ModelRegistry
print('  Qwen3_5ForConditionalGeneration in vllm:',
      'Qwen3_5ForConditionalGeneration' in ModelRegistry.get_supported_archs())
"
