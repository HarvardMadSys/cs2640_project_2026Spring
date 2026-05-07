#!/bin/bash
# Apply Paper 2 v3 Phase 1 changes to the Memento overlay clone.
#
# Idempotent: refuses to apply if the patch is already applied (checked via
# `git apply --check --reverse` heuristic). Use --force to overwrite.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
MEMENTO_DIR="${REPO_ROOT}/external/memento"

if [[ ! -d "${MEMENTO_DIR}" ]]; then
    echo "ERROR: ${MEMENTO_DIR} not found."
    echo "Clone first:  git clone --depth 1 https://github.com/microsoft/memento ${MEMENTO_DIR}"
    exit 1
fi

PATCH="${SCRIPT_DIR}/v3_phase1_modifications.patch"
NEW_FILE="${SCRIPT_DIR}/memento_store.py.new"
TARGET_NEW_FILE="${MEMENTO_DIR}/vllm/vllm/v1/core/block_masking/memento_store.py"

cd "${MEMENTO_DIR}"

# Check whether the patch is already applied (best-effort).
if git apply --check --reverse "${PATCH}" 2>/dev/null; then
    echo "Patch appears to already be applied (reverse-check passed). Skipping."
else
    echo "Applying ${PATCH} ..."
    git apply "${PATCH}"
    echo "  Modified files:"
    git diff --name-only | sed 's/^/    /'
fi

if [[ ! -f "${TARGET_NEW_FILE}" ]]; then
    echo "Installing memento_store.py (new) ..."
    cp "${NEW_FILE}" "${TARGET_NEW_FILE}"
elif cmp -s "${NEW_FILE}" "${TARGET_NEW_FILE}"; then
    echo "memento_store.py already in place (identical)."
else
    # Overwrite — the .new file is the source of truth (it's regenerated
    # from external/memento/.../memento_store.py whenever it changes).
    # Without overwrite we'd silently use a stale version on rebuild.
    echo "Updating memento_store.py (file differs from .new — overwriting)."
    cp "${NEW_FILE}" "${TARGET_NEW_FILE}"
fi

echo ""
echo "Done. Run install_overlay.sh next:"
echo "  cd ${MEMENTO_DIR}/vllm && bash install_overlay.sh"
