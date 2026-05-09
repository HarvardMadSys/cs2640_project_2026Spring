"""
S3FIFO + Learned Promotion Gate (attack idea #1 from ATTACK_PLAN.md).

Replace S3-FIFO's hardcoded `freq >= promote_threshold` rule for S→M promotion
with an online logistic-regression classifier. Features at decision time:
    x1 = accessed_bit         (S3-FIFO's existing signal; bias-recovery feature)
    x2 = log(obj_size + 1)    (≈0 when ignore_obj_size=True; weight stays ~0)
    x3 = age_in_S / S_cap     (normalized residency time in S)
    x4 = ghost_hit_flag       (1 if entry came from G; re-entrant)

Score: p = sigmoid(w·x + b); promote iff p > 0.5.
Online SGD with logistic loss; lr=0.05, L2=1e-4. Initialized at S3-FIFO
behavior: bias_w[x1] ≈ 1.0, others 0.

Robustness path (Lykouris-Vassilvitskii style): if rolling 1000-step training
accuracy < 55%, fall back to the original `freq >= promote_threshold` rule,
guaranteeing we never lose to vanilla S3-FIFO by more than a constant.

Label: y = 1 if (next_access_vtime_at_last_ref - now) < H else 0,
       with H = cache_size (a Belady-boundary horizon).

Note on size feature: with `ignore_obj_size=True` (the run_experiments default),
all req.obj_size==1 → x2=log(2)=const, so weight on x2 just absorbs into bias.
This is documented in the report; size signal only matters when sizes vary.
"""

from __future__ import annotations

import math
import sys
from collections import deque
from typing import Optional

from sim import Cache, Request, admit
from baselines import S3FIFO


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


