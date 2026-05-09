"""Static-best S3FIFO baselines.

Registers individual variants (12 cross-product combos of small_ratio × threshold)
plus three meta-aliases that report the per-cell minimum MR over their sweep:

  S3FIFO+OptT   — best of promote_threshold ∈ {1, 2, 3} at small_ratio=0.10
  S3FIFO+OptS   — best of small_ratio ∈ {0.01, 0.05, 0.10, 0.25} at threshold=1
  S3FIFO+OptST  — best of all 12 (small_ratio, threshold) combinations

These are *offline-tuned* baselines — they pick the best static config per cell
after the fact. They establish what the strongest fixed-policy version of S3FIFO
achieves, against which the learned gate's wins quantify the value of online
adaptation rather than just hyperparameter tuning.

run_experiments.py detects names in META_OPTIMA and expands them: it runs each
underlying variant, then reports min(req_MR), min(byte_MR) per cache size as the
meta-algo's row.
"""

from baselines import S3FIFO


THRESHOLDS: list[int] = [1, 2, 3]
S_RATIOS:   list[float] = [0.01, 0.05, 0.10, 0.25]


# ─── Individual cross-product variants ────────────────────────────────────────

def _make(small_ratio: float, threshold: int):
    return lambda sz, s=small_ratio, t=threshold: S3FIFO(
        sz, small_ratio=s, promote_threshold=t)


ALGOS: dict[str, callable] = {}
for _s in S_RATIOS:
    for _t in THRESHOLDS:
        ALGOS[f"S3FIFO+T{_t}+S{_s:.2f}"] = _make(_s, _t)


# ─── Meta-aliases (post-processed by run_experiments.py) ──────────────────────

META_OPTIMA: dict[str, list[str]] = {
    "S3FIFO+OptT":  [f"S3FIFO+T{t}+S0.10"     for t in THRESHOLDS],
    "S3FIFO+OptS":  [f"S3FIFO+T1+S{s:.2f}"    for s in S_RATIOS],
    "S3FIFO+OptST": [f"S3FIFO+T{t}+S{s:.2f}"
                     for t in THRESHOLDS for s in S_RATIOS],
}
