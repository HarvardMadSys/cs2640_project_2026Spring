#!/usr/bin/env bash
# demo.sh — DOC-04 live 6-policy sweep on pre-generated 5K-request trace.
# Runtime: ~30-45s on target laptop (<60s budget per D-15). Rehearsed 3x
# per D-18; see demo-rehearsal.log.
#
# Invocation: ./demo.sh  (no arguments)
# Output: stdout miss-ratio table + results/demo/figures/mrc.pdf

set -euo pipefail

# --- env setup (D-17) -----------------------------------------------------
# .env defines CONGRESS_API_KEY, COURTLISTENER_API_KEY (not needed for the
# demo itself — replay-Zipf uses the committed trace — but the convention
# is to source .env unconditionally so the environment matches the rest of
# the Phase 1-5 workflow).
if [[ -f .env ]]; then
  # shellcheck source=/dev/null
  source .env
fi

# macOS libexpat workaround — load-bearing, copied from Makefile:70-73.
# Without DYLD_LIBRARY_PATH, Python 3.14's pyexpat symbol-resolves against
# /usr/lib/libexpat.1.dylib which is missing _XML_SetAllocTrackerActivationThreshold.
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib
export PYTHONPATH=.venv/lib/python3.14/site-packages
PLOT_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14

# --- wall-clock start -----------------------------------------------------
START=$(date +%s)

# --- Step 1/3: build ------------------------------------------------------
echo "=== Step 1/3: building cache_sim ==="
make --quiet

# --- Step 2/3: 6-policy sweep on 5K Congress slice ------------------------
echo ""
echo "=== Step 2/3: 6-policy sweep on 5K Congress slice (demo_trace.csv) ==="
mkdir -p results/demo
./cache_sim --trace traces/demo_trace.csv --replay-zipf \
            --policies lru,fifo,clock,s3fifo,sieve,wtinylfu \
            --cache-sizes 0.001,0.01,0.05,0.1 \
            --output-dir results/demo

# --- Live miss-ratio table (D-15 narration) -------------------------------
echo ""
echo "=== Miss-ratio table (live) ==="
column -t -s, results/demo/mrc.csv

# --- Step 3/3: render figure ----------------------------------------------
echo ""
echo "=== Step 3/3: rendering figure ==="
DYLD_LIBRARY_PATH="$DYLD_LIBRARY_PATH" PYTHONPATH="$PYTHONPATH" \
  "$PLOT_PYTHON" scripts/plot_results.py --workload demo

# --- wall-clock end -------------------------------------------------------
END=$(date +%s)
ELAPSED=$((END - START))
echo ""
echo "=== demo complete. Figure: results/demo/figures/mrc.pdf ==="
echo "=== wall-clock: ${ELAPSED}s ==="