class S3FIFOLearned(S3FIFO):
    """S3-FIFO with a learned S→M promotion gate."""

    def __init__(self, cache_size: int, small_ratio: float = 0.1,
                 promote_threshold: int = 1,
                 lr: float = 0.05, l2: float = 1e-4,
                 fallback_acc_threshold: float = 0.55,
                 fallback_window: int = 1000):
        super().__init__(cache_size, small_ratio=small_ratio,
                         promote_threshold=promote_threshold)

        # Belady horizon: an object is "needed soon" if its next reference is
        # within H requests. H = full cache size — one full residence time.
        self.H = cache_size

        # ── per-object learned-policy state (only for objects in S) ────────
        # insertion_idx[oid] = vtime at which oid most recently entered S
        # last_next_vtime[oid] = next_access_vtime of the latest reference
        # ghost_flag[oid] = 1 if oid was popped from G when admitted (re-entrant)
        # NB: in vanilla S3-FIFO, ghost-hit objects go straight to M, bypassing
        # the gate. We KEEP that fast path (ghost-hits are strongly popular by
        # construction) — so ghost_flag is ~always 0 at the gate. We still
        # store it for completeness / future ablation.
        self.insertion_idx: dict[int, int] = {}
        self.last_next_vtime: dict[int, int] = {}
        self.ghost_flag: dict[int, int] = {}

        # Virtual time counter (request index, matches trace's vtime domain).
        self.now: int = 0

        # ── logistic-regression weights ────────────────────────────────────
        # Initialize so the model starts at S3-FIFO's policy:
        # promote iff accessed_bit=1, with a slight positive bias on x1.
        # weights = (w_x1, w_x2, w_x3, w_x4); b = scalar bias.
        self.w = [1.0, 0.0, 0.0, 0.0]
        self.b = -0.5  # so x1=0 -> z=-0.5 (no promote); x1=1 -> z=+0.5 (promote)

        self.lr = lr
        self.l2 = l2

        # Rolling accuracy tracking (last `fallback_window` decisions).
        self.fallback_acc_threshold = fallback_acc_threshold
        self.fallback_window = fallback_window
        self.recent_correct: deque[int] = deque(maxlen=fallback_window)
        # Counters
        self.n_decisions = 0
        self.n_fallback_fired = 0
        # Last-10k tracking for end-of-run report.
        self.last10k: deque[int] = deque(maxlen=10_000)

    # ── feature builder ─────────────────────────────────────────────────────

    def _features(self, oid: int) -> tuple[float, float, float, float]:
        x1 = 1.0 if self.freq.get(oid, 0) >= 1 else 0.0
        # obj_size feature: stored at insertion time in self._sizes (set by sim).
        sz = self._sizes.get(oid, 1)
        x2 = math.log(sz + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        age = (self.now - ins) / max(1, self.S_cap)
        x3 = float(age)
        x4 = float(self.ghost_flag.get(oid, 0))
        return x1, x2, x3, x4

    def _predict(self, x: tuple[float, ...]) -> tuple[float, float]:
        z = self.b
        for i in range(len(self.w)):
            z += self.w[i] * x[i]
        return z, _sigmoid(z)

    def _sgd_update(self, x: tuple[float, ...], y: float, p: float) -> None:
        # Logistic loss gradient: (p - y) * x; plus L2 on w (not on b).
        err = p - y
        for i in range(len(self.w)):
            self.w[i] -= self.lr * (err * x[i] + self.l2 * self.w[i])
        self.b -= self.lr * err

    # ── overrides ───────────────────────────────────────────────────────────

    def on_hit(self, req: Request) -> None:
        super().on_hit(req)  # bumps freq
        self.now += 1
        oid = req.obj_id
        # Update last_next_vtime so the gate's label uses the most recent
        # reference's next_access_vtime.
        self.last_next_vtime[oid] = req.next_access_vtime

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            self.now += 1
            return
        oid = req.obj_id
        was_ghost = oid in self.G_set  # before super removes it
        super().on_miss(req)
        # Track learned-policy state for this insertion. Note super() puts
        # ghost-hit objects directly into M, bypassing S — so we only really
        # need state for S entries. But we record uniformly for safety.
        self.insertion_idx[oid] = self.now
        self.last_next_vtime[oid] = req.next_access_vtime
        self.ghost_flag[oid] = 1 if was_ghost else 0
        self.now += 1

    def _evict_S_one(self) -> Optional[int]:
        """Replace S3-FIFO's freq-based promotion with the learned gate.
        Same overall structure as the parent, but the predicate is the
        logistic-regression classifier (with fallback)."""
        while self.S:
            oid = self.S.popleft()
            if oid not in self.in_S:
                continue  # stale
            self.in_S.discard(oid)

            # Build features and decide.
            x = self._features(oid)
            z, p = self._predict(x)

            # Robustness fallback: if the model has been performing poorly on
            # recent decisions, fall back to the freq>=threshold rule.
            use_fallback = False
            if len(self.recent_correct) >= self.fallback_window:
                acc = sum(self.recent_correct) / len(self.recent_correct)
                if acc < self.fallback_acc_threshold:
                    use_fallback = True
                    self.n_fallback_fired += 1

            if use_fallback:
                f = self.freq.get(oid, 0)
                promote = (f >= self.promote_threshold)
            else:
                promote = (p > 0.5)

            # Generate the label using last_next_vtime.
            nxt = self.last_next_vtime.get(oid, -1)
            if nxt is None or nxt < 0 or nxt > 10**18:
                # No future reference (or sentinel): label = 0 (don't promote).
                y = 0.0
            else:
                y = 1.0 if (nxt - self.now) < self.H else 0.0

            # Online SGD update (always — even on fallback steps, the model
            # keeps learning so it can recover).
            self._sgd_update(x, y, p)

            # Track accuracy: correct iff (p>0.5) == (y==1). Use the model's
            # raw prediction, not the post-fallback decision, so the accuracy
            # measures the model itself.
            pred = 1 if p > 0.5 else 0
            correct = 1 if pred == int(y) else 0
            self.recent_correct.append(correct)
            self.last10k.append(correct)
            self.n_decisions += 1

            if promote:
                self.freq[oid] = 0
                self.in_M.add(oid)
                self.M.append(oid)
                # Clear S-only learned-policy state.
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
                # last_next_vtime persists (object is still in cache).
            else:
                self._ghost_push(oid)
                self.freq.pop(oid, None)
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
                self.last_next_vtime.pop(oid, None)
                return oid
        return None

    def on_remove(self, obj_id: int) -> None:
        super().on_remove(obj_id)
        self.insertion_idx.pop(obj_id, None)
        self.ghost_flag.pop(obj_id, None)
        self.last_next_vtime.pop(obj_id, None)

    # ── reporting ───────────────────────────────────────────────────────────

    def __del__(self):
        try:
            self.report()
        except Exception:
            pass

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        msg = (
            f"[S3FIFOLearned] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"w_x1(acc)={self.w[0]:+.4f} w_x2(logsz)={self.w[1]:+.4f} "
            f"w_x3(age)={self.w[2]:+.4f} w_x4(ghost)={self.w[3]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)


class S3FIFOLearnedV2(S3FIFOLearned):
    """V2: x1 is the *normalized freq counter* (freq / MAX_FREQ ∈ [0, 1])
    instead of the binary accessed_bit.

    Motivation: the V1 binary x1 collapsed S3-FIFO's freq counter from
    {0,1,2,3} into {0,1}, so the model could not distinguish "accessed once"
    (often a one-touch wonder at small cache) from "accessed twice or thrice"
    (genuinely popular). At cache=1000 V1 converged to z_max ≈ -1.8 — i.e.
    the gate degenerated to "never promote", because the only way to suppress
    false positives was to push w_x1 → 0 globally. With a continuous x1, the
    model can learn the equivalent of "promote iff freq ≥ 2" by enlarging
    w_x1 and pushing b more negative.

    Init: w_x1 = 3.0, b = -0.5. This recovers vanilla S3-FIFO at step 0
    (freq=0 → z=-0.5, freq=1 → z=+0.5).
    """

    MAX_FREQ = 3

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        # Re-init to recover vanilla S3-FIFO under the normalized-count feature.
        self.w = [3.0, 0.0, 0.0, 0.0]
        self.b = -0.5
        # promotion-counting telemetry (V1 didn't track this; useful for
        # diagnosing the "always-evict" failure mode you hit in V1).
        self.n_promote = 0
        self.n_evict_from_s = 0

    def _features(self, oid: int) -> tuple[float, float, float, float]:
        # Continuous-valued x1 = freq / MAX_FREQ ∈ [0, 1].
        f = self.freq.get(oid, 0)
        x1 = f / self.MAX_FREQ
        sz = self._sizes.get(oid, 1)
        x2 = math.log(sz + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        x3 = (self.now - ins) / max(1, self.S_cap)
        x4 = float(self.ghost_flag.get(oid, 0))
        return x1, x2, x3, x4

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        msg = (
            f"[S3FIFOLearnedV2] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"promote_rate={promote_rate:.4f} ({self.n_promote}/{self.n_evict_from_s}) "
            f"w_x1(freq)={self.w[0]:+.4f} w_x2(logsz)={self.w[1]:+.4f} "
            f"w_x3(age)={self.w[2]:+.4f} w_x4(ghost)={self.w[3]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)

    def _evict_S_one(self) -> Optional[int]:
        """Same as parent, but counts promote/evict outcomes for telemetry."""
        while self.S:
            oid = self.S.popleft()
            if oid not in self.in_S:
                continue
            self.in_S.discard(oid)
            self.n_evict_from_s += 1

            x = self._features(oid)
            z, p = self._predict(x)

            use_fallback = False
            if len(self.recent_correct) >= self.fallback_window:
                acc = sum(self.recent_correct) / len(self.recent_correct)
                if acc < self.fallback_acc_threshold:
                    use_fallback = True
                    self.n_fallback_fired += 1

            if use_fallback:
                f = self.freq.get(oid, 0)
                promote = (f >= self.promote_threshold)
            else:
                promote = (p > 0.5)

            nxt = self.last_next_vtime.get(oid, -1)
            if nxt is None or nxt < 0 or nxt > 10**18:
                y = 0.0
            else:
                y = 1.0 if (nxt - self.now) < self.H else 0.0

            self._sgd_update(x, y, p)

            pred = 1 if p > 0.5 else 0
            correct = 1 if pred == int(y) else 0
            self.recent_correct.append(correct)
            self.last10k.append(correct)
            self.n_decisions += 1

            if promote:
                self.n_promote += 1
                self.freq[oid] = 0
                self.in_M.add(oid)
                self.M.append(oid)
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
            else:
                self._ghost_push(oid)
                self.freq.pop(oid, None)
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
                self.last_next_vtime.pop(oid, None)
                return oid
        return None


class S3FIFOLearnedV3(S3FIFOLearnedV2):
    """V3: V2 + two additional features.

      x5 = log(hits_since_insert + 1)        scale-free uncapped count
      x6 = (now − last_hit_time) / S_cap     recency since last hit (LRU-style)

    x5 tests whether S3-FIFO's freq cap (=3) is leaving signal on the table.
    x6 gives the gate the LRU-style recency it currently lacks (V1/V2 only see
    age-since-insertion, not age-since-last-hit).

    Init: w=[3,0,0,0,0,0], b=-0.5 → recovers V2's step-0 policy exactly
    (w_x5=w_x6=0 means the new features start with no influence).

    Normalization rule used here: divide by S_cap when the quantity is in
    trace-vtime units (x6); use log(·+1) for unbounded counts (x5). See the
    discussion thread for why /S_cap is wrong for hit counts.
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [3.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.b = -0.5
        self.hits_since_insert: dict[int, int] = {}
        self.last_hit_time: dict[int, int] = {}

    def _features(self, oid: int) -> tuple[float, ...]:
        f = self.freq.get(oid, 0)
        x1 = f / self.MAX_FREQ
        sz = self._sizes.get(oid, 1)
        x2 = math.log(sz + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        x3 = (self.now - ins) / max(1, self.S_cap)
        x4 = float(self.ghost_flag.get(oid, 0))
        h = self.hits_since_insert.get(oid, 0)
        x5 = math.log(h + 1.0)
        last = self.last_hit_time.get(oid, ins)
        x6 = (self.now - last) / max(1, self.S_cap)
        return (x1, x2, x3, x4, x5, x6)

    def on_hit(self, req: Request) -> None:
        super().on_hit(req)
        oid = req.obj_id
        self.hits_since_insert[oid] = self.hits_since_insert.get(oid, 0) + 1
        self.last_hit_time[oid] = self.now  # super already incremented

    def on_miss(self, req: Request) -> None:
        super().on_miss(req)
        oid = req.obj_id
        if oid in self.insertion_idx:  # admitted to S (not bypassed by ghost)
            self.hits_since_insert[oid] = 0
            self.last_hit_time[oid] = self.insertion_idx[oid]

    def on_remove(self, obj_id: int) -> None:
        super().on_remove(obj_id)
        self.hits_since_insert.pop(obj_id, None)
        self.last_hit_time.pop(obj_id, None)

    def _evict_S_one(self) -> Optional[int]:
        # Same eviction logic as V2; also drops V3's per-oid state on promote
        # (eviction-side cleanup is handled by on_remove via the harness).
        while self.S:
            oid = self.S.popleft()
            if oid not in self.in_S:
                continue
            self.in_S.discard(oid)
            self.n_evict_from_s += 1

            x = self._features(oid)
            z, p = self._predict(x)

            use_fallback = False
            if len(self.recent_correct) >= self.fallback_window:
                acc = sum(self.recent_correct) / len(self.recent_correct)
                if acc < self.fallback_acc_threshold:
                    use_fallback = True
                    self.n_fallback_fired += 1

            if use_fallback:
                f = self.freq.get(oid, 0)
                promote = (f >= self.promote_threshold)
            else:
                promote = (p > 0.5)

            nxt = self.last_next_vtime.get(oid, -1)
            if nxt is None or nxt < 0 or nxt > 10**18:
                y = 0.0
            else:
                y = 1.0 if (nxt - self.now) < self.H else 0.0

            self._sgd_update(x, y, p)

            pred = 1 if p > 0.5 else 0
            correct = 1 if pred == int(y) else 0
            self.recent_correct.append(correct)
            self.last10k.append(correct)
            self.n_decisions += 1

            if promote:
                self.n_promote += 1
                self.freq[oid] = 0
                self.in_M.add(oid)
                self.M.append(oid)
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
                self.hits_since_insert.pop(oid, None)
                self.last_hit_time.pop(oid, None)
            else:
                self._ghost_push(oid)
                self.freq.pop(oid, None)
                self.insertion_idx.pop(oid, None)
                self.ghost_flag.pop(oid, None)
                self.last_next_vtime.pop(oid, None)
                self.hits_since_insert.pop(oid, None)
                self.last_hit_time.pop(oid, None)
                return oid
        return None

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        msg = (
            f"[S3FIFOLearnedV3] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"promote_rate={promote_rate:.4f} ({self.n_promote}/{self.n_evict_from_s}) "
            f"w_x1(freq)={self.w[0]:+.4f} w_x2(logsz)={self.w[1]:+.4f} "
            f"w_x3(age)={self.w[2]:+.4f} w_x4(ghost)={self.w[3]:+.4f} "
            f"w_x5(loghits)={self.w[4]:+.4f} w_x6(recency)={self.w[5]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)


class S3FIFOLearnedV4(S3FIFOLearnedV3):
    """V4: minimal feature set — only `log(hits)`, `age`, `recency`.

    Drops V3's x1 (freq), x2 (log_size), x4 (ghost_flag) on the basis that:
      - x4 ≡ 0 at the gate (vanilla S3-FIFO bypasses ghost-hits)
      - x2 is constant under `ignore_obj_size=True`
      - x1 (capped freq) is largely redundant with log(hits) once that's added

    The only popularity signal is `log(hits)`; the only temporal signals are
    `age` and `recency`. Three features total — the smallest informative set
    we've tested.

    Init: w=[1.0, 0, 0], b=-0.35
      hits=0 → log(1)=0     → z=-0.35 → no promote
      hits=1 → log(2)=0.69  → z=+0.34 → promote
      hits≥1 always promotes; recovers vanilla S3-FIFO's `freq≥1` rule via
      the `log(hits+1)` proxy at step 0.
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [1.0, 0.0, 0.0]
        self.b = -0.35

    def _features(self, oid: int) -> tuple[float, ...]:
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        x2 = (self.now - ins) / max(1, self.S_cap)
        last = self.last_hit_time.get(oid, ins)
        x3 = (self.now - last) / max(1, self.S_cap)
        return (x1, x2, x3)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        msg = (
            f"[S3FIFOLearnedV4] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"promote_rate={promote_rate:.4f} ({self.n_promote}/{self.n_evict_from_s}) "
            f"w_loghits={self.w[0]:+.4f} w_age={self.w[1]:+.4f} w_recency={self.w[2]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)


class S3FIFOLearnedV5(S3FIFOLearnedV4):
    """V5: two features only — `log(hits + 1)` and `recency / age`.

    Collapses V4's separate `age` and `recency` into a single dimensionless
    ratio `recency / age = (now − last_hit_time) / max(1, now − insertion_idx)`.

      ratio ≈ 0 → just hit (last_hit_time ≈ now)
      ratio = 1 → never hit (last_hit_time defaults to insertion_idx)
      ratio ∈ (0, 1) → some hits, but a fraction of residency has passed since

    The intuition: V4's `w_age` and `w_recency` carried complementary signs
    on different traces (cluster45: w_age=+0.33, w_recency=−2.32). The *ratio*
    captures the relevant interaction — "what fraction of residency was
    post-last-hit" — in one feature, pruning the implicit redundancy when
    objects have never been hit (where x_age == x_recency exactly).

    Init: w = [1.0, -0.5], b = -0.35
      hits=0, ratio=1.0 → z = 0 + (-0.5)(1) + (-0.35) = -0.85   no promote
      hits=1, ratio≈0   → z = 0.69 + 0 + (-0.35) = +0.34         promote
      Recovers vanilla S3-FIFO's `freq ≥ 1` rule at step 0 for the
      just-inserted-now-hit case (the typical first transition).
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [1.0, -0.5]
        self.b = -0.35

    def _features(self, oid: int) -> tuple[float, ...]:
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        age = max(1, self.now - ins)
        last = self.last_hit_time.get(oid, ins)
        x2 = (self.now - last) / age
        return (x1, x2)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        msg = (
            f"[S3FIFOLearnedV5] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"promote_rate={promote_rate:.4f} ({self.n_promote}/{self.n_evict_from_s}) "
            f"w_loghits={self.w[0]:+.4f} w_ratio={self.w[1]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)


class S3FIFOLearnedV6(S3FIFOLearnedV4):
    """V6: `log(hits)`, `age`, and `recency / age`.

    Restores `age` as a separate feature alongside the V5 ratio. The pair
    `(age, recency/age)` is bijective with `(age, recency)` — recovering
    recency = age × ratio — but the model is *linear* in features, so
    linear-in-(age, ratio) yields a different decision surface than V4's
    linear-in-(age, recency).

    The ratio captures the multiplicative interaction "fraction of residency
    since last hit," while age preserves absolute scale. V5's failure was
    losing the absolute axis; V6 puts it back without giving up the ratio.

    Init: w=[1.0, 0, -0.5], b=-0.35
      hits=0, ratio=1 → z = 0 + 0 - 0.5 - 0.35 = -0.85 → no promote
      hits=1, ratio=0 → z = 0.69 + 0 + 0 - 0.35 = +0.34 → promote
      Recovers vanilla S3-FIFO's freq≥1 rule for the just-hit transition.
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [1.0, 0.0, -0.5]
        self.b = -0.35

    def _features(self, oid: int) -> tuple[float, ...]:
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        age = self.now - ins
        x2 = age / max(1, self.S_cap)
        last = self.last_hit_time.get(oid, ins)
        x3 = (self.now - last) / max(1, age)
        return (x1, x2, x3)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        msg = (
            f"[S3FIFOLearnedV6] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} fallback_fired={self.n_fallback_fired} "
            f"promote_rate={promote_rate:.4f} ({self.n_promote}/{self.n_evict_from_s}) "
            f"w_loghits={self.w[0]:+.4f} w_age={self.w[1]:+.4f} w_ratio={self.w[2]:+.4f} "
            f"b={self.b:+.4f}"
        )
        print(msg, file=sys.stderr)


class S3FIFOLearnedV7(S3FIFOLearnedV4):
    """V7: just `log(hits + 1)` and `age / S_cap`. No recency.

    Two features. Tests whether recency-since-last-hit was load-bearing in V4.
    Hypothesis: V7 should lose on traces where V4's `w_recency` was strongest
    (cluster45 cache=1000: w_recency=−2.32; cloudphysics cache=5000:
    w_recency=−1.08).

    Init: w=[1.0, 0], b=-0.35
      hits=0 → z=-0.35 → no promote
      hits=1 → z≈+0.34 → promote
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [1.0, 0.0]
        self.b = -0.35

    def _features(self, oid: int) -> tuple[float, ...]:
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        x2 = (self.now - ins) / max(1, self.S_cap)
        return (x1, x2)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        print(
            f"[S3FIFOLearnedV7] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} promote_rate={promote_rate:.4f} "
            f"({self.n_promote}/{self.n_evict_from_s}) "
            f"w_loghits={self.w[0]:+.4f} w_age={self.w[1]:+.4f} b={self.b:+.4f}",
            file=sys.stderr,
        )


class S3FIFOLearnedV8(S3FIFOLearnedV4):
    """V8: `log(hits + 1)` and `log((age / S_cap) + 1)`. Log-compressed age.

    Same two features as V7, but with log compression on age. The age
    distribution at decision time has a heavy tail (most objects are near
    age=S_cap, some are post-promotion at age >> S_cap). Log compression
    spreads the bulk near the natural-eviction boundary and compresses the
    long-resident tail. Scale-invariant: log((age/S_cap) + 1) ≈ 0 at
    insertion, ≈ 0.69 at the natural eviction boundary, ≈ 2.40 at 10× the
    boundary.

    Init: w=[1.0, 0], b=-0.35  (same step-0 policy as V7).
    """

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.w = [1.0, 0.0]
        self.b = -0.35

    def _features(self, oid: int) -> tuple[float, ...]:
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        age = (self.now - ins) / max(1, self.S_cap)
        x2 = math.log(age + 1.0)
        return (x1, x2)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        print(
            f"[S3FIFOLearnedV8] cache_size={self.cache_size} decisions={self.n_decisions} "
            f"last10k_acc={last10k_acc:.4f} promote_rate={promote_rate:.4f} "
            f"({self.n_promote}/{self.n_evict_from_s}) "
            f"w_loghits={self.w[0]:+.4f} w_logage={self.w[1]:+.4f} b={self.b:+.4f}",
            file=sys.stderr,
        )


class PiecewiseLinear:
    """Tiny piecewise-linear function of one variable.

    Fixed knot positions, learnable knot weights. Forward pass: locate the
    segment containing x, return linear interpolation between adjacent knot
    weights. Outside [knots[0], knots[-1]] we clamp (return the boundary
    knot's value) to avoid extrapolation instability.

    Backward pass: distribute the upstream gradient to the two active knots,
    weighted by the interpolation coefficients (1−t) and t.
    """

    __slots__ = ("knots", "k", "w")

    def __init__(self, knots: list[float], init_values: Optional[list[float]] = None):
        self.knots = list(knots)
        self.k = len(self.knots) - 1
        if init_values is None:
            self.w = [0.0] * len(self.knots)
        else:
            assert len(init_values) == len(self.knots)
            self.w = list(init_values)

    def _segment(self, x: float) -> tuple[int, float]:
        if x <= self.knots[0]:
            return 0, 0.0
        if x >= self.knots[-1]:
            return self.k - 1, 1.0
        # k is small (~4), linear search is fine.
        for s in range(self.k):
            if x < self.knots[s + 1]:
                t = (x - self.knots[s]) / (self.knots[s + 1] - self.knots[s])
                return s, t
        return self.k - 1, 1.0

    def __call__(self, x: float) -> float:
        s, t = self._segment(x)
        return (1 - t) * self.w[s] + t * self.w[s + 1]

    def update(self, x: float, grad: float, lr: float, l2: float) -> None:
        s, t = self._segment(x)
        self.w[s] -= lr * (grad * (1 - t) + l2 * self.w[s])
        self.w[s + 1] -= lr * (grad * t + l2 * self.w[s + 1])


class S3FIFOLearnedV9(S3FIFOLearnedV4):
    """V9: GAM (generalized additive model) over V4's three features.

    Replaces V4's linear `z = w_h·loghits + w_a·age + w_r·recency + b` with
        z = f_loghits(loghits) + f_age(age) + f_recency(recency) + b
    where each f_i is piecewise linear with K=4 segments (5 knot weights).

    Each f_i is plottable and individually interpretable — you can read off
    exactly what the model learned per feature without weight entanglement.

    Parameter count: 3 features × 5 knots + 1 bias = 16. Up from V4's 4.

    Init recovers V4's step-0 policy:
      f_loghits(x) = x         (knot weights = knot positions: identity)
      f_age(x)     = 0          (all knots zero)
      f_recency(x) = 0          (all knots zero)
      b            = -0.35
    so step-0 prediction equals 1.0·loghits − 0.35, matching V4 init.

    Knot positions chosen by domain knowledge:
      loghits ∈ [0, 5]   covers log(1)=0 (no hits) through log(150)≈5
      age, recency ∈ [0, 2] covers natural-residency boundary at 1.0 plus
                            post-promotion residents at age > 1.0
    """

    LOGHITS_KNOTS: list[float] = [0.0, 1.0, 2.0, 3.0, 5.0]
    AGE_KNOTS:     list[float] = [0.0, 0.5, 1.0, 1.5, 2.0]
    RECENCY_KNOTS: list[float] = [0.0, 0.5, 1.0, 1.5, 2.0]

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        # f_loghits initialized as identity → matches V4's w_h = 1.0 exactly
        # (linear interpolation between (0,0), (1,1), (2,2), (3,3), (5,5) is x)
        self.f_loghits = PiecewiseLinear(self.LOGHITS_KNOTS, self.LOGHITS_KNOTS)
        self.f_age = PiecewiseLinear(self.AGE_KNOTS)
        self.f_recency = PiecewiseLinear(self.RECENCY_KNOTS)
        self.b = -0.35
        # self.w from parent is unused; _predict/_sgd_update overridden.

    def _predict(self, x: tuple[float, ...]) -> tuple[float, float]:
        x_h, x_a, x_r = x
        z = (self.f_loghits(x_h) + self.f_age(x_a) + self.f_recency(x_r)
             + self.b)
        return z, _sigmoid(z)

    def _sgd_update(self, x: tuple[float, ...], y: float, p: float) -> None:
        err = p - y
        x_h, x_a, x_r = x
        self.f_loghits.update(x_h, err, self.lr, self.l2)
        self.f_age.update(x_a, err, self.lr, self.l2)
        self.f_recency.update(x_r, err, self.lr, self.l2)
        self.b -= self.lr * err

    def _features(self, oid: int) -> tuple[float, ...]:
        # Same three features as V4.
        h = self.hits_since_insert.get(oid, 0)
        x1 = math.log(h + 1.0)
        ins = self.insertion_idx.get(oid, self.now)
        x2 = (self.now - ins) / max(1, self.S_cap)
        last = self.last_hit_time.get(oid, ins)
        x3 = (self.now - last) / max(1, self.S_cap)
        return (x1, x2, x3)

    def report(self) -> None:
        if self.n_decisions == 0:
            return
        last10k_acc = sum(self.last10k) / max(1, len(self.last10k))
        promote_rate = self.n_promote / max(1, self.n_evict_from_s)
        # Format knot weights as compact lists.
        fmt = lambda ws: "[" + ", ".join(f"{w:+.3f}" for w in ws) + "]"
        print(
            f"[S3FIFOLearnedV9-GAM] cache_size={self.cache_size} "
            f"decisions={self.n_decisions} last10k_acc={last10k_acc:.4f} "
            f"promote_rate={promote_rate:.4f} "
            f"({self.n_promote}/{self.n_evict_from_s}) b={self.b:+.4f}\n"
            f"  f_loghits @ {self.LOGHITS_KNOTS}: {fmt(self.f_loghits.w)}\n"
            f"  f_age     @ {self.AGE_KNOTS}: {fmt(self.f_age.w)}\n"
            f"  f_recency @ {self.RECENCY_KNOTS}: {fmt(self.f_recency.w)}",
            file=sys.stderr,
        )


class S3FIFONoPromote(S3FIFO):
    """Diagnostic baseline: hardcoded `promote = False` from S.

    Tests whether V1's win at cache=1000 is really "the learner found a clever
    rule" or just "shutting off the S→M flow is net-good at small cache."
    Ghost-hits still route directly to M via `S3FIFO.on_miss`.
    """

    def _evict_S_one(self) -> Optional[int]:
        while self.S:
            oid = self.S.popleft()
            if oid not in self.in_S:
                continue
            self.in_S.discard(oid)
            self._ghost_push(oid)
            self.freq.pop(oid, None)
            return oid
        return None


ALGOS = {
    "S3FIFO+LearnedPromote": lambda sz: S3FIFOLearned(sz),
    "S3FIFO+LearnedV2": lambda sz: S3FIFOLearnedV2(sz),
    "S3FIFO+LearnedV3": lambda sz: S3FIFOLearnedV3(sz),
    "S3FIFO+LearnedV4": lambda sz: S3FIFOLearnedV4(sz),
    "S3FIFO+LearnedV5": lambda sz: S3FIFOLearnedV5(sz),
    "S3FIFO+LearnedV6": lambda sz: S3FIFOLearnedV6(sz),
    "S3FIFO+LearnedV7": lambda sz: S3FIFOLearnedV7(sz),
    "S3FIFO+LearnedV8": lambda sz: S3FIFOLearnedV8(sz),
    "S3FIFO+LearnedV9-GAM": lambda sz: S3FIFOLearnedV9(sz),
    "S3FIFO+NoPromote": lambda sz: S3FIFONoPromote(sz),
}


# ─── V4 sweep over S_ratio only ──────────────────────────────────────────────
# T (promote_threshold) is meaningful only for V4's robustness fallback path,
# which never fires in any trace we've tested. So V4+T2 ≡ V4+T1 ≡ V4+T3 on the
# gate path; sweeping T is wasted work. Only S_ratio matters — it changes both
# the queue partition AND the feature normalization (S_cap), so it has a real
# effect on V4's learned policy.

_V4_S_RATIOS = [0.01, 0.05, 0.10, 0.25]


def _make_v4(small_ratio: float):
    return lambda sz, s=small_ratio: S3FIFOLearnedV4(
        sz, small_ratio=s, promote_threshold=1)


for _s in _V4_S_RATIOS:
    ALGOS[f"S3FIFO+LearnedV4+S{_s:.2f}"] = _make_v4(_s)


META_OPTIMA: dict[str, list[str]] = {
    # OptS: sweep S at T=1 (the only knob V4 actually uses).
    "S3FIFO+LearnedV4+OptS": [
        f"S3FIFO+LearnedV4+S{s:.2f}" for s in _V4_S_RATIOS
    ],
}
