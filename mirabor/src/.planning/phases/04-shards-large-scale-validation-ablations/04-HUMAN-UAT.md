---
status: partial
phase: 04-shards-large-scale-validation-ablations
source: [04-VERIFICATION.md]
started: 2026-04-20T12:00:00Z
updated: 2026-04-20T12:00:00Z
---

## Current Test

[awaiting human testing of 5 ablation / SHARDS PDF figures]

## Tests

### 1. Inspect results/shards_large/figures/shards_convergence.pdf
expected: Log-scale x-axis (sampling rate %), 3 data points, the 0.01% point has asterisk annotation and a footnote at bottom reading "below paper-recommended 200-sample floor (D-01)"; MAE values should be 0.0437 (0.01%), 0.0496 (0.1%), 0.0378 (1%) — monotone-ish, with the 0.1% rate having the highest error.
result: [pending]

### 2. Inspect results/shards_large/figures/shards_mrc_overlay.pdf
expected: Multiple overlapping MRC curves — solid black "Exact MRC (50K oracle)" line as baseline, dotted lines for 50K SHARDS at {0.1%, 1%, 10%} (low opacity), dashed lines for 1M SHARDS at {0.01%, 0.1%, 1%, 10%} (full opacity). Legend on right. Y-axis starts at 0. This is the PITFALLS M3 money-shot figure — SHARDS approximations should visibly converge toward the exact MRC at higher rates.
result: [pending]

### 3. Inspect results/congress/figures/ablation_s3fifo.pdf
expected: 2-panel figure (Congress left, Court right), 3 lines per panel (light-red S3-FIFO-5, red S3-FIFO-10, dark-red S3-FIFO-20), x-axis alpha 0.6-1.2, y-axis miss ratio. S3-FIFO-5 should be the lowest line at every alpha on both panels; Court panel should show visibly larger spread than Congress at high alpha (the 6.3pp vs 1.2pp Δ(20−5) finding).
result: [pending]

### 4. Inspect results/congress/figures/ablation_sieve.pdf
expected: 2-panel figure (Congress | Court), 2 lines per panel — solid purple SIEVE (lower miss ratio) vs dashed purple SIEVE-NoProm (higher miss ratio). Linestyle distinction (solid vs dashed) should be visually clear. SIEVE-NoProm should be consistently above SIEVE at every alpha on both panels. Peak gap ~15.4pp on Congress at α=1.0; Congress gap should be visibly larger than Court.
result: [pending]

### 5. Inspect results/congress/figures/ablation_doorkeeper.pdf
expected: 2-panel figure (Congress | Court), 2 lines per panel — solid brown W-TinyLFU vs dashed brown W-TinyLFU+DK. Lines should be very close on both panels (within <1pp at all α). Congress lines should nearly overlap; Court lines should show small divergence at high α. Any visible large gap would contradict the "marginal hedge" finding — flag as issue.
result: [pending]

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps
