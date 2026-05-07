#!/bin/bash
#SBATCH --job-name=qwen36_vllm
#SBATCH --partition=gpu_requeue
#SBATCH --time=02:00:00
#SBATCH --gres=gpu:1
#SBATCH --constraint=a100
#SBATCH --cpus-per-task=8
#SBATCH --mem=80G
#SBATCH --output=/n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_vllm.%j.log

# Launch vLLM serving Qwen3.6-27B-FP8 in language-model-only mode with
# automatic prefix caching enabled, for the prefix-cache + tool-calling
# corruption probe.
#
# vLLM 0.10.0 (the version in the lora env) does NOT register
# Qwen3_5ForConditionalGeneration. This will fail unless vLLM is upgraded
# to >= 0.18 (where qwen3_5 / qwen3_next / hybrid Mamba models started to
# land). See the upstream design doc for the Hybrid KV Cache Manager:
# https://docs.vllm.ai/en/stable/design/hybrid_kv_cache_manager/

set -euo pipefail

export HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
PY=${PY:-/n/home06/vcainamisir/micromamba/envs/lora/bin/python}

PORT=${PORT:-9876}
MODEL=${MODEL:-Qwen/Qwen3.6-27B-FP8}

echo "[$(date)] launching vLLM serve on :$PORT for $MODEL"
nvidia-smi -L || true

# `--language-model-only` skips the vision tower (the same delete we do
# manually in HFLocalModel for transformers).
# `--enable-prefix-caching` is what we are *probing* — we want to test
# whether it corrupts tool calling on this hybrid model.
# Hybrid KV cache manager is auto-enabled when the model has both
# linear-attention and full-attention layers. As of the upstream design
# doc, prefix caching for Mamba "align" mode is "experimental".
exec $PY -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --enable-prefix-caching \
    --language-model-only \
    --max-model-len 65536 \
    --gpu-memory-utilization 0.90 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes
