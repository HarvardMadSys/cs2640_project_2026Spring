#!/usr/bin/env bash
# Interleave the chutes traces referenced by the Makefile's TRACES_CHUTES list
# onto a 1-day period, keeping the first 14 days of each source trace. Output
# files are written next to the inputs in CHUTES_DIR with a `_14din1d` suffix.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHUTES_DIR="/netscratch/shared/juncheng/2026_chutes_requests/per_model_hash"
RANGE="14d"
PERIOD="1d"
SUFFIX="_14din1d"

TRACES=(
    Qwen_Qwen3-14B_hash16
    deepseek-ai_DeepSeek-V3_2-TEE_hash16
    moonshotai_Kimi-K2_5-TEE_hash16
    zai-org_GLM-5-TEE_hash16
)

for trace in "${TRACES[@]}"; do
    in_file="${CHUTES_DIR}/${trace}.jsonl"
    out_file="${CHUTES_DIR}/${trace}${SUFFIX}.jsonl"
    echo "=== ${trace} ==="
    python3 "${SCRIPT_DIR}/interleave_trace.py" \
        "${in_file}" "${out_file}" \
        --range "${RANGE}" --period "${PERIOD}"
done
