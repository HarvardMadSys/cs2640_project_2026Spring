"""
Attack idea #8: Decision-stump admission filter on top of S3-FIFO.

This is the must-implement control baseline from ATTACK_PLAN.md. The "stump"
is a one-feature classifier: admit iff (obj_size <= threshold) OR (obj_id in
S3-FIFO ghost queue G). Objects bigger than the threshold and unknown to G
are dropped on miss — they never enter the small queue.

Why: AdaptSize (Berger et al., NSDI '17) shows that for Twitter-like skewed
size distributions, a probabilistic "small admit, big reject" filter improves
both request- and byte-MR by stopping rare giant objects from displacing many
small popular ones. We use a deterministic stump (single threshold) which is
the simplest realisation of the same idea, and learn the threshold online by
1-D bisection on observed miss-bytes/admit-bytes per bin.

Implementation notes
--------------------
* The harness loads the trace with ignore_obj_size=True (so req.obj_size==1).
  Size-based admission needs the real on-disk size. We solve that without
  modifying sim.py by lazily building a module-level obj_id→size map directly
  from the CSV on first cache instantiation. Sizes are stable per obj_id in
  the Twitter cluster52 trace (verified empirically: 0 size-mutations in the
  first 100k rows).
* Online bisection:
    - Warm-up window: first 5000 requests. Threshold initialised to the
      median real-size observed in this window.
    - Every K=10000 requests, compute "value density" = hits/bytes_admitted
      separately for the below-threshold bin and the above-threshold-but-
      admitted-via-ghost bin. If above density >= below density, the threshold
      is too tight — bisect lower. Else bisect higher. Bounds shrink each step
      (standard bisection); they reset to the global [p1, p99] every 50 windows
      so the policy can recover from a non-stationary trace.
* Eviction stays untouched (vanilla S3-FIFO walking). Only on_miss is gated.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from sim import Cache, Request, admit, TWITTER_CSV
from baselines import S3FIFO


# ─── module-level real-size map ─────────────────────────────────────────────
# Populated on first construction. Shared across all stump instances in a
# process because run_experiments.py instantiates a fresh cache per (algo,
# size) cell — we don't want to re-parse 1M rows three times.

_SIZE_MAP: dict[int, int] = {}
_SIZE_MAP_LOADED: bool = False


def _ensure_size_map(path: Path = TWITTER_CSV) -> dict[int, int]:
    global _SIZE_MAP, _SIZE_MAP_LOADED
    if _SIZE_MAP_LOADED:
        return _SIZE_MAP
    with open(path, "rt") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].lstrip().startswith("#"):
                continue
            try:
                _, oid_s, sz_s, _ = row
            except ValueError:
                continue
            oid = int(oid_s)
            if oid not in _SIZE_MAP:
                _SIZE_MAP[oid] = max(1, int(sz_s))
    _SIZE_MAP_LOADED = True
    return _SIZE_MAP


def _real_size(req: Request) -> int:
    """Return the real on-disk size for `req`, even if the harness loaded the
    trace with ignore_obj_size=True (req.obj_size == 1)."""
    if req.obj_size > 1:
        return req.obj_size
    return _SIZE_MAP.get(req.obj_id, 1)


# ─── Adaptive stump (online bisection) ──────────────────────────────────────


class S3FIFOStump(S3FIFO):
    """S3-FIFO with a size-stump admission gate, threshold learned online.

    Admission rule on miss:
        if real_size(req) > threshold and obj_id ∉ G:
            skip (do not call admit)
        else:
            normal S3-FIFO miss path

    Threshold update: 1-D bisection on observed value-density per bin every
    K=10000 requests, after a 5000-request warm-up window.
    """

    WARMUP = 5000
    UPDATE_EVERY = 10000
    BOUND_RESET_EVERY = 50  # every N updates, widen bounds to global p1..p99

    def __init__(self, cache_size: int, small_ratio: float = 0.1,
                 promote_threshold: int = 1):
        super().__init__(cache_size, small_ratio=small_ratio,
                         promote_threshold=promote_threshold)
        _ensure_size_map()

        # Threshold state — start optimistic so we admit everything during
        # warm-up, then snap to the median of observed sizes.
        self.threshold: float = float("inf")
        self.lo: float = 1.0
        self.hi: float = float("inf")
        self.warmup_sizes: list[int] = []

        # Per-bin running stats (reset every UPDATE_EVERY).
        self.below_admit_bytes: float = 0.0
        self.below_hits: int = 0
        self.above_admit_bytes: float = 0.0
        self.above_hits: int = 0

        # Track which bin a still-resident object came from, so on_hit can
        # credit the right bin.
        self._bin: dict[int, int] = {}  # obj_id -> 0 (below) | 1 (above-via-ghost)

        # Stats for bisection bookkeeping.
        self.n_seen: int = 0
        self.n_updates: int = 0
        self.n_rejected: int = 0
        self.n_admitted: int = 0

    # ── hooks ────────────────────────────────────────────────────────────────

    def on_hit(self, req: Request) -> None:
        super().on_hit(req)
        bin_ = self._bin.get(req.obj_id)
        if bin_ == 0:
            self.below_hits += 1
        elif bin_ == 1:
            self.above_hits += 1

    def on_miss(self, req: Request) -> None:
        self.n_seen += 1
        oid = req.obj_id
        sz = _real_size(req)

        # Warm-up: collect sizes, admit everything.
        if self.n_seen <= self.WARMUP:
            self.warmup_sizes.append(sz)
            if self.n_seen == self.WARMUP:
                self.warmup_sizes.sort()
                med = self.warmup_sizes[len(self.warmup_sizes) // 2]
                self.threshold = float(med)
                # Set bisection bounds to roughly p1 .. p99 of warm-up.
                p1 = self.warmup_sizes[max(0, len(self.warmup_sizes) // 100)]
                p99 = self.warmup_sizes[
                    min(len(self.warmup_sizes) - 1,
                        (99 * len(self.warmup_sizes)) // 100)]
                self.lo = float(max(1, p1))
                self.hi = float(max(self.lo + 1, p99))
                self.warmup_sizes = []  # release memory
            super().on_miss(req)
            self._bin[oid] = 0
            self.below_admit_bytes += sz
            self.n_admitted += 1
            return

        in_ghost = oid in self.G_set

        # Stump rule: reject if too big AND not seen-before in ghost.
        if sz > self.threshold and not in_ghost:
            self.n_rejected += 1
            self._maybe_update()
            return

        # Otherwise admit via the normal S3-FIFO miss path.
        super().on_miss(req)
        if oid in self._members:  # actually admitted
            if sz > self.threshold:
                # via-ghost (treated as popular by S3FIFO -> goes to M)
                self._bin[oid] = 1
                self.above_admit_bytes += sz
            else:
                self._bin[oid] = 0
                self.below_admit_bytes += sz
            self.n_admitted += 1

        self._maybe_update()

    def on_remove(self, obj_id: int) -> None:
        super().on_remove(obj_id)
        self._bin.pop(obj_id, None)

    # ── bisection ────────────────────────────────────────────────────────────

    def _maybe_update(self) -> None:
        if self.n_seen <= self.WARMUP:
            return
        if (self.n_seen - self.WARMUP) % self.UPDATE_EVERY != 0:
            return
        self.n_updates += 1

        # Value density per bin: hits per byte admitted (higher = better).
        below_d = (self.below_hits / self.below_admit_bytes
                   if self.below_admit_bytes > 0 else 0.0)
        above_d = (self.above_hits / self.above_admit_bytes
                   if self.above_admit_bytes > 0 else 0.0)

        # Bisect: if big-objects ARE delivering hits, threshold is too tight
        # (lower it to admit more); else raise it (admit fewer big ones).
        # If above_admit_bytes is ~0 (we never see big objects via ghost),
        # we have no signal — leave threshold and slowly raise to widen.
        old_th = self.threshold
        if self.above_admit_bytes < 1.0:
            # No data on the above bin; nudge threshold up a hair to gather it.
            self.threshold = min(self.hi, self.threshold * 1.05)
        elif above_d >= below_d:
            # Big-bin density >= small-bin density: bigs we're already
            # admitting (via ghost) are returning hits just as efficiently as
            # smalls. The threshold is too low — RAISE it to admit more bigs.
            self.lo = self.threshold
            self.threshold = (self.threshold + self.hi) / 2.0
        else:
            # Small-bin density > big-bin density: smalls are more efficient.
            # The threshold is too permissive — LOWER it to reject more bigs.
            self.hi = self.threshold
            self.threshold = (self.lo + self.threshold) / 2.0

        # Periodically reset bounds so we can recover from non-stationarity.
        if self.n_updates % self.BOUND_RESET_EVERY == 0:
            # Re-widen by 50% on each side, capped to a reasonable absolute
            # range. Don't reset too aggressively — keeps history meaningful.
            self.lo = max(1.0, self.lo * 0.75)
            self.hi = self.hi * 1.5

        # Reset per-window stats so the next window's signal is fresh.
        self.below_admit_bytes = 0.0
        self.below_hits = 0
        self.above_admit_bytes = 0.0
        self.above_hits = 0

        # Optional debug — silenced by default.
        # print(f"[stump] seen={self.n_seen} th: {old_th:.0f}->{self.threshold:.0f} "
        #       f"below_d={below_d:.4f} above_d={above_d:.4f} "
        #       f"rej_frac={self.n_rejected/(self.n_admitted+self.n_rejected):.3f}")


# ─── Fixed-threshold variant (ablation) ─────────────────────────────────────


class S3FIFOStumpFixed(S3FIFO):
    """Like S3FIFOStump but with a hard-coded threshold (no learning).

    Default threshold = 256 bytes — chosen as a round number near the median
    of the warm-up distribution observed in the trace (~54–600). Override via
    the `threshold` kwarg if you want to sweep.
    """

    def __init__(self, cache_size: int, threshold: int = 256,
                 small_ratio: float = 0.1, promote_threshold: int = 1):
        super().__init__(cache_size, small_ratio=small_ratio,
                         promote_threshold=promote_threshold)
        _ensure_size_map()
        self.threshold = float(threshold)
        self.n_rejected = 0
        self.n_admitted = 0

    def on_miss(self, req: Request) -> None:
        sz = _real_size(req)
        in_ghost = req.obj_id in self.G_set
        if sz > self.threshold and not in_ghost:
            self.n_rejected += 1
            return
        super().on_miss(req)
        if req.obj_id in self._members:
            self.n_admitted += 1


# ─── registry ───────────────────────────────────────────────────────────────

ALGOS = {
    "S3FIFO+Stump": lambda sz: S3FIFOStump(sz),
    "S3FIFO+StumpFixed": lambda sz: S3FIFOStumpFixed(sz),
}


# ─── self-check / size-mode ablation ────────────────────────────────────────

if __name__ == "__main__":
    # Sanity: run S3FIFO and the stump variants on 100k requests at size 1000,
    # under both ignore_obj_size modes, and print the comparison.
    import sys, time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sim import process_trace, read_twitter_csv, TWITTER_CSV

    LIMIT = 100_000
    SIZE = 1000

    for ignore in (True, False):
        trace = list(read_twitter_csv(TWITTER_CSV, limit=LIMIT,
                                      ignore_obj_size=ignore))
        for name, ctor in [
            ("S3FIFO", lambda s: S3FIFO(s)),
            ("S3FIFO+StumpFixed", lambda s: S3FIFOStumpFixed(s)),
            ("S3FIFO+Stump", lambda s: S3FIFOStump(s)),
        ]:
            cache = ctor(SIZE)
            t0 = time.time()
            req_mr, byte_mr = process_trace(cache, trace)
            secs = time.time() - t0
            extra = ""
            if hasattr(cache, "threshold"):
                extra += f" th={cache.threshold:.0f}"
            if hasattr(cache, "n_rejected"):
                extra += (f" rej={cache.n_rejected}"
                          f" adm={cache.n_admitted}")
            print(f"ignore_obj_size={ignore} {name:<22} "
                  f"req_MR={req_mr:.4f} byte_MR={byte_mr:.4f} "
                  f"{secs:.1f}s {extra}")
